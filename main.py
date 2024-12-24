import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, JobQueue
from flask import Flask
import threading
import datetime

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=8201cdda1887b263a9985dfb298c09ae4a750407&salt=1734589043288&token=f2cd066275956f1dc5a3b20b395767fce2bbebca5f812376f4a56d242785cdc3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None
previous_power = None
last_charging_time = None

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
            active_power_kw = float(next(item['val'] for item in parameters if item['par'] == 'bt_load_active_power_sole'))

            # تحويل الطاقة إلى W
            active_power_w = active_power_kw * 1000

            # تحديد حالة الشحن
            charging = grid_voltage > 0.0

            return battery_capacity, grid_voltage, charging, active_power_w
        else:
            return None, None, None, None
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None, None, None, None

# دالة /battery لعرض البيانات ومراقبة الشحن
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, last_charging_time
    chat_id = update.effective_chat.id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w = fetch_battery_data()

    if current_battery is not None:
        # تحديث وقت آخر شحن
        if charging:
            last_charging_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # تقييم استهلاك الطاقة
        if charging:
            power_status = "لا يوجد استهلاك على البطارية 💡"
            active_power_w = 0
        else:
            if active_power_w > 500:
                power_status = "يوجد استهلاك كبير 🛑"
            elif active_power_w > 300:
                power_status = "يوجد استهلاك متوسط ⚠️"
            else:
                power_status = "يوجد استهلاك قليل ✅"

        charging_status = "يوجد كهرباء ✔️ ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
        message = (
            f"🔋 نسبة شحن البطارية: {current_battery:.0f}%\n"
            f"⚡ فولت الكهرباء: {grid_voltage:.2f}V\n"
            f"🔌 حالة الشحن: {charging_status}\n"
            f"⚙️ استهلاك البطارية: {active_power_w:.0f}W - {power_status}"
        )

        # إضافة زر حالة الكهرباء
        keyboard = [[InlineKeyboardButton("حالة الكهرباء", callback_data='check_status')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(message, reply_markup=reply_markup)

        # إعداد المراقبة الدورية
        context.job_queue.run_repeating(
            monitor_battery,
            interval=10,
            first=5,
            chat_id=chat_id,
            name=str(chat_id)
        )

        await update.message.reply_text("🔍 سأرسل تنبيهات لوحدي عند حدوث تغييرات, انا موجود لراحتك 😊.")
    else:
        await update.message.reply_text("⚠️ فشل في الحصول على بيانات البطارية.")

# دالة زر حالة الكهرباء
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if last_charging_time:
        await query.edit_message_text(text=f"📅 آخر وقت تم فيه الشحن: {last_charging_time}")
    else:
        await query.edit_message_text(text="❌ لم يتم تسجيل وقت شحن سابق.")

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

    tg_app.add_handler(CommandHandler("battery", battery_and_monitor))
    tg_app.add_handler(CommandHandler("stop", stop_monitoring))
    tg_app.add_handler(CallbackQueryHandler(button_callback))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
