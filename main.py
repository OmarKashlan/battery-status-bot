# ============================== IMPORTS ============================== #
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading
import time
import datetime
import pytz  # إضافة مكتبة pytz

# ============================== CONFIGURATION ============================== #
try:
    from config import TELEGRAM_TOKEN as TOKEN, API_URL
except ImportError:
    import os
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    API_URL = os.environ.get("API_URL")

# Set timezone to Damascus (Syria)
TIMEZONE = pytz.timezone('Asia/Damascus')

# Thresholds
BATTERY_CHANGE_THRESHOLD = 3   # Battery change percentage that triggers alert
FRIDGE_ACTIVATION_THRESHOLD = 60  # Battery percentage needed for fridge
FRIDGE_WARNING_THRESHOLD = 63     # Battery percentage to warn about fridge shutdown
POWER_THRESHOLDS = (300, 500)  # Power thresholds (normal, medium, high) in watts
API_CHECK_INTERVAL = 300  # Check API every 5 minutes (300 seconds)

# Global variables
last_power_usage = None  # To track power usage for alerts
last_electricity_time = None  # To track when electricity was last available
electricity_start_time = None  # To track when electricity started
api_failure_notified = False  # To track if we've already notified about API failure
fridge_warning_sent = False  # To track if fridge warning has been sent
admin_chat_id = None  # To store admin's chat ID for notifications

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
    global last_electricity_time, electricity_start_time
    
    try:
        response = requests.get(API_URL, timeout=10)
        if response.status_code == 200:
            data = response.json()
            params = {item['par']: item['val'] for item in data['dat']['parameter']}
            
            # Create the data dictionary
system_data = {
                'battery': float(params['bt_battery_capacity']),
                'voltage': float(params['bt_grid_voltage']),
                'charging': float(params['bt_grid_voltage']) > 0,
                'power_usage': float(params['bt_load_active_power_sole']) * 1000,
                'fridge_voltage': float(params['bt_ac2_output_voltage']),
                'charge_current': float(params.get('bt_battery_charging_current', 0))
            }
            
            # Update electricity tracking with correct timezone
            if system_data['charging']:
                if electricity_start_time is None:
                    electricity_start_time = datetime.datetime.now(TIMEZONE)
                last_electricity_time = datetime.datetime.now(TIMEZONE)
            else:
                electricity_start_time = None
                
            return system_data
        return None
    except Exception as e:
        print(f"خطأ في الاتصال: {str(e)}")
        return None

# ============================== API HEALTH CHECK ============================== #
async def check_api_health(context: ContextTypes.DEFAULT_TYPE):
    """Periodically check if the API is working and notify if it fails"""
    global api_failure_notified, admin_chat_id
    
    # Skip if we don't have an admin chat ID to send notifications to
    if not admin_chat_id:
        return
    
    data = get_system_data()
    
    if not data:
        # API is not working, send notification if we haven't already
        if not api_failure_notified:
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text="⚠️ تنبيه تلقائي: تعذر الاتصال بال API. يرجى تحديث الخدمة وتغيير عنوان API."
            )
            api_failure_notified = True
    else:
        # API is working again after a failure
        if api_failure_notified:
            await context.bot.send_message(
                chat_id=admin_chat_id,
                text="✅ تم استعادة الاتصال بال API بنجاح! البوت يعمل مرة أخرى."
            )
            api_failure_notified = False

# ============================== TELEGRAM COMMANDS ============================== #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - initialize the bot and save admin chat ID"""
    global admin_chat_id
    
    # Save the user's chat ID as admin
    admin_chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "مرحباً بك في بوت مراقبة نظام الطاقة! 🔋\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة التلقائية\n"
        "/stop - إيقاف المراقبة التلقائية\n\n"
        "سيتم إرسال إشعار تلقائي عند فشل الاتصال بال API."
    )
    
    # Start the API health check job if not already running
    start_api_health_check(context)

async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - show status and start monitoring"""
    global admin_chat_id
    
    # Save the user's chat ID as admin
    admin_chat_id = update.effective_chat.id
    
    data = get_system_data()
    
    if not data:
        await send_error_message(update)
        return
    
    await send_status_message(update, data)
    start_auto_monitoring(update, context, data)
    
    # Start the API health check job if not already running
    start_api_health_check(context)

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
    global last_electricity_time
    
    # Format the electricity time string with 12-hour format
    if data['charging']:
        electricity_status = "موجودة ويتم الشحن✔️"
        electricity_time_str = "الكهرباء متوفرة حالياً"
    else:
        electricity_status = "لا يوجد كهرباء ⚠️"
        electricity_time_str = f"{last_electricity_time.strftime('%I:%M:%S %p')}" if last_electricity_time else "غير معلوم 🤷"
    
    message = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {electricity_status}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}\n"
        f"⏱️ اخر توقيت لوجود الكهرباء: {electricity_time_str}"
    )
    await update.message.reply_text(message)

# ============================== AUTOMATIC MONITORING ============================== #
def start_api_health_check(context: ContextTypes.DEFAULT_TYPE):
    """Start API health check job"""
    # Check if the job is already running
    jobs = context.job_queue.get_jobs_by_name("api_health_check")
    if not jobs:
        # Add new job to check API health every API_CHECK_INTERVAL seconds
        context.job_queue.run_repeating(
            check_api_health,
            interval=API_CHECK_INTERVAL,
            first=10,  # First check after 10 seconds
            name="api_health_check"
        )

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
    global last_power_usage, fridge_warning_sent

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
        # Reset fridge warning when electricity comes back
        if new_data['charging']:
            fridge_warning_sent = False
    
    # Check for fridge warning (battery at 63% and no electricity)
    if (not new_data['charging'] and 
        new_data['battery'] <= FRIDGE_WARNING_THRESHOLD and 
        new_data['battery'] > FRIDGE_ACTIVATION_THRESHOLD and 
        not fridge_warning_sent):
        await send_fridge_warning_alert(context, new_data['battery'])
        fridge_warning_sent = True
    
    # Reset fridge warning if battery goes above warning threshold or below activation threshold
    if (new_data['battery'] > FRIDGE_WARNING_THRESHOLD or 
        new_data['battery'] <= FRIDGE_ACTIVATION_THRESHOLD):
        fridge_warning_sent = False
    
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
    global last_electricity_time, electricity_start_time
    
    current_time = datetime.datetime.now(TIMEZONE)
    
    if is_charging:
        # Update tracking variables
        electricity_start_time = current_time
        last_electricity_time = current_time
        
        message = (
            f"⚡ عادت الكهرباء! الشحن جارٍ الآن.\n"
            f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
        )
    else:
        # Record the last time electricity was available
        if electricity_start_time is not None:
            last_electricity_time = current_time
        
        message = (
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

async def send_fridge_warning_alert(context: ContextTypes.DEFAULT_TYPE, battery_level: float):
    """Send warning when battery is close to fridge shutdown threshold"""
    remaining_percentage = battery_level - FRIDGE_ACTIVATION_THRESHOLD
    message = (
        f"🧊⚠️ تنبيه البراد!\n"
        f"البطارية حالياً: {battery_level:.0f}%\n"
        f"متبقي {remaining_percentage:.0f}% فقط لينطفئ البراد عند الوصول لـ {FRIDGE_ACTIVATION_THRESHOLD}%"
    )
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

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
    """Determine fridge status"""
    if data['charging']:  # If electricity is available
        return "يعمل على الكهرباء ⚡"
    elif data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:  # If on battery but above threshold
        return "يعمل على البطارية 🔋"
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

# ============================== API URL UPDATE COMMAND ============================== #
async def update_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update_api command - update the API URL"""
    global API_URL, api_failure_notified
    
    # Check if a URL was provided
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ يرجى توفير عنوان API الجديد بعد الأمر.\n"
            "مثال: /update_api https://example.com/api/new_url"
        )
        return
    
    # Update the API URL
    new_url = context.args[0]
    API_URL = new_url
    api_failure_notified = False  # Reset notification status
    
    # Test the new URL
    data = get_system_data()
    if data:
        await update.message.reply_text(f"✅ تم تحديث عنوان API بنجاح وتم التحقق من صحته!")
    else:
        await update.message.reply_text(
            f"⚠️ تم تحديث عنوان API، ولكن يبدو أنه لا يعمل بشكل صحيح.\n"
            f"يرجى التحقق من العنوان والمحاولة مرة أخرى."
        )

# ============================== MAIN EXECUTION ============================== #
def main():
    """Initialize and start the bot"""
    bot = ApplicationBuilder().token(TOKEN).build()
    
    # Add command handlers
    bot.add_handler(CommandHandler("start", start_command))
    bot.add_handler(CommandHandler("battery", battery_command))
    bot.add_handler(CommandHandler("stop", stop_command))
    bot.add_handler(CommandHandler("update_api", update_api_command))
    
    # Start the web server
    threading.Thread(target=run_flask_server).start()
    
    # إضافة إعادة التشغيل التلقائي
    while True:
        try:
            print("🚀 البوت بدأ العمل...")
            bot.run_polling(drop_pending_updates=True)
        except Exception as e:
            print(f"❌ البوت توقف: {e}")
            print("🔄 إعادة تشغيل خلال 30 ثانية...")
            time.sleep(30)

if __name__ == "__main__":
    main()
