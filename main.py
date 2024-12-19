import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # استبدل التوكن هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=8201cdda1887b263a9985dfb298c09ae4a750407&salt=1734589043288&token=f2cd066275956f1dc5a3b20b395767fce2bbebca5f812376f4a56d242785cdc3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# متغير لتخزين نسبة البطارية السابقة
previous_battery = None

# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)


# دالة لجلب نسبة البطارية من API
def fetch_battery_percentage():
    try:
        response = requests.get(API_URL)
        if response.status_code == 200:
            data = response.json()
            parameters = data['dat']['parameter']
            battery_capacity = float(
                next(item['val'] for item in parameters
                     if item['par'] == 'bt_battery_capacity'))
            return battery_capacity
        else:
            return None
    except Exception:
        return None


# دالة /battery لعرض نسبة البطارية وبدء المراقبة
async def battery_and_monitor(update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
    global previous_battery
    chat_id = update.effective_chat.id

    # عرض نسبة البطارية
    current_battery = fetch_battery_percentage()
    if current_battery is not None:
        message = f"🔋 نسبة شحن البطارية: {current_battery:.0f}%"
        await update.message.reply_text(message)

        # بدء مراقبة البطارية
        if previous_battery is None:
            previous_battery = current_battery

        # تشغيل المهمة المتكررة
        job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in job_removed:
            job.schedule_removal()  # إيقاف المهمة القديمة إن وجدت

        context.job_queue.run_repeating(monitor_battery,
                                        interval=60,
                                        first=5,
                                        chat_id=chat_id,
                                        name=str(chat_id))

        await update.message.reply_text(
            "🔍 بدأ مراقبة البطارية. سأرسل تنبيه عند انخفاض الشحن بنسبة 1%.")
    else:
        await update.message.reply_text("⚠️ فشل في الحصول على بيانات البطارية."
                                        )


# دالة مراقبة البطارية بشكل دوري
async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global previous_battery
    job = context.job
    chat_id = job.chat_id

    # جلب نسبة البطارية الحالية
    current_battery = fetch_battery_percentage()
    if current_battery is not None:
        # مقارنة مع القيمة السابقة
        if previous_battery - current_battery >= 1:  # انخفاض 1% أو أكثر
            message = f"⚠️ تنبيه: انخفضت نسبة شحن البطارية إلى {current_battery:.0f}%"
            await context.bot.send_message(chat_id=chat_id, text=message)
            previous_battery = current_battery
        elif current_battery > previous_battery:
            previous_battery = current_battery  # تحديث عند زيادة الشحن


# دالة لإيقاف المراقبة
async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in job_removed:
        job.schedule_removal()

    await update.message.reply_text("⏹️ تم إيقاف مراقبة البطارية.")


# إعداد البوت
def main():
    # إنشاء التطبيق
    tg_app = Application.builder().token(TOKEN).build()

    # إضافة الأوامر
    tg_app.add_handler(CommandHandler(
        "battery", battery_and_monitor))  # أمر البطارية + بدء المراقبة
    tg_app.add_handler(CommandHandler("stop", stop_monitoring))  # إيقاف المراقبة

    # تشغيل خادم Flask في خيط مستقل
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # تشغيل البوت
    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()


if __name__ == "__main__":
    main()
