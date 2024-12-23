import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "<7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M>"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=8201cdda1887b263a9985dfb298c09ae4a750407&salt=1734589043288&token=f2cd066275956f1dc5a3b20b395767fce2bbebca5f812376f4a56d242785cdc3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"
BUZZER_API_URL = "https://web1.shinemonitor.com/public/"

# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# دالة للتحقق من حالة الطنين
def check_buzzer_status():
    try:
        response = requests.get(BUZZER_API_URL, params={
            "action": "queryDeviceCtrlValue",
            "id": "std_buzzer_ctrl_a",
            "source": "1",
            "devcode": "2451",
            "pn": "W0040157841922",
            "devaddr": "1",
            "sn": "96322407504037",
            "i18n": "en_US"
        })
        if response.status_code == 200:
            data = response.json()
            return data['dat']['val']
        else:
            return None
    except Exception as e:
        print(f"Error fetching buzzer status: {e}")
        return None

# دالة لتغيير حالة الطنين
def set_buzzer_status(enable):
    try:
        val = "Enable" if enable else "Disable"
        response = requests.post(BUZZER_API_URL, params={
            "action": "setDeviceCtrlValue",
            "id": "std_buzzer_ctrl_a",
            "val": val,
            "source": "1",
            "devcode": "2451",
            "pn": "W0040157841922",
            "devaddr": "1",
            "sn": "96322407504037",
            "i18n": "en_US"
        })
        return response.status_code == 200
    except Exception as e:
        print(f"Error setting buzzer status: {e}")
        return False

# دالة للتعامل مع زر /buzzer
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

# دالة للتعامل مع الأزرار
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
