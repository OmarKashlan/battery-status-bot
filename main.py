# ============================================
# 1. إعداد السجلات
# ============================================
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading
import logging
import time

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# 2. إعدادات البوت و API
# ============================================
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # التوكن مباشرة
API_URL = "https://web1.shinemonitor.com/public/?sign=b3729511f4f2938474571eb8b9b8a3ad0cbde922&salt=1738677949971&token=51b1ed7a085b7bbbcc185a7a7884ae79555058e4aefd91247f0864059eb95485&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None
previous_power = None
previous_charging_current = None
previous_charging_speed = None

# ============================================
# 3. إعداد خادم Flask
# ============================================
# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    try:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)), use_reloader=False)
    except Exception as e:
        logger.error(f"Error running Flask server: {e}")

# ============================================
# 4. جلب بيانات البطارية من الـ API
# ============================================
def fetch_battery_data(retries=3, delay=2):
    for attempt in range(retries):
        try:
            response = requests.get(API_URL, timeout=10)  # تعيين حد زمني للطلب (10 ثوانٍ)
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

                    if charging_current == 0:
                        charging_speed = "لا يوجد كهرباء حالياً"
                    elif 1 <= charging_current < 30:
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
    
    # فشل في كل المحاولات
    logger.error("Failed to fetch battery data after multiple attempts.")
    return None, None, None, None, None, None

# ============================================
# 5. معالجة طلبات البوت (البطارية والمراقبة)
# ============================================
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, previous_charging_current, previous_charging_speed
    chat_id = update.effective_chat.id

    try:
        current_battery, grid_voltage, charging, active_power_w, charging_current, charging_speed = fetch_battery_data()

        if current_battery is not None:
            charging_status = "يوجد كهرباء ✔️ ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
            power_status = (
                "لا يوجد استهلاك على البطارية 💡" if charging
                else "يوجد استهلاك كبير 🛑" if active_power_w > 500
                else "يوجد استهلاك متوسط ⚠️" if active_power_w > 300
                else "لا يوجد استهلاك مطلقاً" if active_power_w == 0
                else "يوجد استهلاك قليل ✅"
            )

            message = (
                f"🔋 *نسبة شحن البطارية:* {current_battery:.0f}%\n"
                f"⚡ *فولت الكهرباء:* {grid_voltage:.2f}V\n"
                f"🔌 *حالة الشحن:* {charging_status}\n"
                f"⚡ *تيار الشحن:* {charging_current:.2f}A ({charging_speed})\n"
                f"⚙️ *استهلاك البطارية:* {active_power_w:.0f}W - {power_status}"
            )
            await update.message.reply_text(message, parse_mode="Markdown")

            # حفظ القيم السابقة
            previous_battery = current_battery
            previous_voltage = grid_voltage
            previous_charging = charging
            previous_power = active_power_w
            previous_charging_current = charging_current
            previous_charging_speed = charging_speed

            # جدولة مراقبة البطارية
            context.job_queue.run_repeating(
                monitor_battery,
                interval=10,
                first=5,
                chat_id=chat_id,
                name=str(chat_id)
            )
        else:
            logger.error("Failed to fetch battery data.")
            await update.message.reply_photo(
                photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",
                caption="⚠️ فشل في الحصول على بيانات البطارية بسبب انتهاء فترة المعلومات (تعبت من الاخر يعني), فـ يرجى الطلب من عمر تحديث الخدمة."
            )
    except Exception as e:
        logger.error(f"Error in battery_and_monitor: {e}")
        await update.message.reply_text("حدث خطأ غير متوقع. يرجى اخبار عمر بالمشكلة فوراً.")

# ============================================
# 6. مراقبة البطارية باستمرار
# ============================================
# بدلاً من last_sent_message, خزّن آخر قيمة للبطارية
last_sent_battery = None

async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global last_sent_time, last_sent_battery
    job = context.job
    chat_id = job.chat_id

    try:
        # جلب البيانات من الـ API
        current_battery, grid_voltage, charging, active_power_w, charging_current, charging_speed = fetch_battery_data()

        if current_battery is not None:
            current_time = time.time()

            if last_sent_time is None or current_time - last_sent_time > message_delay:
                if abs(current_battery - last_sent_battery) >= 3:
                    change = "زاد" if current_battery > last_sent_battery else "انخفض"
                    message = f"⚠️ تنبيه: {change} شحن البطارية إلى {current_battery:.0f}%!"
                    
                    # إرسال الرسالة
                    await context.bot.send_message(chat_id=chat_id, text=message)
                    
                    # تحديث القيم
                    last_sent_battery = current_battery
                    last_sent_time = current_time
    except Exception as e:
        logger.error(f"Error in monitor_battery: {e}")
# ============================================
# 7. تشغيل البوت
# ============================================
def main():
    tg_app = ApplicationBuilder().token(TOKEN).build()
    tg_app.add_handler(CommandHandler("battery", battery_and_monitor))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    logger.info("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
