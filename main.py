import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=39871d56f585c92bbf62aa002b0fa04df62df3ab&salt=1737733127623&token=1d311937754818bde612b4b1b0a55e846ca02c8b9a790b04e1956ea27263b0e4&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None
previous_power = None

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
    global previous_battery, previous_voltage, previous_charging, previous_power
    chat_id = update.effective_chat.id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w = fetch_battery_data()

    if current_battery is not None:
        # تقييم استهلاك الطاقة بناءً على وجود الكهرباء
        if charging:
            power_status = "لا يوجد استهلاك على البطارية 💡"
            active_power_w = 0
        else:
            if active_power_w > 500:
                power_status = "يوجد استهلاك كبير 🛑"
            elif active_power_w > 300:
                power_status = "يوجد استهلاك متوسط ⚠️"
            elif active_power_w == 0:
                power_status = "لا يوجد استهلاك مطلقاً"
            else:
                power_status = "يوجد استهلاك قليل ✅"

        charging_status = "يوجد كهرباء ✔️ ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
        message = (
            f"🔋 نسبة شحن البطارية: {current_battery:.0f}%\n"
            f"⚡ فولت الكهرباء: {grid_voltage:.2f}V\n"
            f"🔌 حالة الشحن: {charging_status}\n"
            f"⚙️ استهلاك البطارية: {active_power_w:.0f}W - {power_status}"
        )
        await update.message.reply_text(message)

        # حفظ القيم الحالية للمراقبة
        if previous_battery is None or previous_voltage is None or previous_charging is None or previous_power is None:
            previous_battery = current_battery
            previous_voltage = grid_voltage
            previous_charging = charging
            previous_power = active_power_w

        # إعداد المهام المتكررة
        job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in job_removed:
            job.schedule_removal()

        context.job_queue.run_repeating(
            monitor_battery,
            interval=10,  # تحديث كل 10 ثوانٍ
            first=5,
            chat_id=chat_id,
            name=str(chat_id)
        )

        await update.message.reply_text("🔍 سأرسل تنبيهات لوحدي عند حدوث تغييرات, انا موجود لراحتك فلوكة 😊.")
    else:
        await update.message.reply_photo(
        photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",  # ضع مسار الصورة أو رابط URL للصورة
        caption="⚠️ فشل في الحصول على بيانات البطارية, يرجى الطلب من عمر تحديث الخدمة."
    )

# دالة مراقبة البطارية بشكل دوري
async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power
    job = context.job
    chat_id = job.chat_id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w = fetch_battery_data()

    if current_battery is not None:
        charging_status = "يوجد كهرباء 🔌 ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."

        # تحذير عند انخفاض الفولت إلى 168V أو أقل
        if grid_voltage <= 168.0 and grid_voltage != previous_voltage:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تحذير: انخفض فولت الكهرباء إلى {grid_voltage:.2f}V!"
            )
            previous_voltage = grid_voltage

        # تنبيه عند أي تغيير بنسبة 1%
        if abs(current_battery - previous_battery) >= 3:
            change = "زاد" if current_battery > previous_battery else "انخفض"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تنبيه: {change} شحن البطارية إلى {current_battery:.0f}%!"
            )
            previous_battery = current_battery

        # تنبيه إذا تغيرت حالة الشحن
        if charging != previous_charging:
            status = "⚡ عادت الكهرباء! الشحن مستمر." if charging else "⚠️ انقطعت الكهرباء! الشحن متوقف."
            await context.bot.send_message(chat_id=chat_id, text=status)
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

    tg_app.add_handler(CommandHandler("battery", battery_and_monitor))
    tg_app.add_handler(CommandHandler("stop", stop_monitoring))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
