import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading
import logging
from functools import lru_cache
import time

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # التوكن مباشرة
API_URL = "https://web1.shinemonitor.com/public/?sign=ec9295a4d4f204a390f8e9d25e25b6d63af6b54f&salt=1738393944534&token=29645c3615e7b967bd874492186e69e1c96a103cc9e852ec97635a699e88424b&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# إعداد السجلات (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None
previous_power = None
previous_charging_current = None
previous_charging_speed = None

# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# دالة لجلب بيانات البطارية من API
@lru_cache(maxsize=1)
def fetch_battery_data(retries=3, delay=2):
    for attempt in range(retries):
        try:
            response = requests.get(API_URL)
            if response.status_code == 200:
                data = response.json()
                if data.get("err") == 0:
                    parameters = data['dat']['parameter']
                    battery_capacity = float(next(item['val'] for item in parameters if item['par'] == 'bt_battery_capacity'))
                    grid_voltage = float(next(item['val'] for item in parameters if item['par'] == 'bt_grid_voltage'))
                    active_power_kw = float(next(item['val'] for item in parameters if item['par'] == 'bt_load_active_power_sole'))
                    charging_current = float(next(item['val'] for item in parameters if item['par'] == 'bt_battery_charging_current'))
                    active_power_w = active_power_kw * 1000
                    charging = grid_voltage > 0.0
                   if    charging_current == 0:
                        charging_speed = "لا يوجد كهرباء حالياً"
                    elif  1 <= charging_current < 30:
                        charging_speed = "الشحن طبيعي"
                    elif 30 <= charging_current < 60:
                        charging_speed = "الشحن سريع"
                    else:
                        charging_speed = "الشحن سريع جداً"
                    return battery_capacity, grid_voltage, charging, active_power_w, charging_current, charging_speed
                else:
                    logger.error(f"API returned an error: {data.get('desc')}")
            else:
                logger.error(f"API request failed with status code: {response.status_code}")
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay)
    return None, None, None, None, None, None

# دالة /battery لعرض البيانات ومراقبة الشحن
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, previous_charging_current, previous_charging_speed
    chat_id = update.effective_chat.id

    try:
        current_battery, grid_voltage, charging, active_power_w, charging_current, charging_speed = fetch_battery_data()

        if current_battery is not None:
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
                f"🔋 *نسبة شحن البطارية:* {current_battery:.0f}%\n"
                f"⚡ *فولت الكهرباء:* {grid_voltage:.2f}V\n"
                f"🔌 *حالة الشحن:* {charging_status}\n"
                f"⚡ *تيار الشحن:* {charging_current:.2f}A ({charging_speed})\n"
                f"⚙️ *استهلاك البطارية:* {active_power_w:.0f}W - {power_status}"
            )
            await update.message.reply_text(message, parse_mode="Markdown")

            if previous_battery is None or previous_voltage is None or previous_charging is None or previous_power is None or previous_charging_current is None:
                previous_battery = current_battery
                previous_voltage = grid_voltage
                previous_charging = charging
                previous_power = active_power_w
                previous_charging_current = charging_current
                previous_charging_speed = charging_speed

            job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
            for job in job_removed:
                job.schedule_removal()

            context.job_queue.run_repeating(
                monitor_battery,
                interval=10,
                first=5,
                chat_id=chat_id,
                name=str(chat_id)
            )

            await update.message.reply_text("✅ تم بدء مراقبة البطارية. وسأرسل المعلومات كل تغيّر 3%.")
        else:
            await update.message.reply_photo(
                photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",
                caption="⚠️ فشل في الحصول على بيانات البطارية, يرجى الطلب من عمر تحديث الخدمة."
            )
    except Exception as e:
        logger.error(f"Error in battery_and_monitor: {e}")
        await update.message.reply_text("حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.")

# دالة مراقبة البطارية بشكل دوري
async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, previous_charging_current, previous_charging_speed
    job = context.job
    chat_id = job.chat_id

    try:
        current_battery, grid_voltage, charging, active_power_w, charging_current, charging_speed = fetch_battery_data()

        if current_battery is not None:
            charging_status = "يوجد كهرباء 🔌 ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."

            if grid_voltage <= 168.0 and grid_voltage != previous_voltage:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ تحذير: انخفض فولت الكهرباء إلى {grid_voltage:.2f}V!"
                )
                previous_voltage = grid_voltage

            if abs(current_battery - previous_battery) >= 3:
                change = "زاد" if current_battery > previous_battery else "انخفض"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ تنبيه: {change} شحن البطارية إلى {current_battery:.0f}%!"
                )
                previous_battery = current_battery

            if charging != previous_charging:
                status = "⚡ عادت الكهرباء! الشحن مستمر." if charging else "⚠️ انقطعت الكهرباء! الشحن متوقف."
                await context.bot.send_message(chat_id=chat_id, text=status)
                previous_charging = charging

            if abs(charging_current - previous_charging_current) >= 10:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ تنبيه: تيار الشحن تغير إلى {charging_current:.2f}A ({charging_speed})!"
                )
                previous_charging_current = charging_current

            if charging_speed != previous_charging_speed:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ تغيير في سرعة الشحن: {charging_speed}!"
                )
                previous_charging_speed = charging_speed
    except Exception as e:
        logger.error(f"Error in monitor_battery: {e}")

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

    logger.info("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
