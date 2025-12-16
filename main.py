# ============================== IMPORTS ============================== #
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import datetime
import pytz  # إضافة مكتبة pytz
import asyncio

# ============================== CONFIGURATION ============================== #
try:
    from config import TELEGRAM_TOKEN as TOKEN, API_URL
except ImportError:
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    API_URL = os.environ.get("API_URL")

# Set timezone to Damascus (Syria)
TIMEZONE = pytz.timezone('Asia/Damascus')

# Thresholds
BATTERY_CHANGE_THRESHOLD = 10   # Battery change percentage that triggers alert
FRIDGE_ACTIVATION_THRESHOLD = 65  # Battery percentage needed for fridge
FRIDGE_WARNING_THRESHOLD = 68     # Battery percentage to warn about fridge shutdown
POWER_THRESHOLDS = (500, 850)  # Power thresholds (normal, medium, high) in watts

# Global variables
last_power_usage = None  # To track power usage for alerts
last_electricity_time = None  # To track when electricity was last available
electricity_start_time = None  # To track when electricity started
electricity_duration = None  # To store the duration of last electricity session
fridge_warning_sent = False  # To track if fridge warning has been sent
admin_chat_id = None  # To store admin's chat ID for notifications
api_failure_notified = False  # Track if we already sent API failure notification
last_api_failure_time = None  # Track when API last failed
consecutive_failures = 0  # Track consecutive API failures

# ============================== LOGGING HELPERS ============================== #
def log_command(command, user_id):
    print(f"[COMMAND] {command} by user {user_id}")

def log_bot_to_user(user_id, text):
    print(f"[BOT->USER] To {user_id}: {text}")

def log_api_data(system_data):
    print(f"[API DATA] {system_data}")

# ============================== DATA FETCHING ============================== #
def get_system_data():
    """Get power system data from API - NO CACHING, always fetch fresh data"""
    global last_electricity_time, electricity_start_time, electricity_duration
    
    if not API_URL:
        print("❌ ERROR: API URL is not specified")
        return None
    
    # Try twice with longer timeout
    for attempt in range(2):
        try:
            print(f"🔄 Trying to connect to API... (Attempt {attempt + 1}/2) {datetime.datetime.now().strftime('%H:%M:%S')}")
            response = requests.get(API_URL, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                params = {item['par']: item['val'] for item in data['dat']['parameter']}
                
                # Create the data dictionary
                system_data = {
                    'battery': float(params.get('bt_battery_capacity', 0)),
                    'voltage': float(params.get('bt_grid_voltage', 0)),
                    'charging': float(params.get('bt_grid_voltage', 0)) > 0,
                    'power_usage': float(params.get('bt_load_active_power_sole', 0)) * 1000,
                    'fridge_voltage': float(params.get('bt_ac2_output_voltage', 0)),
                    'charge_current': float(params.get('bt_battery_charging_current', 0))
                }
                
                # Update electricity tracking with correct timezone
                current_time_tz = datetime.datetime.now(TIMEZONE)
                
                if system_data['charging']:
                    if electricity_start_time is None:
                        electricity_start_time = current_time_tz
                    last_electricity_time = current_time_tz
                else:
                    # If electricity just stopped, calculate duration
                    if electricity_start_time is not None and last_electricity_time is not None:
                        electricity_duration = last_electricity_time - electricity_start_time
                    electricity_start_time = None
                
                print("✅ Successfully fetched new data from API")
                log_api_data(system_data)
                return system_data
                
            else:
                print(f"❌ API returned status code: {response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"❌ API timeout - attempt {attempt + 1}")
        except requests.exceptions.ConnectionError:
            print(f"❌ Connection error - attempt {attempt + 1}") 
        except Exception as e:
            print(f"❌ Error connecting to API: {str(e)} - attempt {attempt + 1}")
        
        # Wait 1 second before retry (only on first attempt)
        if attempt == 0:
            import time
            time.sleep(1)
    
    return None

def format_duration(duration):
    """Format duration into readable Arabic text"""
    if duration is None:
        return ""
    
    total_seconds = int(duration.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    duration_parts = []
    
    if hours > 0:
        hour_text = "ساعة" if hours == 1 else f"{hours} ساعات" if hours <= 10 else f"{hours} ساعة"
        duration_parts.append(hour_text)
    
    if minutes > 0:
        minute_text = "دقيقة" if minutes == 1 else f"{minutes} دقائق" if minutes <= 10 else f"{minutes} دقيقة"
        duration_parts.append(minute_text)
    
    if seconds > 0 and hours == 0:  # Only show seconds if less than an hour
        second_text = "ثانية" if seconds == 1 else f"{seconds} ثواني" if seconds <= 10 else f"{seconds} ثانية"
        duration_parts.append(second_text)
    
    if not duration_parts:
        return "أقل من ثانية"
    
    return " و ".join(duration_parts)

# ============================== TELEGRAM COMMANDS ============================== #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - initialize the bot and save admin chat ID"""
    global admin_chat_id
    log_command("/start", update.effective_chat.id)
    admin_chat_id = update.effective_chat.id
    reply = (
        "مرحباً بك في بوت مراقبة نظام الطاقة! 🔋\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة التلقائية\n"
        "/stop - إيقاف المراقبة التلقائية\n"
        "/update_api - تحديث عنوان API"
    )
    log_bot_to_user(update.effective_chat.id, reply)
    await update.message.reply_text(reply)

async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - show status and start monitoring"""
    global admin_chat_id, api_failure_notified, consecutive_failures
    log_command("/battery", update.effective_chat.id)
    admin_chat_id = update.effective_chat.id
    loading = "⏳ جاري الحصول على البيانات..."
    log_bot_to_user(update.effective_chat.id, loading)
    status_msg = await update.message.reply_text(loading)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    log_api_data(data)
    if not data:
        fail_text = "⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة"
        log_bot_to_user(update.effective_chat.id, fail_text)
        await status_msg.edit_text(fail_text)
        return

    data['reported_battery'] = data['battery']

    api_failure_notified = False
    consecutive_failures = 0
    msg = format_status_message(data)
    log_bot_to_user(update.effective_chat.id, msg)
    await status_msg.edit_text(msg)
    start_auto_monitoring(update, context, data)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command - stop monitoring"""
    log_command("/stop", update.effective_chat.id)
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if jobs:
        for job in jobs:
            job.schedule_removal()
        msg = "✅ تم إيقاف المراقبة التلقائية بنجاح."
        log_bot_to_user(update.effective_chat.id, msg)
        await update.message.reply_text(msg)
    else:
        msg = "❌ المراقبة التلقائية غير مفعلة حالياً."
        log_bot_to_user(update.effective_chat.id, msg)
        await update.message.reply_text(msg)

def format_status_message(data: dict) -> str:
    global last_electricity_time, electricity_duration
    if data['charging']:
        electricity_status = "موجودة ويتم الشحن✔️"
        electricity_time_str = "الكهرباء متوفرة حالياً"
    else:
        electricity_status = "لا يوجد كهرباء ⚠️"
        if last_electricity_time:
            electricity_time_str = f"{last_electricity_time.strftime('%I:%M:%S %p')}"
            if electricity_duration:
                duration_str = format_duration(electricity_duration)
                electricity_time_str += f"\nوقد بقيت الكهرباء لمدة {duration_str}"
        else:
            electricity_time_str = "غير معلوم 🤷"
    status_text = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {electricity_status}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}\n"
        f"⏱️ اخر توقيت لوجود الكهرباء: {electricity_time_str}"
    )
    return status_text

# ============================== AUTOMATIC MONITORING ============================== #
def start_auto_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name(f"{chat_id}_reminder"):
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
    global last_power_usage, fridge_warning_sent, api_failure_notified, last_api_failure_time, consecutive_failures
    
    old_data = context.job.data
    loop = asyncio.get_event_loop()
    new_data = await loop.run_in_executor(None, get_system_data)
    log_api_data(new_data)

    # --- 1. Handle API Failure ---
    if not new_data:
        consecutive_failures += 1
        print(f"📡 Failed to get data (attempt {consecutive_failures}/10)")
        if consecutive_failures >= 10 and not api_failure_notified:
            txt = "⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة"
            log_bot_to_user(context.job.chat_id, txt)
            await context.bot.send_message(chat_id=context.job.chat_id, text=txt)
            api_failure_notified = True
            last_api_failure_time = datetime.datetime.now()
            context.job_queue.run_repeating(send_api_failure_reminder, interval=10800, first=10800, chat_id=context.job.chat_id, name=f"{context.job.chat_id}_reminder")
        return

    consecutive_failures = 0
    api_failure_notified = False
    for job in context.job_queue.get_jobs_by_name(f"{context.job.chat_id}_reminder"):
        job.schedule_removal()

    # --- 2. Check Standard Alerts ---
    
    # Power Usage
    if new_data['power_usage'] > POWER_THRESHOLDS[1]:
        if last_power_usage is None:
            await send_power_alert(context, new_data['power_usage'])
            last_power_usage = new_data['power_usage']
    elif new_data['power_usage'] <= POWER_THRESHOLDS[1] and old_data.get('power_usage', 0) > POWER_THRESHOLDS[1]:
        await send_power_reduced_alert(context, new_data['power_usage'])
        last_power_usage = None
    
    # Electricity Status
    if old_data.get('charging', False) != new_data['charging']:
        await send_electricity_alert(context, new_data['charging'], new_data['battery'])
        if new_data['charging']:
            fridge_warning_sent = False

    # Fridge Warning
    if (not new_data['charging'] and 
        new_data['battery'] <= FRIDGE_WARNING_THRESHOLD and 
        new_data['battery'] > FRIDGE_ACTIVATION_THRESHOLD and 
        not fridge_warning_sent):
        await send_fridge_warning_alert(context, new_data['battery'])
        fridge_warning_sent = True
    if (new_data['battery'] > FRIDGE_WARNING_THRESHOLD or new_data['battery'] <= FRIDGE_ACTIVATION_THRESHOLD):
        fridge_warning_sent = False

    # --- 3. FIX PART 2: THE 10% BATTERY CHECK ---
    
    # Get the anchor we set in battery_command. 
    # If it's missing (rare case), default to current battery.
    last_reported = old_data.get('reported_battery', new_data['battery'])
    
    # Calculate the difference between NOW and the ANCHOR
    if abs(new_data['battery'] - last_reported) >= BATTERY_CHANGE_THRESHOLD:
        # Threshold met! Send alert.
        await send_battery_alert(context, last_reported, new_data['battery'])
        
        # MOVE THE ANCHOR to the new level
        new_data['reported_battery'] = new_data['battery']
    else:
        # Threshold NOT met.
        # KEEP THE OLD ANCHOR so we don't "forget" the starting point.
        new_data['reported_battery'] = last_reported
    
    # Save for next loop
    context.job.data = new_data
    print(f"🔄 Check completed - {datetime.datetime.now().strftime('%H:%M:%S')}")

async def send_api_failure_reminder(context: ContextTypes.DEFAULT_TYPE):
    global last_api_failure_time
    if last_api_failure_time:
        duration = datetime.datetime.now() - last_api_failure_time
        hours = int(duration.total_seconds() / 3600)
        txt = (
            f"🔔 تذكير: API لا يزال معطلاً منذ {hours} ساعة\n"
            "الرجاء الطلب من عمورة تحديث الخدمة"
        )
        log_bot_to_user(context.job.chat_id, txt)
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=txt
        )

# ============================== ALERT MESSAGES ============================== #
async def send_power_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    message = f"⚠️ تحذير! استهلاك الطاقة كبير جدًا: {power_usage:.0f}W"
    try:
        log_bot_to_user(context.job.chat_id, message)
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"Failed to send power usage alert: {e}")

async def send_power_reduced_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    message = f"👍 تم خفض استهلاك الطاقة إلى {power_usage:.0f}W."
    try:
        log_bot_to_user(context.job.chat_id, message)
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"Failed to send reduced power alert: {e}")

async def send_electricity_alert(context: ContextTypes.DEFAULT_TYPE, is_charging: bool, battery_level: float):
    global last_electricity_time, electricity_start_time, electricity_duration
    
    current_time = datetime.datetime.now(TIMEZONE)
    
    if is_charging:
        # حالة عودة الكهرباء
        electricity_start_time = current_time
        last_electricity_time = current_time
        # تصفير مدة الجلسة السابقة عند بدء جلسة جديدة
        electricity_duration = None
        
        message = (
            f"✅ عادت الكهرباء! الشحن جارٍ الآن.\n"
            f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
        )
    else:
        # حالة انقطاع الكهرباء
        # نتحقق مما إذا كانت electricity_duration قد حُسبت في get_system_data
        if electricity_duration is not None:
            duration_str = format_duration(electricity_duration)
            message = (
                f"⛔ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
                f"نسبة البطارية حالياً هي: {battery_level:.0f}%\n"
                f"مدة بقاء الكهرباء: {duration_str}"
            )
        else:
            # في حال لم يتم حساب المدة لسبب ما (مثلاً بدأ البوت والكهرباء مقطوعة فوراً)
            message = (
                f"⛔ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
                f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
            )

    try:
        log_bot_to_user(context.job.chat_id, message)
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"Failed to send electricity alert: {e}")

async def send_battery_alert(context: ContextTypes.DEFAULT_TYPE, old_value: float, new_value: float):
    arrow = "⬆️ زيادة" if new_value > old_value else "⬇️ انخفاض"
    message = f"{arrow}\nالشحن: {old_value:.0f}% ← {new_value:.0f}%"
    try:
        log_bot_to_user(context.job.chat_id, message)
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=message
        )
    except Exception as e:
        print(f"Failed to send battery alert: {e}")

async def send_fridge_warning_alert(context: ContextTypes.DEFAULT_TYPE, battery_level: float):
    remaining_percentage = battery_level - FRIDGE_ACTIVATION_THRESHOLD
    message = (
        f"🧊⚠️ تنبيه البراد!\n"
        f"البطارية حالياً: {battery_level:.0f}%\n"
        f"متبقي {remaining_percentage:.0f}% فقط لينطفئ البراد عند الوصول لـ {FRIDGE_ACTIVATION_THRESHOLD}%"
    )
    try:
        log_bot_to_user(context.job.chat_id, message)
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"Failed to send fridge warning alert: {e}")

# ============================== STATUS HELPERS ============================== #
def get_charging_status(current: float) -> str:
    if current >= 60:
        return f"{current:.1f}A (الشحن سريع جداً 🔴)"
    elif 30 <= current < 60:
        return f"{current:.1f}A (الشحن سريع 🟡)"
    elif 1 <= current < 30:
        return f"{current:.1f}A (الشحن طبيعي 🟢)"
    return f"{current:.1f}A (لا يوجد شحن ⚪)"

def get_fridge_status(data: dict) -> str:
    if data['charging']:
        return "يعمل على الكهرباء ⚡"
    elif data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:
        return "يعمل على البطارية 🔋"
    elif data['fridge_voltage'] > 0 and not data['charging']:
        return "يعمل على البطارية (البطارية منخفضة) ⚠️"
    return "مطفئ ⛔"

def get_consumption_status(power: float) -> str:
    if power <= POWER_THRESHOLDS[0]:
        return "عادي 🟢"
    elif POWER_THRESHOLDS[0] < power <= POWER_THRESHOLDS[1]:
        return "متوسط 🟡"
    return "كبير 🔴"

# ============================== API URL UPDATE COMMAND ============================== #
async def update_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global API_URL, api_failure_notified, last_api_failure_time, consecutive_failures
    log_command("/update_api", update.effective_chat.id)
    if not context.args or len(context.args) < 1:
        msg = (
            "❌ يرجى توفير عنوان API الجديد بعد الأمر.\n\n"
            "مثال:\n"
            "/update_api https://web.dessmonitor.com/public/?sign=..."
        )
        log_bot_to_user(update.effective_chat.id, msg)
        await update.message.reply_text(msg)
        return
    new_url = context.args[0]
    old_url = API_URL
    API_URL = new_url
    test_msg_txt = "⏳ اختبار الرابط الجديد..."
    log_bot_to_user(update.effective_chat.id, test_msg_txt)
    test_msg = await update.message.reply_text(test_msg_txt)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    log_api_data(data)
    if data:
        chat_id = update.effective_chat.id
        for job in context.job_queue.get_jobs_by_name(f"{chat_id}_reminder"):
            job.schedule_removal()
        api_failure_notified = False
        last_api_failure_time = None
        consecutive_failures = 0
        msg = (
            f"✅ تم تحديث رابط API بنجاح!\n\n"
            f"يمكنك الآن استخدام /battery لعرض الحالة وبدء المراقبة"
        )
        log_bot_to_user(update.effective_chat.id, msg)
        await test_msg.edit_text(msg)
        print(f"✅ API URL updated: {old_url} -> {new_url}")
    else:
        API_URL = old_url
        msg = (
            f"❌ الرابط الجديد لا يعمل!\n\n"
            f"تم الاحتفاظ بالرابط القديم.\n"
            f"يرجى التحقق من الرابط والمحاولة مرة أخرى."
        )
        log_bot_to_user(update.effective_chat.id, msg)
        await test_msg.edit_text(msg)

# ============================== MAIN EXECUTION ============================== #
def main():
    if not TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN is not set. Please set it in environment variables or config.py.")
        return
    try:
        print("🚀 Starting the bot...")
        bot = ApplicationBuilder().token(TOKEN).build()
        bot.add_handler(CommandHandler("start", start_command))
        bot.add_handler(CommandHandler("battery", battery_command))
        bot.add_handler(CommandHandler("stop", stop_command))
        bot.add_handler(CommandHandler("update_api", update_api_command))
        print("✅ Bot is ready and running...")
        bot.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ Error running the bot: {e}")
        raise e

if __name__ == "__main__":
    main()
