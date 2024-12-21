import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=8201cdda1887b263a9985dfb298c09ae4a750407&salt=1734589043288&token=f2cd066275956f1dc5a3b20b395767fce2bbebca5f812376f4a56d242785cdc3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None

# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# دالة لجلب بيانات البطارية من API
def fetch_battery_data():
    try:
        response = requests.get(API_URL)
        if response.status_code == 200:
            data = response.json()
            parameters = data['dat']['parameter']
            
            # استخراج القيم المطلوبة
            battery_capacity = float(next(item['val'] for item in parameters if item['par'] == 'bt_battery_capacity'))
            grid_voltage = float(next(item['val'] for item in parameters if item['par'] == 'bt_grid_voltage'))

            # تحديد حالة الشحن
            charging = grid_voltage > 0.0  # إذا كان هناك فولت، فهو يشحن

            return battery_capacity, grid_voltage, charging
        else:
            return None, None, None
    except Exception:
        return None, None, None

# دالة /battery لعرض البيانات ومراقبة الشحن
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging
    chat_id = update.effective_chat.id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging = fetch_battery_data()

    if current_battery is not None:
        charging_status = "يوجد كهرباء 🔌 ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
        message = (
            f"🔋 نسبة شحن البطارية: {current_battery:.0f}%\n"
            f"⚡ فولت الكهرباء لمعرفة اذا كانت الكهرباء قوية ام لا (يجب ان تكون V170 فأكثر) : {grid_voltage:.2f}V\n"
            f"🔌 حالة الشحن: {charging_status}"
        )
        await update.message.reply_text(message)

        # حفظ القيم الحالية للمراقبة
        if previous_battery is None or previous_voltage is None or previous_charging is None:
            previous_battery = current_battery
            previous_voltage = grid_voltage
            previous_charging = charging

        # إعداد المهام المتكررة
        job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in job_removed:
            job.schedule_removal()  # إيقاف المهام القديمة إن وجدت

        context.job_queue.run_repeating(
            monitor_battery,
            interval=60,
            first=5,
            chat_id=chat_id,
            name=str(chat_id)
        )

        await update.message.reply_text(
            "🔍 بدأ مراقبة البطارية والفولت. سأرسل تنبيهات عند حدوث تغييرات."
        )
    else:
        await update.message.reply_text("⚠️ فشل في الحصول على بيانات البطارية.")

# دالة مراقبة البطارية بشكل دوري
async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging
    job = context.job
    chat_id = job.chat_id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging = fetch_battery_data()

    if current_battery is not None:
        charging_status = "يوجد كهرباء 🔌 ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."

        # تحذير عند انخفاض الفولت إلى 168V أو أقل
        if grid_voltage <= 168.0 and grid_voltage != previous_voltage:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تحذير: انخفض فولت الكهرباء إلى {grid_voltage:.2f}V!"
            )

        # تنبيه عند نقصان البطارية 1%
        if current_battery < previous_battery - 1:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تنبيه: انخفضت نسبة شحن البطارية إلى {current_battery:.0f}%!"
            )
            previous_battery = current_battery

        # تنبيه عند زيادة البطارية 3%
        if current_battery >= previous_battery + 3:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ شحن البطارية زاد إلى {current_battery:.0f}%!"
            )
            previous_battery = current_battery

        # تنبيه إذا توقفت الكهرباء وتوقف الشحن
        if charging != previous_charging:
            if not charging:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ انقطعت الكهرباء! توقف الشحن عند {current_battery:.0f}%."
                )
            previous_charging = charging

# دالة لإيقاف المراقبة
async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in job_removed:
        job.schedule_removal()

    await update.message.reply_text("⏹️ تم إيقاف مراقبة البطارية.")

# إعداد البوت
def main():
    tg_app = ApplicationBuilder().token(TOKEN).build()
    job_queue = tg_app.job_queue
    job_queue.start()

    # إضافة الأوامر
    tg_app.add_handler(CommandHandler("battery", battery_and_monitor))
    tg_app.add_handler(CommandHandler("stop", stop_monitoring))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
