import os
import logging
import asyncio
import sqlite3
from cryptography.fernet import Fernet
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ConversationHandler
)
import sys
import subprocess

# تهيئة التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# الإعدادات الهامة (سيتم قراءتها من المتغيرات البيئية)
# -----------------------------------------------------------------------------

C2_BOT_TOKEN = os.environ.get('C2_BOT_TOKEN')
ADMIN_USER_ID = int(os.environ.get('ADMIN_USER_ID'))
CHANNEL_ID = int(os.environ.get('CHANNEL_ID'))
ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY').encode()
cipher_suite = Fernet(ENCRYPTION_KEY)

# Telethon API credentials (for telegram_reporter.py)
TELEGRAM_APP_API_ID = os.environ.get('TELEGRAM_APP_API_ID')
TELEGRAM_APP_API_HASH = os.environ.get('TELEGRAM_APP_API_HASH')

# -----------------------------------------------------------------------------
# حالات المحادثة
# -----------------------------------------------------------------------------

(GET_TELETHON_PHONE, GET_TELETHON_CODE, GET_TELETHON_PASSWORD, 
 SELECT_REPORT_TARGET, GET_REPORT_MESSAGE, GET_REPORT_COUNT, 
 GET_REPORT_COOLDOWN, GET_EMAIL_PROTOCOL, GET_EMAIL_HOST, GET_EMAIL_PORT, 
 GET_EMAIL_USERNAME, GET_EMAIL_PASSWORD, GET_EMAIL_IMAP_SERVER, GET_EMAIL_IMAP_PORT,
 GET_EMAIL_METHOD, GET_EMAIL_TO, GET_EMAIL_SUBJECT, GET_EMAIL_BODY, GET_EMAIL_COUNT_METHOD) = range(19)

# -----------------------------------------------------------------------------
# وظائف قاعدة البيانات
# -----------------------------------------------------------------------------

def initialize_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            language_code TEXT,
            is_bot INTEGER,
            is_premium INTEGER,
            last_activity TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telethon_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone_number TEXT UNIQUE,
            api_id INTEGER,
            api_hash TEXT,
            session_file TEXT,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email_address TEXT UNIQUE,
            password_encrypted BLOB,
            smtp_host TEXT,
            smtp_port INTEGER,
            imap_host TEXT,
            imap_port INTEGER,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

def update_user_info(user_data):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, language_code, is_bot, is_premium, last_activity)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    ''', (
        user_data['user_id'], user_data.get('username'), user_data.get('first_name'),
        user_data.get('last_name'), user_data.get('language_code'), user_data.get('is_bot'),
        user_data.get('is_premium')
    ))
    conn.commit()
    conn.close()

def add_telethon_account(user_id, phone_number, api_id, api_hash, session_file):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO telethon_accounts (user_id, phone_number, api_id, api_hash, session_file) VALUES (?, ?, ?, ?, ?)",
                       (user_id, phone_number, api_id, api_hash, session_file))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Phone number already exists
    finally:
        conn.close()

def get_telethon_accounts(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone_number, is_active FROM telethon_accounts WHERE user_id = ?", (user_id,))
    accounts = cursor.fetchall()
    conn.close()
    return accounts

def delete_telethon_account(account_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM telethon_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

def toggle_telethon_account_status(account_id, current_status):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    new_status = 1 if current_status == 0 else 0
    cursor.execute("UPDATE telethon_accounts SET is_active = ? WHERE id = ?", (new_status, account_id))
    conn.commit()
    conn.close()
    return new_status

def add_email_account(user_id, email_address, password, smtp_host, smtp_port, imap_host, imap_port):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    try:
        encrypted_password = cipher_suite.encrypt(password.encode())
        cursor.execute("INSERT INTO email_accounts (user_id, email_address, password_encrypted, smtp_host, smtp_port, imap_host, imap_port) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (user_id, email_address, encrypted_password, smtp_host, smtp_port, imap_host, imap_port))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_email_accounts(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, email_address, is_active FROM email_accounts WHERE user_id = ?", (user_id,))
    accounts = cursor.fetchall()
    conn.close()
    return accounts

def delete_email_account(account_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM email_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

def toggle_email_account_status(account_id, current_status):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    new_status = 1 if current_status == 0 else 0
    cursor.execute("UPDATE email_accounts SET is_active = ? WHERE id = ?", (new_status, account_id))
    conn.commit()
    conn.close()
    return new_status

# -----------------------------------------------------------------------------
# وظائف تيليجرام بوت
# -----------------------------------------------------------------------------

async def start(update: Update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("عذراً، هذا البوت مخصص للمشرف فقط.")
        return

    # Check if user is subscribed to the channel
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status not in ['member', 'administrator', 'creator']:
            keyboard = [[InlineKeyboardButton("اشترك في القناة", url=f"https://t.me/{await context.bot.get_chat(CHANNEL_ID).username}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "يرجى الاشتراك في القناة التالية لاستخدام البوت:",
                reply_markup=reply_markup
            )
            return
    except Exception as e:
        logger.error(f"Error checking channel subscription: {e}")
        await update.message.reply_text("حدث خطأ أثناء التحقق من الاشتراك في القناة. يرجى المحاولة لاحقاً.")
        return

    update_user_info({
        'user_id': user_id,
        'username': update.effective_user.username,
        'first_name': update.effective_user.first_name,
        'last_name': update.effective_user.last_name,
        'language_code': update.effective_user.language_code,
        'is_bot': update.effective_user.is_bot,
        'is_premium': update.effective_user.is_premium
    })

    keyboard = [
        [InlineKeyboardButton("إدارة حسابات تيليجرام", callback_data='manage_telethon')],
        [InlineKeyboardButton("إدارة حسابات الإيميل", callback_data='manage_email')],
        [InlineKeyboardButton("تنفيذ هجوم بلاغات تيليجرام", callback_data='start_telegram_report_attack')],
        [InlineKeyboardButton("تنفيذ هجوم إيميل", callback_data='start_email_attack')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('أهلاً بك في بوت القيادة والتحكم C2!\nاختر أحد الخيارات:', reply_markup=reply_markup)

async def handle_callback_query(update: Update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != ADMIN_USER_ID:
        await query.message.reply_text("عذراً، هذا البوت مخصص للمشرف فقط.")
        return

    data = query.data

    if data == 'manage_telethon':
        await manage_telethon_accounts(query, context)
    elif data.startswith('add_telethon_account'):
        await query.message.reply_text("أدخل رقم هاتف حساب تيليجرام الذي تريد إضافته (بما في ذلك رمز الدولة، مثال: +1234567890):")
        return GET_TELETHON_PHONE
    elif data.startswith('view_telethon_accounts'):
        await view_telethon_accounts(query, context)
    elif data.startswith('delete_telethon_'):
        account_id = int(data.split('_')[-1])
        delete_telethon_account(account_id)
        await query.message.reply_text(f"تم حذف حساب تيليجرام بنجاح.")
        await view_telethon_accounts(query, context) # Refresh list
    elif data.startswith('toggle_telethon_'):
        parts = data.split('_')
        account_id = int(parts[2])
        current_status = int(parts[3])
        new_status = toggle_telethon_account_status(account_id, current_status)
        status_text = "مفعل" if new_status == 1 else "معطل"
        await query.message.reply_text(f"تم تغيير حالة الحساب إلى: {status_text}")
        await view_telethon_accounts(query, context) # Refresh list
    elif data == 'manage_email':
        await manage_email_accounts(query, context)
    elif data.startswith('add_email_account'):
        await query.message.reply_text("أدخل عنوان البريد الإلكتروني الذي تريد إضافته:")
        return GET_EMAIL_PROTOCOL
    elif data.startswith('view_email_accounts'):
        await view_email_accounts(query, context)
    elif data.startswith('delete_email_'):
        account_id = int(data.split('_')[-1])
        delete_email_account(account_id)
        await query.message.reply_text(f"تم حذف حساب الإيميل بنجاح.")
        await view_email_accounts(query, context) # Refresh list
    elif data.startswith('toggle_email_'):
        parts = data.split('_')
        account_id = int(parts[2])
        current_status = int(parts[3])
        new_status = toggle_email_account_status(account_id, current_status)
        status_text = "مفعل" if new_status == 1 else "معطل"
        await query.message.reply_text(f"تم تغيير حالة الحساب إلى: {status_text}")
        await view_email_accounts(query, context) # Refresh list
    elif data == 'start_telegram_report_attack':
        await query.message.reply_text("اختر نوع الهدف للبلاغ:\n\n1. مستخدم (User ID أو Username)\n2. قناة (Channel Username أو Invite Link)\n3. مجموعة (Group Invite Link)")
        context.user_data['attack_type'] = 'telegram_report'
        return SELECT_REPORT_TARGET
    elif data == 'start_email_attack':
        await query.message.reply_text("اختر طريقة إرسال الإيميل:\n\n1. بريد إلكتروني واحد\n2. قائمة بريد إلكتروني (تتطلب رفع ملف)")
        context.user_data['attack_type'] = 'email_attack'
        keyboard = [
            [InlineKeyboardButton("بريد إلكتروني واحد", callback_data='email_method_single')],
            [InlineKeyboardButton("قائمة بريد إلكتروني (مستقبلاً)", callback_data='email_method_list_future')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("اختر طريقة الإرسال:", reply_markup=reply_markup)
        return GET_EMAIL_METHOD
    elif data == 'email_method_single':
        await query.message.reply_text("أدخل عنوان البريد الإلكتروني للمستلم:")
        context.user_data['email_count_method'] = 'single'
        return GET_EMAIL_TO
    elif data == 'email_method_list_future':
        await query.message.reply_text("هذه الميزة غير متاحة حالياً. يرجى اختيار 'بريد إلكتروني واحد'.")
        return ConversationHandler.END # End the conversation for now
    elif data == 'cancel':
        await query.message.reply_text("تم إلغاء العملية.")
        return ConversationHandler.END

    return ConversationHandler.END

async def manage_telethon_accounts(query, context):
    keyboard = [
        [InlineKeyboardButton("إضافة حساب تيليجرام جديد", callback_data='add_telethon_account')],
        [InlineKeyboardButton("عرض وإدارة الحسابات الحالية", callback_data='view_telethon_accounts')],
        [InlineKeyboardButton("عودة للقائمة الرئيسية", callback_data='start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("إدارة حسابات تيليجرام:", reply_markup=reply_markup)

async def view_telethon_accounts(query, context):
    accounts = get_telethon_accounts(query.from_user.id)
    if not accounts:
        await query.message.edit_text("لا توجد حسابات تيليجرام مضافة حالياً. يمكنك إضافة واحدة.")
        return
    
    keyboard = []
    for acc_id, phone, is_active in accounts:
        status = "مفعل" if is_active == 1 else "معطل"
        keyboard.append([
            InlineKeyboardButton(f"الرقم: {phone} (الحالة: {status})", callback_data='no_action'),
            InlineKeyboardButton("حذف", callback_data=f'delete_telethon_{acc_id}'),
            InlineKeyboardButton("تفعيل/تعطيل", callback_data=f'toggle_telethon_{acc_id}_{is_active}')
        ])
    keyboard.append([InlineKeyboardButton("عودة", callback_data='manage_telethon')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("حسابات تيليجرام المضافة:", reply_markup=reply_markup)

async def get_telethon_phone_input(update: Update, context):
    user_phone = update.message.text
    context.user_data['temp_telethon_phone'] = user_phone
    await update.message.reply_text("الآن، يرجى إدخال API ID الخاص بهذا الحساب (من my.telegram.org):")
    return GET_TELETHON_CODE

async def get_telethon_api_id_input(update: Update, context):
    try:
        api_id = int(update.message.text)
        context.user_data['temp_telethon_api_id'] = api_id
        await update.message.reply_text("الآن، يرجى إدخال API Hash الخاص بهذا الحساب (من my.telegram.org):")
        return GET_TELETHON_PASSWORD
    except ValueError:
        await update.message.reply_text("معرف API ID غير صالح. يرجى إدخال رقم صحيح:")
        return GET_TELETHON_CODE

async def get_telethon_api_hash_input(update: Update, context):
    api_hash = update.message.text
    phone_number = context.user_data.get('temp_telethon_phone')
    api_id = context.user_data.get('temp_telethon_api_id')

    if not phone_number or not api_id or not api_hash:
        await update.message.reply_text("حدث خطأ ما. يرجى إعادة البدء من /start.")
        return ConversationHandler.END

    # For simplicity, we just save them. Telethon session files would be generated on first use.
    # The session_file path should be unique or based on phone_number
    session_file = f"sessions/{phone_number}.session" # This path might not persist on free tiers
    
    if add_telethon_account(update.effective_user.id, phone_number, api_id, api_hash, session_file):
        await update.message.reply_text(f"تم إضافة حساب تيليجرام {phone_number} بنجاح.")
    else:
        await update.message.reply_text(f"حساب تيليجرام {phone_number} موجود بالفعل.")

    context.user_data.clear()
    return ConversationHandler.END

async def manage_email_accounts(query, context):
    keyboard = [
        [InlineKeyboardButton("إضافة حساب إيميل جديد", callback_data='add_email_account')],
        [InlineKeyboardButton("عرض وإدارة الحسابات الحالية", callback_data='view_email_accounts')],
        [InlineKeyboardButton("عودة للقائمة الرئيسية", callback_data='start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("إدارة حسابات الإيميل:", reply_markup=reply_markup)

async def view_email_accounts(query, context):
    accounts = get_email_accounts(query.from_user.id)
    if not accounts:
        await query.message.edit_text("لا توجد حسابات إيميل مضافة حالياً. يمكنك إضافة واحدة.")
        return
    
    keyboard = []
    for acc_id, email, is_active in accounts:
        status = "مفعل" if is_active == 1 else "معطل"
        keyboard.append([
            InlineKeyboardButton(f"الإيميل: {email} (الحالة: {status})", callback_data='no_action'),
            InlineKeyboardButton("حذف", callback_data=f'delete_email_{acc_id}'),
            InlineKeyboardButton("تفعيل/تعطيل", callback_data=f'toggle_email_{acc_id}_{is_active}')
        ])
    keyboard.append([InlineKeyboardButton("عودة", callback_data='manage_email')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("حسابات الإيميل المضافة:", reply_markup=reply_markup)

async def get_email_protocol_input(update: Update, context):
    email_address = update.message.text
    context.user_data['temp_email_address'] = email_address
    await update.message.reply_text("أدخل اسم مضيف SMTP (مثال: smtp.gmail.com):")
    return GET_EMAIL_HOST

async def get_email_host_input(update: Update, context):
    smtp_host = update.message.text
    context.user_data['temp_smtp_host'] = smtp_host
    await update.message.reply_text("أدخل منفذ SMTP (عادة 587 أو 465):")
    return GET_EMAIL_PORT

async def get_email_port_input(update: Update, context):
    try:
        smtp_port = int(update.message.text)
        context.user_data['temp_smtp_port'] = smtp_port
        await update.message.reply_text("أدخل اسم المستخدم للبريد الإلكتروني (غالباً هو نفس عنوان الإيميل):")
        return GET_EMAIL_USERNAME
    except ValueError:
        await update.message.reply_text("منفذ SMTP غير صالح. يرجى إدخال رقم صحيح:")
        return GET_EMAIL_PORT

async def get_email_username_input(update: Update, context):
    email_username = update.message.text
    context.user_data['temp_email_username'] = email_username
    await update.message.reply_text("أدخل كلمة مرور البريد الإلكتروني (أو كلمة مرور التطبيق):")
    return GET_EMAIL_PASSWORD

async def get_email_password_input(update: Update, context):
    email_password = update.message.text
    context.user_data['temp_email_password'] = email_password
    await update.message.reply_text("أدخل اسم مضيف IMAP (مثال: imap.gmail.com):")
    return GET_EMAIL_IMAP_SERVER

async def get_email_imap_server_input(update: Update, context):
    imap_host = update.message.text
    context.user_data['temp_imap_host'] = imap_host
    await update.message.reply_text("أدخل منفذ IMAP (عادة 993):")
    return GET_EMAIL_IMAP_PORT

async def get_email_imap_port_input(update: Update, context):
    try:
        imap_port = int(update.message.text)
        context.user_data['temp_imap_port'] = imap_port

        email_address = context.user_data.get('temp_email_address')
        email_password = context.user_data.get('temp_email_password')
        smtp_host = context.user_data.get('temp_smtp_host')
        smtp_port = context.user_data.get('temp_smtp_port')
        imap_host = context.user_data.get('temp_imap_host')
        
        if add_email_account(update.effective_user.id, email_address, email_password, smtp_host, smtp_port, imap_host, imap_port):
            await update.message.reply_text(f"تم إضافة حساب الإيميل {email_address} بنجاح.")
        else:
            await update.message.reply_text(f"حساب الإيميل {email_address} موجود بالفعل.")

        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("منفذ IMAP غير صالح. يرجى إدخال رقم صحيح:")
        return GET_EMAIL_IMAP_PORT

async def select_report_target_input(update: Update, context):
    target = update.message.text
    context.user_data['target_entity'] = target
    await update.message.reply_text("أدخل رسالة البلاغ (ما تريد الإبلاغ عنه):")
    return GET_REPORT_MESSAGE

async def get_report_message_input(update: Update, context):
    report_msg = update.message.text
    context.user_data['report_message'] = report_msg
    await update.message.reply_text("أدخل عدد البلاغات الكلي الذي تريد إرساله:")
    return GET_REPORT_COUNT

async def get_report_count_input(update: Update, context):
    try:
        count = int(update.message.text)
        if count <= 0:
            raise ValueError
        context.user_data['max_reports_limit'] = count
        await update.message.reply_text("أدخل فترة التهدئة بين كل بلاغ بالثواني (مثال: 60 لثانية واحدة):")
        return GET_REPORT_COOLDOWN
    except ValueError:
        await update.message.reply_text("عدد البلاغات غير صالح. يرجى إدخال رقم صحيح وموجب:")
        return GET_REPORT_COUNT

async def get_report_cooldown_input(update: Update, context):
    try:
        cooldown = int(update.message.text)
        if cooldown < 0:
            raise ValueError
        context.user_data['cooldown_seconds'] = cooldown
        
        target_entity = context.user_data.get('target_entity')
        report_message_content = context.user_data.get('report_message')
        max_reports_limit = context.user_data.get('max_reports_limit')
        
        keyboard = [
            [InlineKeyboardButton("تأكيد وبدء الهجوم", callback_data='confirm_telegram_report_attack')],
            [InlineKeyboardButton("إلغاء", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        summary = (
            f"**ملخص هجوم البلاغات:**\n"
            f"**الهدف:** `{target_entity}`\n"
            f"**رسالة البلاغ:** `{report_message_content}`\n"
            f"**الحد الأقصى للبلاغات:** `{max_reports_limit}`\n"
            f"**فترة التهدئة (ثانية):** `{cooldown}`\n\n"
            f"هل أنت متأكد من هذه الإعدادات؟"
        )
        await update.message.reply_text(summary, reply_markup=reply_markup, parse_mode='Markdown')
        return SELECT_REPORT_TARGET # Stay in this state to handle confirmation or cancel

    except ValueError:
        await update.message.reply_text("فترة التهدئة غير صالحة. يرجى إدخال رقم صحيح وموجب (أو صفر):")
        return GET_REPORT_COOLDOWN

async def confirm_and_run_report(update: Update, context):
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_telegram_report_attack':
        user_id = query.from_user.id
        target_entity = context.user_data.get('target_entity')
        report_message_content = context.user_data.get('report_message')
        max_reports_limit = context.user_data.get('max_reports_limit')
        cooldown_seconds = context.user_data.get('cooldown_seconds')

        if not TELEGRAM_APP_API_ID or not TELEGRAM_APP_API_HASH:
            await query.message.reply_text("خطأ: لم يتم تعيين TELEGRAM_APP_API_ID أو TELEGRAM_APP_API_HASH في المتغيرات البيئية.")
            return ConversationHandler.END

        await query.message.reply_text(f"بدء هجوم البلاغات على {target_entity}...")

        # Run telegram_reporter.py as a separate process
        # Pass necessary arguments to the script
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            'telegram_reporter.py',
            str(user_id),
            str(target_entity),
            report_message_content,
            str(cooldown_seconds),
            str(max_reports_limit),
            TELEGRAM_APP_API_ID,
            TELEGRAM_APP_API_HASH,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if stdout:
            await query.message.reply_text(f"تقرير من telegram_reporter:\n```\n{stdout.decode().strip()}\n```", parse_mode='Markdown')
        if stderr:
            await query.message.reply_text(f"خطأ من telegram_reporter:\n```\n{stderr.decode().strip()}\n```", parse_mode='Markdown')

        await query.message.reply_text("تم اكتمال عملية البلاغات (أو توقفت).")
        context.user_data.clear()
        return ConversationHandler.END
    elif query.data == 'cancel':
        await query.message.reply_text("تم إلغاء عملية هجوم البلاغات.")
        context.user_data.clear()
        return ConversationHandler.END
    return ConversationHandler.END

async def get_email_to_input(update: Update, context):
    email_to = update.message.text
    context.user_data['email_to_address'] = email_to
    await update.message.reply_text("أدخل موضوع البريد الإلكتروني:")
    return GET_EMAIL_SUBJECT

async def get_email_subject_input(update: Update, context):
    email_subject = update.message.text
    context.user_data['email_subject'] = email_subject
    await update.message.reply_text("أدخل نص البريد الإلكتروني:")
    return GET_EMAIL_BODY

async def get_email_body_input(update: Update, context):
    email_body = update.message.text
    context.user_data['email_body'] = email_body
    
    keyboard = [
        [InlineKeyboardButton("تأكيد وبدء هجوم الإيميل", callback_data='confirm_email_attack')],
        [InlineKeyboardButton("إلغاء", callback_data='cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    summary = (
        f"**ملخص هجوم الإيميل:**\n"
        f"**المرسل إليه:** `{context.user_data['email_to_address']}`\n"
        f"**الموضوع:** `{context.user_data['email_subject']}`\n"
        f"**النص:** `{email_body}`\n\n"
        f"هل أنت متأكد من هذه الإعدادات؟"
    )
    await update.message.reply_text(summary, reply_markup=reply_markup, parse_mode='Markdown')
    return GET_EMAIL_COUNT_METHOD # Stay in this state to handle confirmation or cancel

async def confirm_and_run_email(update: Update, context):
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_email_attack':
        user_id = query.from_user.id
        email_to = context.user_data.get('email_to_address')
        email_subject = context.user_data.get('email_subject')
        email_body = context.user_data.get('email_body')
        
        await query.message.reply_text(f"بدء إرسال الإيميلات إلى {email_to}...")

        # Run email_sender.py as a separate process
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            'email_sender.py',
            str(user_id),
            email_to,
            email_subject,
            email_body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if stdout:
            await query.message.reply_text(f"تقرير من email_sender:\n```\n{stdout.decode().strip()}\n```", parse_mode='Markdown')
        if stderr:
            await query.message.reply_text(f"خطأ من email_sender:\n```\n{stderr.decode().strip()}\n```", parse_mode='Markdown')

        await query.message.reply_text("تم اكتمال عملية إرسال الإيميلات (أو توقفت).")
        context.user_data.clear()
        return ConversationHandler.END
    elif query.data == 'cancel':
        await query.message.reply_text("تم إلغاء عملية هجوم الإيميل.")
        context.user_data.clear()
        return ConversationHandler.END
    return ConversationHandler.END

async def unknown(update: Update, context):
    await update.message.reply_text("عذراً، لا أفهم هذا الأمر. يرجى استخدام الأوامر أو الأزرار المتاحة.")

def main():
    initialize_db()

    application = Application.builder().token(C2_BOT_TOKEN).build()

    conv_handler_telethon = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern='^add_telethon_account')],
        states={
            GET_TELETHON_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telethon_phone_input)],
            GET_TELETHON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telethon_api_id_input)],
            GET_TELETHON_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telethon_api_hash_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: 'manage_telethon' # Return to manage_telethon state after completion
        }
    )

    conv_handler_email = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern='^add_email_account')],
        states={
            GET_EMAIL_PROTOCOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_protocol_input)],
            GET_EMAIL_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_host_input)],
            GET_EMAIL_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_port_input)],
            GET_EMAIL_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_username_input)],
            GET_EMAIL_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_password_input)],
            GET_EMAIL_IMAP_SERVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_imap_server_input)],
            GET_EMAIL_IMAP_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_imap_port_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        map_to_parent={
            ConversationHandler.END: 'manage_email' # Return to manage_email state after completion
        }
    )

    conv_handler_report = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern='^start_telegram_report_attack')],
        states={
            SELECT_REPORT_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_report_target_input),
                                   CallbackQueryHandler(confirm_and_run_report, pattern='^confirm_telegram_report_attack$|^cancel$')],
            GET_REPORT_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_report_message_input)],
            GET_REPORT_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_report_count_input)],
            GET_REPORT_COOLDOWN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_report_cooldown_input)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_email_attack = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern='^start_email_attack$|^email_method_single$|^email_method_list_future$')],
        states={
            GET_EMAIL_METHOD: [CallbackQueryHandler(handle_callback_query, pattern='^email_method_single$|^email_method_list_future$')],
            GET_EMAIL_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_to_input)],
            GET_EMAIL_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_subject_input)],
            GET_EMAIL_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_body_input),
                             CallbackQueryHandler(confirm_and_run_email, pattern='^confirm_email_attack$|^cancel$')],
            GET_EMAIL_COUNT_METHOD: [CallbackQueryHandler(confirm_and_run_email, pattern='^confirm_email_attack$|^cancel$')], # This state now handles confirmation
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )


    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler_telethon)
    application.add_handler(conv_handler_email)
    application.add_handler(conv_handler_report)
    application.add_handler(conv_handler_email_attack)
    application.add_handler(CallbackQueryHandler(handle_callback_query)) # For general callbacks not handled by conv handlers
    application.add_handler(MessageHandler(filters.COMMAND, unknown)) # Handles unknown commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown)) # Handles unknown text

    application.run_polling(allowed_updates=Update.ALL_TYPES)

async def cancel(update: Update, context):
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

if __name__ == '__main__':
    main()
