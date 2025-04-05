# ============================== IMPORTS ============================== #
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading
import time

# ============================== CONFIGURATION ============================== #
try:
    from config import TELEGRAM_TOKEN, API_URL
except ImportError:
    # استخدام متغيرات بيئية إذا لم يتوفر ملف config.py (للاستضافة)
    import os
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    API_URL = os.environ.get("API_URL")
# Thresholds
BATTERY_CHANGE_THRESHOLD = 3   # Battery change percentage that triggers alert
FRIDGE_ACTIVATION_THRESHOLD = 60  # Battery percentage needed for fridge
POWER_THRESHOLDS = (300, 500)  # Power thresholds (normal, medium, high) in watts

# ============================== FLASK WEB SERVER ============================== #
flask_app = Flask(__name__)

@flask_app.route('/')
def status_check():
    """Status endpoint to check if bot is running"""
    return "✅ البوت يعمل بشكل طبيعي"

def run_flask_server():
    """Start Flask server on separate thread"""
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ============================== DATA FETCHING ============================== #
def get_system_data():
    """Get power system data from API"""
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

# ============================== TELEGRAM COMMANDS ============================== #
async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - show status and start monitoring"""
    data = get_system_data()
    
    if not data:
        await send_error_message(update)
        return
    
    await send_status_message(update, data)
    start_auto_monitoring(update, context, data)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command - stop monitoring"""
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    
    if jobs:
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("✅ تم إيقاف المراقبة التلقائية بنجاح.")
    else:
        await update.message.reply_text("❌ المراقبة التلقائية غير مفعلة حالياً.")

async def send_error_message(update: Update):
    """Send error message when data fetching fails"""
    await update.message.reply_photo(
        photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",
        caption="⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة "
    )

async def send_status_message(update: Update, data: dict):
    """Format and send current system status"""
    message = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {'موجودة ويتم الشحن✔️' if data['charging'] else 'لا يوجد كهرباء ⚠️'}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}"
    )
    await update.message.reply_text(message)

# ============================== AUTOMATIC MONITORING ============================== #
last_power_usage = None  # Global variable to track power usage for alerts

def start_auto_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    """Start automatic monitoring job"""
    chat_id = update.effective_chat.id
    # Remove any existing monitoring jobs
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    
    # Add new job to check for changes every 10 seconds
    context.job_queue.run_repeating(
        check_for_changes,
        interval=10,
        first=5,
        chat_id=chat_id,
        name=str(chat_id),
        data=initial_data
    )

async def check_for_changes(context: ContextTypes.DEFAULT_TYPE):
    """Check for important changes in system status"""
    global last_power_usage

    old_data = context.job.data
    new_data = get_system_data()

    if not new_data:
        return
    
    current_time = time.time()

    # Check power usage changes
    if new_data['power_usage'] > POWER_THRESHOLDS[1]:
        if last_power_usage is None or new_data['power_usage'] != last_power_usage:
            await send_power_alert(context, new_data['power_usage'])
            last_power_usage = new_data['power_usage']
    elif new_data['power_usage'] <= POWER_THRESHOLDS[1] and old_data['power_usage'] > POWER_THRESHOLDS[1]:
        await send_power_reduced_alert(context, new_data['power_usage'])
        last_power_usage = None

    # Check if electricity status changed
    if old_data['charging'] != new_data['charging']:
        await send_electricity_alert(context, new_data['charging'], new_data['battery'])
    
    # Check for significant battery level changes
    if abs(new_data['battery'] - old_data['battery']) >= BATTERY_CHANGE_THRESHOLD:
        await send_battery_alert(context, old_data['battery'], new_data['battery'])

    context.job.data = new_data

# ============================== ALERT MESSAGES ============================== #
async def send_power_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """Send alert for high power consumption"""
    message = f"⚠️ تحذير! استهلاك الطاقة كبير جدًا: {power_usage:.0f}W"
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_power_reduced_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """Send notification when power consumption decreases"""
    message = f"👍 تم خفض استهلاك الطاقة إلى {power_usage:.0f}W."
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_electricity_alert(context: ContextTypes.DEFAULT_TYPE, is_charging: bool, battery_level: float):
    """Send alert when electricity status changes, including battery level"""
    message = (
        f"⚡ عادت الكهرباء! الشحن جارٍ الآن.\n"
        f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
    ) if is_charging else (
        f"⚠️ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
        f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
    )
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_battery_alert(context: ContextTypes.DEFAULT_TYPE, old_value: float, new_value: float):
    """Send alert when battery percentage changes significantly"""
    arrow = "⬆️ زيادة" if new_value > old_value else "⬇️ انخفاض"
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"{arrow}\nالشحن: {old_value:.0f}% → {new_value:.0f}%"
    )

# ============================== STATUS HELPERS ============================== #
def get_charging_status(current: float) -> str:
    """Determine charging status based on current"""
    if current >= 60:
        return f"{current:.1f}A (الشحن سريع جداً 🔴)"
    elif 30 <= current < 60:
        return f"{current:.1f}A (الشحن سريع 🟡)"
    elif 1 <= current < 30:
        return f"{current:.1f}A (الشحن طبيعي 🟢)"
    return f"{current:.1f}A (لا يوجد شحن ⚪)"

def get_fridge_status(data: dict) -> str:
    """Determine fridge status and calculate remaining runtime if on battery"""
    if data['charging']:  # If electricity is available
        return "يعمل على الكهرباء ✅"
    elif data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:  # If on battery but above threshold
        if data['power_usage'] > 0:
            # Estimate runtime: assuming 1% battery = 30 Watt-hours
            battery_watt_hours = data['battery'] * 30
            # Use only 80% of battery capacity
            usable_watt_hours = battery_watt_hours * 0.8
            hours = usable_watt_hours / data['power_usage']
            return f"يعمل على البطارية ({int(hours)}h {int((hours*60)%60)}m) ⏳"
        return "يعمل على البطارية (وقت غير محدد) ⚠️"
    elif data['fridge_voltage'] > 0 and not data['charging']:
        return "يعمل على البطارية (البطارية منخفضة) ⚠️"
    return "مطفئ ⛔"

def get_consumption_status(power: float) -> str:
    """Determine power consumption level"""
    if power <= POWER_THRESHOLDS[0]:
        return "عادي 🟢"
    elif POWER_THRESHOLDS[0] < power <= POWER_THRESHOLDS[1]:
        return "متوسط 🟡"
    return "كبير 🔴"

# ============================== MAIN EXECUTION ============================== #
def main():
    """Initialize and start the bot"""
    bot = ApplicationBuilder().token(TOKEN).build()
    bot.add_handler(CommandHandler("battery", battery_command))
    bot.add_handler(CommandHandler("stop", stop_command))
    threading.Thread(target=run_flask_server).start()
    bot.run_polling()

if __name__ == "__main__":
    main()
