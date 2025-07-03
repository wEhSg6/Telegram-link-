import sys
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sqlite3
import time
import asyncio
from cryptography.fernet import Fernet
import os

# مفتاح التشفير (يجب أن يكون هو نفسه في c2_bot_main.py)
ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY').encode()
cipher_suite = Fernet(ENCRYPTION_KEY)

def get_email_accounts_local(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT email_address, password_encrypted, smtp_host, smtp_port FROM email_accounts WHERE user_id = ? AND is_active = 1", (user_id,))
    accounts = cursor.fetchall()
    conn.close()
    
    decrypted_accounts = []
    for email_address, password_encrypted, smtp_host, smtp_port in accounts:
        try:
            decrypted_password = cipher_suite.decrypt(password_encrypted).decode()
            decrypted_accounts.append({
                'email_address': email_address,
                'password': decrypted_password,
                'smtp_host': smtp_host,
                'smtp_port': smtp_port
            })
        except Exception as e:
            print(f"خطأ في فك تشفير كلمة مرور الإيميل {email_address}: {e}")
            continue
    return decrypted_accounts

async def send_email(sender_email, sender_password, smtp_host, smtp_port, recipient_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server: # Use SMTP_SSL for port 465, or SMTP for 587 + starttls
            # If using port 587, uncomment this:
            # server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print(f"تم إرسال الإيميل بنجاح من {sender_email} إلى {recipient_email}.")
        return True
    except Exception as e:
        print(f"فشل إرسال الإيميل من {sender_email} إلى {recipient_email}: {e}")
        return False

async def main(user_id, email_to, email_subject, email_body):
    accounts = get_email_accounts_local(user_id)
    if not accounts:
        print("لا توجد حسابات إيميل مفعلة لهذا المستخدم لإرسال الإيميلات.")
        return

    print(f"بدء حملة إرسال الإيميلات إلى {email_to} باستخدام {len(accounts)} حساب.")
    total_sent = 0

    for account in accounts:
        sent_success = await send_email(
            account['email_address'],
            account['password'],
            account['smtp_host'],
            account['smtp_port'],
            email_to,
            email_subject,
            email_body
        )
        if sent_success:
            total_sent += 1
        
        # Add a small delay between sending emails from different accounts
        await asyncio.sleep(1) 

    print(f"اكتملت حملة إرسال الإيميلات. تم إرسال {total_sent} إيميل بنجاح.")

if __name__ == '__main__':
    if len(sys.argv) != 5:
        print("الاستخدام: python email_sender.py <user_id> <email_to> <email_subject> <email_body>")
        sys.exit(1)

    user_id = int(sys.argv[1])
    email_to = sys.argv[2]
    email_subject = sys.argv[3]
    email_body = sys.argv[4]

    asyncio.run(main(user_id, email_to, email_subject, email_body))

