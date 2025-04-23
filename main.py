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
POWER_THRESHOLDS = (300, 500)  # Power thresholds (normal, medium, high) in watts
API_CHECK_INTERVAL = 300  # Check API every 5 minutes (300 seconds)

# Global variables
last_power_usage = None  # To track power usage for alerts
last_electricity_time = None  # To track when electricity was last available
electricity_start_time = None  # To track when electricity started
api_failure_notified = False  # To track if we've already notified about API failure
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
        response = requests.get(API_URL)
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
                # If this is the first time we're seeing electricity, record the start time
                if electricity_start_time is None:
                    electricity_start_time = datetime.datetime.now(TIMEZONE)
                # Always update the last seen time when electricity is available
                last_electricity_time = datetime.datetime.now(TIMEZONE)
            else:
                # If electricity was previously available but now it's gone, record the last time
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
        "/stop - إيقاف المراقبة التلقائية\n"
        "/buzzer - عرض حالة الزمور الحالية\n"
        "/buzzer on - تشغيل الزمور\n"
        "/buzzer off - إيقاف الزمور\n"
        "/update_api - تحديث عنوان API\n\n"
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

# ============================== BUZZER CONTROL COMMANDS ============================== #
async def buzzer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buzzer command - control buzzer status"""
    global admin_chat_id
    
    # Save the user's chat ID as admin
    admin_chat_id = update.effective_chat.id
    
    # Check if arguments are provided (on/off)
    if not context.args or len(context.args) < 1:
        # If no argument, check current status first
        status = await get_buzzer_status()
        
        if status is None:
            await update.message.reply_text("⚠️ تعذر الاتصال بال API للتحقق من حالة الزمور.")
            return
            
        await update.message.reply_text(
            f"🔊 حالة الزمور الحالية: {'مفعّل' if status == 'Enable' else 'معطل'}\n\n"
            "استخدم الأمر مع 'on' لتشغيل الزمور أو 'off' لإيقافه.\n"
            "مثال: /buzzer on"
        )
        return
    
    # Process the command with argument
    command = context.args[0].lower()
    
    if command == "on":
        result = await set_buzzer_status(True)
        if result:
            await update.message.reply_text("✅ تم تشغيل الزمور بنجاح.")
        else:
            await update.message.reply_text("⚠️ تعذر تشغيل الزمور. يرجى التحقق من الاتصال.")
    
    elif command == "off":
        result = await set_buzzer_status(False)
        if result:
            await update.message.reply_text("✅ تم إيقاف الزمور بنجاح.")
        else:
            await update.message.reply_text("⚠️ تعذر إيقاف الزمور. يرجى التحقق من الاتصال.")
    
    else:
        await update.message.reply_text(
            "❌ أمر غير صالح. استخدم:\n"
            "/buzzer on - لتشغيل الزمور\n"
            "/buzzer off - لإيقاف الزمور"
        )

async def get_buzzer_status():
    """Get current buzzer status from API"""
    try:
        # استخدام العنوان الأساسي مباشرة
        base_url = "https://web1.shinemonitor.com/public/"
        
        # استخراج المعاملات الأساسية من عنوان API الحالي
        params = {}
        if '?' in API_URL:
            query_string = API_URL.split('?')[1]
            for param in query_string.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    params[key] = value
        
        # الاحتفاظ بالمعاملات الأساسية فقط
        essential_params = {
            'sign': params.get('sign', ''),
            'salt': params.get('salt', ''),
            'token': params.get('token', ''),
            'pn': params.get('pn', ''),
            'sn': params.get('sn', ''),
            'devcode': params.get('devcode', ''),
            'devaddr': params.get('devaddr', ''),
            'source': params.get('source', ''),
            'i18n': params.get('i18n', '')
        }
        
        # إضافة معلمات استعلام الزمور
        query_params = essential_params.copy()
        query_params.update({
            'action': 'queryDeviceCtrlValue',
            'id': 'std_buzzer_ctrl_a'
        })
        
        # بناء عنوان URL كامل
        query_url = base_url + '?' + '&'.join(f'{k}={v}' for k, v in query_params.items() if v)
        
        # طباعة عنوان URL للتصحيح
        print(f"Buzzer status URL: {query_url}")
        
        # إرسال الطلب
        response = requests.get(query_url)
        
        # طباعة الاستجابة للتصحيح
        print(f"Buzzer status response: {response.status_code} - {response.text[:200]}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('err') == 0 and 'dat' in data:
                return data['dat'].get('val')
        
        return None
    except Exception as e:
        print(f"خطأ في جلب حالة الزمور: {str(e)}")
        return None

async def set_buzzer_status(enable: bool):
    """Set buzzer status (enable/disable)"""
    try:
        # استخدام العنوان الأساسي مباشرة
        base_url = "https://web1.shinemonitor.com/public/"
        
        # استخراج المعاملات من API_URL
        params = {}
        if '?' in API_URL:
            query_string = API_URL.split('?')[1]
            for param in query_string.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    params[key] = value
        
        # المعاملات الأساسية
        essential_params = {
            'sign': params.get('sign', ''),
            'salt': params.get('salt', ''),
            'token': params.get('token', ''),
            'pn': params.get('pn', ''),
            'sn': params.get('sn', ''),
            'devcode': params.get('devcode', ''),
            'devaddr': params.get('devaddr', ''),
            'source': params.get('source', ''),
            'i18n': params.get('i18n', '')
        }
        
        # إضافة معلمات التحكم بالزمور
        control_params = essential_params.copy()
        control_params.update({
            'action': 'ctrlDevice',
            'id': 'std_buzzer_ctrl_a',
            'val': '69' if enable else '68'  # 69 = Enable, 68 = Disable
        })
        
        # بناء عنوان URL كامل
        control_url = base_url + '?' + '&'.join(f'{k}={v}' for k, v in control_params.items() if v)
        
        # طباعة عنوان URL للتصحيح
        print(f"Buzzer control URL: {control_url}")
        
        # إرسال الطلب
        response = requests.get(control_url)
        
        # طباعة الاستجابة للتصحيح
        print(f"Buzzer control response: {response.status_code} - {response.text[:200]}")
        
        if response.status_code == 200:
            data = response.json()
            return data.get('err') == 0
        
        return False
    except Exception as e:
        print(f"خطأ في تعيين حالة الزمور: {str(e)}")
        return False

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
    bot.add_handler(CommandHandler("buzzer", buzzer_command))
    
    # Start the web server
    threading.Thread(target=run_flask_server).start()
    
    # Start polling
    bot.run_polling()

if __name__ == "__main__":
    main()
