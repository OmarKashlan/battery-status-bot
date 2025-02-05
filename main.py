import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# ============================ إعدادات البوت الأساسية ============================ #
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"
API_URL = "https://web1.shinemonitor.com/public/?sign=e35fa2a0e99e6d7d3b4e8656cb385335a19769e7&salt=1738750708040&token=4ac3905ae2ff1552a9fb7cdf3b13c1a1a2c0211dd7549860284355099d3a17f3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# ============================ إعدادات المراقبة ============================ #
BATTERY_CHANGE_THRESHOLD = 3
FRIDGE_ACTIVATION_THRESHOLD = 60
POWER_THRESHOLDS = (300, 500)  # (عادي، متوسط، كبير)

# ============================ خدمة Flask الخلفية ============================ #
flask_app = Flask(__name__)

@flask_app.route('/')
def status_check():
    return "✅ البوت يعمل بشكل طبيعي"

def run_flask_server():
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ============================ نظام جلب البيانات ============================ #
def get_system_data():
    try:
        response = requests.get(API_URL)
        if response.status_code == 200:
            data = response.json()
            params = {item['par']: item['val'] for item in data['dat']['parameter']}
            
            return {
                'battery': float(params['bt_battery_capacity']),
                'voltage': float(params['bt_grid_voltage']),
                'charging': float(params['bt_grid_voltage']) > 0,
                'power_usage': float(params['bt_load_active_power_sole']) * 1000,
                'fridge_voltage': float(params['bt_ac2_output_voltage']),
                'charge_current': float(params.get('bt_battery_charging_current', 0))
            }
        return None
    except Exception as e:
        print(f"خطأ في الاتصال: {str(e)}")
        return None

# ============================ أوامر التليجرام ============================ #
async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_system_data()
    
    if not data:
        await send_error_message(update)
        return
    
    await send_status_message(update, data)
    start_auto_monitoring(update, context, data)

async def send_error_message(update: Update):
    await update.message.reply_photo(
        photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",
        caption="⚠️ تعذر الحصول على البيانات لانه تعبت وانا عم جبلك البيانات ولله ، الرجاء الطلب من عمورة تحديث الخدمة "
    )

async def send_status_message(update: Update, data: dict):
    message = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {'موجودة ويتم الشحن✔️' if data['charging'] else 'لا يوجد كهرباء ⚠️'}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}"
    )
    await update.message.reply_text(message)

# ============================ نظام المراقبة التلقائية ============================ #
def start_auto_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    
    context.job_queue.run_repeating(
        check_for_changes,
        interval=10,
        first=5,
        chat_id=chat_id,
        name=str(chat_id),
        data=initial_data
    )

async def check_for_changes(context: ContextTypes.DEFAULT_TYPE):
    old_data = context.job.data
    new_data = get_system_data()
    
    if not new_data:
        return
    
    if old_data['charging'] != new_data['charging']:
        await send_electricity_alert(context, new_data['charging'])
    
    if abs(new_data['battery'] - old_data['battery']) >= BATTERY_CHANGE_THRESHOLD:
        await send_battery_alert(context, old_data['battery'], new_data['battery'])
    
    context.job.data = new_data

async def send_electricity_alert(context: ContextTypes.DEFAULT_TYPE, is_charging: bool):
    message = "⚡ عادت الكهرباء! الشحن جارٍ الآن." if is_charging else "⚠️ انقطعت الكهرباء! يتم التشغيل على البطارية."
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_battery_alert(context: ContextTypes.DEFAULT_TYPE, old_value: float, new_value: float):
    arrow = "⬆️ زيادة" if new_value > old_value else "⬇️ انخفاض"
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"{arrow}\nالشحن: {old_value:.0f}% → {new_value:.0f}%"
    )

# ============================ دوال مساعدة ============================ #
def get_charging_status(current: float) -> str:
    if current >= 60:
        return f"{current:.1f}A (الشحن سريع جداً 🔴)"
    elif 30 <= current < 60:
        return f"{current:.1f}A (الشحن سريع 🟡)"
    elif 1 <= current < 30:
        return f"{current:.1f}A (الشحن طبيعي 🟢)"
    return f"{current:.1f}A (لا يوجد شحن ⚪)"

def get_fridge_status(data: dict) -> str:
    if data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:
        return "يعمل ✅"
    elif data['battery'] < FRIDGE_ACTIVATION_THRESHOLD and data['fridge_voltage'] > 0 and not data['charging']:
        if data['power_usage'] > 0:
            hours = (data['battery'] * 0.8 * 1000) / data['power_usage']
            return f"يعمل ({int(hours)}h {int((hours*60)%60)}m) ⏳"
        return "يعمل (وقت غير محدد) ⚠️"
    return "مطفئ ⛔"

def get_consumption_status(power: float) -> str:
    if power <= POWER_THRESHOLDS[0]:
        return "عادي 🟢"
    elif POWER_THRESHOLDS[0] < power <= POWER_THRESHOLDS[1]:
        return "متوسط 🟡"
    return "كبير 🔴"

# ============================ التشغيل الرئيسي ============================ #
def main():
    bot = ApplicationBuilder().token(TOKEN).build()
    bot.add_handler(CommandHandler("battery", battery_command))
    threading.Thread(target=run_flask_server).start()
    bot.run_polling()

if __name__ == "__main__":
    main()
