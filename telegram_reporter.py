import sys
import asyncio
from telethon import TelegramClient
from telethon.tl.functions.messages import ReportSpamRequest
from telethon.tl.types import User, Channel, Chat, InputReportReasonOther # تم تعديل هذا السطر
import sqlite3
import time
import os # لإمكانية قراءة مفتاح التشفير إذا لزم الأمر، لكن الأفضل تمريره

async def main(user_id, target_entity, report_message, cooldown_seconds, max_reports_limit, telegram_app_api_id, telegram_app_api_hash):
    
    # وظيفة لاسترجاع حسابات Telethon من قاعدة البيانات
    def get_telethon_accounts_local(user_id):
        conn = sqlite3.connect('bot_data.db')
        cursor = conn.cursor()
        # هنا لا نحتاج api_id, api_hash من الـ DB لأننا سنستخدم تلك التي تم تمريرها كمتغيرات بيئية
        cursor.execute("SELECT phone_number FROM telethon_accounts WHERE user_id = ? AND is_active = 1", (user_id,))
        accounts = cursor.fetchall()
        conn.close()
        return [acc[0] for acc in accounts] # Return list of phone numbers

    active_accounts = get_telethon_accounts_local(user_id)
    
    if not active_accounts:
        print("لا توجد حسابات تيليجرام مفعلة لهذا المستخدم لتنفيذ البلاغات.")
        return

    print(f"بدء حملة البلاغات لـ {len(active_accounts)} حسابات...")
    total_sent_reports = 0

    for phone in active_accounts:
        if total_sent_reports >= max_reports_limit:
            print(f"تم الوصول إلى الحد الأقصى الكلي للبلاغات ({max_reports_limit}). إيقاف الحملة.")
            break

        print(f"محاولة الإبلاغ باستخدام الحساب: {phone}")
        client = TelegramClient(phone, int(telegram_app_api_id), telegram_app_api_hash)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"الحساب {phone} غير مصرح به. تخطي.")
                continue

            # Resolve target entity
            entity = None
            try:
                entity = await client.get_entity(target_entity)
            except Exception as e:
                print(f"خطأ في حل الهدف {target_entity}: {e}. تخطي هذا الحساب.")
                continue

            if isinstance(entity, User):
                input_entity = entity.input_entity
            elif isinstance(entity, (Channel, Chat)):
                # For channels/groups, you might need to convert it to a peer
                # or use messages.ReportSpamRequest which accepts Peer types
                input_entity = await client.get_input_entity(entity)
            else:
                print(f"نوع الكيان غير مدعوم: {type(entity)}. تخطي.")
                continue

            # Perform the report
            # The reason should be InputReportReasonOther as per Telethon updates
            await client(ReportSpamRequest(
                peer=input_entity,
                reason=InputReportReasonOther(), # تم تعديل هذا السطر
                message=report_message
            ))
            
            print(f"تم إرسال بلاغ بنجاح من {phone} على {target_entity}.")
            total_sent_reports += 1

        except Exception as e:
            print(f"فشل الإبلاغ من {phone}: {e}")
        finally:
            if client.is_connected():
                await client.disconnect()
        
        if cooldown_seconds > 0:
            print(f"انتظار {cooldown_seconds} ثوانٍ قبل البلاغ التالي...")
            await asyncio.sleep(cooldown_seconds)

    print(f"اكتملت حملة البلاغات. تم إرسال {total_sent_reports} بلاغاً إجمالاً.")

if __name__ == '__main__':
    # تأكد أنك تتلقى الآن 7 وسائط (اسم السكريبت + 6)
    if len(sys.argv) != 8:
        print("الاستخدام: python telegram_reporter.py <user_id> <target_entity> <report_message> <cooldown_seconds> <max_reports_limit> <telegram_app_api_id> <telegram_app_api_hash>")
        sys.exit(1)

    user_id = int(sys.argv[1])
    target_entity = sys.argv[2]
    report_message = sys.argv[3]
    cooldown_seconds = int(sys.argv[4])
    max_reports_limit = int(sys.argv[5])
    telegram_app_api_id = sys.argv[6]
    telegram_app_api_hash = sys.argv[7]

    asyncio.run(main(user_id, target_entity, report_message, cooldown_seconds, max_reports_limit, telegram_app_api_id, telegram_app_api_hash))

