import os
import requests
import time
import hashlib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=8201cdda1887b263a9985dfb298c09ae4a750407&salt=1734589043288&token=f2cd066275956f1dc5a3b20b395767fce2bbebca5f812376f4a56d242785cdc3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"
BASE_URL = "https://web1.shinemonitor.com/public/"
TOKEN_API = "8f46000a563f0e3cc0c998ac46ca5cf11eab7e372f3b472abc7a5c0ea03c00e7"

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

# دالة لإنشاء URL ديناميكي لجلب البيانات
def generate_buzzer_url(action):
    try:
        # إعداد القيم الأساسية
        salt = str(int(time.time() * 1000))  # الوقت الحالي بالميللي ثانية
        raw_sign = f"{TOKEN_API}{salt}{action}std_buzzer_ctrl_a1W0040157841922963224075040372451"
        sign = hashlib.sha1(raw_sign.encode('utf-8')).hexdigest()  # توليد التوقيع

        params = {
            "action": action,
            "source": "1",
            "pn": "W0040157841922",
            "sn": "96322407504037",
            "devcode": "2451",
            "devaddr": "1",
            "id": "std_buzzer_ctrl_a",
            "i18n": "en_US",
            "salt": salt,
            "token": TOKEN_API,
            "sign": sign
        }
        response = requests.get(BASE_URL, params=params)
        print("Generated URL:", response.url)  # لعرض الرابط في السجلات
        return response.url
    except Exception as e:
        print(f"Error generating URL: {e}")
        return None

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

# دالة للتحقق من حالة الطنين
def check_buzzer_status():
    try:
        dynamic_url = generate_buzzer_url("queryDeviceCtrlValue")
        if not dynamic_url:
            return None

        response = requests.get(dynamic_url)
        print(f"Response: {response.status_code}, {response.text}")  # عرض الاستجابة
        if response.status_code == 200:
            data = response.json()
            if 'dat' in data and 'val' in data['dat']:
                return data['dat']['val']
            else:
                print("⚠️ البيانات غير متوقعة أو مفقودة.")
                return None
        else:
            print(f"⚠️ API Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching buzzer status: {e}")
        return None

# دالة لتغيير حالة الطنين
def set_buzzer_status(enable):
    try:
        val = "Enable" if enable else "Disable"
        dynamic_url = generate_buzzer_url("setDeviceCtrlValue")
        if not dynamic_url:
            return False

        response = requests.post(dynamic_url, params={"val": val})
        return response.status_code == 200
    except Exception as e:
        print(f"Error setting buzzer status: {e}")
        return False

# دالة /battery لعرض البيانات ومراقبة الشحن
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power
    chat_id = update.effective_chat.id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w = fetch_battery_data()

    if current_battery is not None:
        if charging:
            power_status = "لا يوجد استهلاك على البطارية 💡"
            active_power_w = 0
        else:
            if active_power_w > 500:
                power_status = "يوجد استهلاك كبير 🔥"
            elif active_power_w > 300:
                power_status = "يوجد استهلاك متوسط ⚡"
            else:
                power_status = "يوجد استهلاك قليل 💡"

        charging_status = "يوجد كهرباء ✔️ ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
        message = (
            f"🔋 نسبة شحن البطارية: {current_battery:.0f}%\n"
            f"⚡ فولت الكهرباء: {grid_voltage:.2f}V\n"
            f"🔌 حالة الشحن: {charging_status}\n"
            f"⚙️ استهلاك البطارية: {active_power_w:.0f}W - {power_status}"
        )
        await update.message.reply_text(message)

async def buzzer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_buzzer_status()
    if status is None:
        await update.message.reply_text("⚠️ تعذر الحصول على حالة الطنين.")
        return

    status_text = "مُفعل 🔊" if status == "Enable" else "متوقف 🔕"

    keyboard = [
        [InlineKeyboardButton("تشغيل 🔊", callback_data="enable_buzzer")],
        [InlineKeyboardButton("إيقاف 🔕", callback_data="disable_buzzer")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🔔 حالة الطنين الحالية: {status_text}\nاختر أحد الخيارات أدناه:",
        reply_markup=reply_markup
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "enable_buzzer":
        success = set_buzzer_status(True)
        message = "تم تفعيل الطنين بنجاح! 🔊" if success else "⚠️ فشل في تفعيل الطنين."
    elif query.data == "disable_buzzer":
        success = set_buzzer_status(False)
        message = "تم إيقاف الطنين بنجاح! 🔕" if success else "⚠️ فشل في إيقاف الطنين."

    await query.edit_message_text(text=message)

# إعداد البوت
def main():
    tg_app = ApplicationBuilder().token(TOKEN).build()
    tg_app.add_handler(CommandHandler("battery", battery_and_monitor))
    tg_app.add_handler(CommandHandler("buzzer", buzzer))
    tg_app.add_handler(CallbackQueryHandler(button))
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
