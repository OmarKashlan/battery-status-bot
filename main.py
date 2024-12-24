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
            "token": TOKEN_API
        }

        # توليد سلسلة البيانات للتوقيع حسب الحقول المطلوبة
        raw_sign = f"{TOKEN_API}{salt}{action}{params['id']}{params['pn']}{params['sn']}{params['devcode']}{params['devaddr']}"
        sign = hashlib.sha1(raw_sign.encode('utf-8')).hexdigest()

        # إضافة التوقيع إلى المعاملات
        params["sign"] = sign
        return params
    except Exception as e:
        print(f"Error generating URL: {e}")
        return None

# دالة للتحقق من حالة الطنين
def check_buzzer_status():
    try:
        params = generate_buzzer_url("queryDeviceCtrlValue")
        response = requests.get(BASE_URL, params=params)
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
        params = generate_buzzer_url("setDeviceCtrlValue")
        response = requests.post(BASE_URL, params=params, json={"val": val})
        print(f"Set Buzzer Response: {response.status_code}, {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error setting buzzer status: {e}")
        return False

# دالة /buzzer للتحكم في الطنين
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
    tg_app.add_handler(CommandHandler("buzzer", buzzer))
    tg_app.add_handler(CallbackQueryHandler(button))
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    print("✅ البوت يعمل الآن...")
    tg_app.run_polling()

if __name__ == "__main__":
    main()
