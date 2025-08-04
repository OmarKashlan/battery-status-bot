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
FRIDGE_ACTIVATION_THRESHOLD = 50  # Battery percentage needed for fridge
FRIDGE_WARNING_THRESHOLD = 53     # Battery percentage to warn about fridge shutdown
POWER_THRESHOLDS = (500, 850)  # Power thresholds (normal, medium, high) in watts

# Global variables
last_power_usage = None  # To track power usage for alerts
last_electricity_time = None  # To track when electricity was last available
electricity_start_time = None  # To track when electricity started
electricity_duration = None  # To store the duration of last electricity session
fridge_warning_sent = False  # To track if fridge warning has been sent
admin_chat_id = None  # To store admin's chat ID for notifications
last_api_data = None  # Cache for last successful data
last_api_time = None  # Time of last successful API call
user_last_request = {}  # Track user requests to prevent spam

# ============================== DATA FETCHING ============================== #
def get_system_data():
    """Get power system data from API with improved caching and error handling"""
    global last_electricity_time, electricity_start_time, electricity_duration, last_api_data, last_api_time
    
    if not API_URL:
        print("خطأ: عنوان API غير محدد")
        return last_api_data  # Return cached data if available
    
    # If we have recent data (less than 5 seconds old), return cached data
    current_time = datetime.datetime.now()
    if (last_api_data and last_api_time and 
        (current_time - last_api_time).seconds < 5):
        print("استخدام البيانات المخزنة (أحدث من 5 ثواني)")
        return last_api_data
    
    try:
        response = requests.get(API_URL, timeout=5)  # Increased to 5 seconds
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
            
            # Cache the successful data
            last_api_data = system_data
            last_api_time = current_time
            print("✅ تم الحصول على بيانات جديدة من API")
            return system_data
            
        else:
            print(f"API returned status code: {response.status_code}")
            
    except requests.exceptions.Timeout:
        print("API timeout - استخدام البيانات المخزنة")
    except requests.exceptions.ConnectionError:
        print("Connection error - استخدام البيانات المخزنة") 
    except Exception as e:
        print(f"خطأ في الاتصال: {str(e)} - استخدام البيانات المخزنة")
    
    # Return cached data if available, even if API failed
    if last_api_data:
        print("📦 استخدام البيانات المخزنة بسبب فشل API")
        return last_api_data
    
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
    
    # Save the user's chat ID as admin
    admin_chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "مرحباً بك في بوت مراقبة نظام الطاقة! 🔋\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة التلقائية\n"
        "/stop - إيقاف المراقبة التلقائية\n"
        "/update_api - تحديث عنوان API"
    )

async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - show status and start monitoring"""
    global admin_chat_id, user_last_request
    
    # Save the user's chat ID as admin
    admin_chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Rate limiting: prevent spam requests (max 1 request per 3 seconds per user)
    current_time = datetime.datetime.now()
    if user_id in user_last_request:
        time_diff = (current_time - user_last_request[user_id]).seconds
        if time_diff < 3:
            await update.message.reply_text(
                f"⏳ انتظر {3 - time_diff} ثانية قبل الطلب مرة أخرى"
            )
            return
    
    user_last_request[user_id] = current_time
    
    # Send immediate response to user
    status_msg = await update.message.reply_text("⏳ جاري الحصول على البيانات...")
    
    # Get data asynchronously (non-blocking)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    
    if not data:
        await status_msg.edit_text(
            "⚠️ تعذر الحصول على البيانات الحالية\n"
            "جاري المحاولة مرة أخرى خلال ثوان..."
        )
        
        # Try once more after a brief delay
        await asyncio.sleep(2)
        data = await loop.run_in_executor(None, get_system_data)
        
        if not data:
            await status_msg.edit_text("⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة")
            return
    
    # Update the message with actual data
    await edit_status_message(status_msg, data)
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
        caption="⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة"
    )

async def send_status_message(update: Update, data: dict):
    """Format and send current system status"""
    global last_electricity_time, electricity_duration
    
    # Format the electricity time string with 12-hour format
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

async def edit_status_message(message, data: dict):
    """Format and edit existing message with system status"""
    global last_electricity_time, electricity_duration
    
    # Format the electricity time string with 12-hour format
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
    await message.edit_text(status_text)

# ============================== AUTOMATIC MONITORING ============================== #
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
    
    # Get new data asynchronously to avoid blocking
    loop = asyncio.get_event_loop()
    new_data = await loop.run_in_executor(None, get_system_data)

    if not new_data:
        print("📡 فشل في الحصول على البيانات - تخطي هذه الدورة")
        return
    
    # If this is the first run, just store the data and return
    if not old_data:
        context.job.data = new_data
        print(f"✅ أول فحص - تم حفظ البيانات - {datetime.datetime.now().strftime('%H:%M:%S')}")
        return
    
    # Check power usage changes
    if new_data['power_usage'] > POWER_THRESHOLDS[1]:
        if last_power_usage is None:
            await send_power_alert(context, new_data['power_usage'])
            last_power_usage = new_data['power_usage']
    elif new_data['power_usage'] <= POWER_THRESHOLDS[1] and old_data.get('power_usage', 0) > POWER_THRESHOLDS[1]:
        await send_power_reduced_alert(context, new_data['power_usage'])
        last_power_usage = None

    # Check if electricity status changed
    if old_data.get('charging', False) != new_data['charging']:
        await send_electricity_alert(context, new_data['charging'], new_data['battery'])
        # Reset fridge warning when electricity comes back
        if new_data['charging']:
            fridge_warning_sent = False
    
    # Check for fridge warning (battery at 53% and no electricity)
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
    
    # Check for significant battery level changes (skip on first run)
    if 'battery' in old_data and abs(new_data['battery'] - old_data['battery']) >= BATTERY_CHANGE_THRESHOLD:
        await send_battery_alert(context, old_data['battery'], new_data['battery'])

    context.job.data = new_data
    print(f"🔄 فحص مكتمل - {datetime.datetime.now().strftime('%H:%M:%S')}")

# ============================== ALERT MESSAGES ============================== #
async def send_power_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """Send alert for high power consumption"""
    message = f"⚠️ تحذير! استهلاك الطاقة كبير جدًا: {power_usage:.0f}W"
    try:
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"فشل في إرسال تنبيه استهلاك الطاقة: {e}")

async def send_power_reduced_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """Send notification when power consumption decreases"""
    message = f"👍 تم خفض استهلاك الطاقة إلى {power_usage:.0f}W."
    try:
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"فشل في إرسال تنبيه خفض الطاقة: {e}")

async def send_electricity_alert(context: ContextTypes.DEFAULT_TYPE, is_charging: bool, battery_level: float):
    """Send alert when electricity status changes, including battery level"""
    global last_electricity_time, electricity_start_time, electricity_duration
    
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
        # Record the last time electricity was available and calculate duration
        if electricity_start_time is not None:
            last_electricity_time = current_time
            electricity_duration = last_electricity_time - electricity_start_time
            duration_str = format_duration(electricity_duration)
            
            message = (
                f"⚠️ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
                f"نسبة البطارية حالياً هي: {battery_level:.0f}%\n"
                f"مدة بقاء الكهرباء: {duration_str}"
            )
        else:
            message = (
                f"⚠️ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
                f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
            )
    
    try:
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"فشل في إرسال تنبيه الكهرباء: {e}")

async def send_battery_alert(context: ContextTypes.DEFAULT_TYPE, old_value: float, new_value: float):
    """Send alert when battery percentage changes significantly"""
    arrow = "⬆️ زيادة" if new_value > old_value else "⬇️ انخفاض"
    try:
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=f"{arrow}\nالشحن: {old_value:.0f}% → {new_value:.0f}%"
        )
    except Exception as e:
        print(f"فشل في إرسال تنبيه البطارية: {e}")

async def send_fridge_warning_alert(context: ContextTypes.DEFAULT_TYPE, battery_level: float):
    """Send warning when battery is close to fridge shutdown threshold"""
    remaining_percentage = battery_level - FRIDGE_ACTIVATION_THRESHOLD
    message = (
        f"🧊⚠️ تنبيه البراد!\n"
        f"البطارية حالياً: {battery_level:.0f}%\n"
        f"متبقي {remaining_percentage:.0f}% فقط لينطفئ البراد عند الوصول لـ {FRIDGE_ACTIVATION_THRESHOLD}%"
    )
    try:
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
    except Exception as e:
        print(f"فشل في إرسال تنبيه البراد: {e}")

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
    global API_URL, last_api_data, last_api_time
    
    # Check if a URL was provided
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ يرجى توفير عنوان API الجديد بعد الأمر.\n"
            "مثال: /update_api https://example.com/api/new_url"
        )
        return
    
    # Update the API URL and clear cache
    new_url = context.args[0]
    old_url = API_URL
    API_URL = new_url
    last_api_data = None  # Clear cache to force new request
    last_api_time = None
    
    # Send immediate response and test the new URL
    test_msg = await update.message.reply_text("⏳ اختبار العنوان الجديد...")
    
    # Test the new URL asynchronously
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    
    if data:
        await test_msg.edit_text(f"✅ تم تحديث عنوان API بنجاح وتم التحقق من صحته!")
        print(f"API URL updated successfully: {old_url} -> {new_url}")
    else:
        # Restore old URL if new one doesn't work
        API_URL = old_url
        await test_msg.edit_text(
            f"⚠️ العنوان الجديد لا يعمل، تم الاحتفاظ بالعنوان القديم.\n"
            f"يرجى التحقق من العنوان والمحاولة مرة أخرى."
        )

# ============================== MAIN EXECUTION ============================== #
def main():
    """Initialize and start the bot"""
    if not TOKEN:
        print("❌ خطأ: TELEGRAM_TOKEN غير محدد. يرجى تعيينه في متغيرات البيئة أو ملف config.py")
        return
        
    try:
        print("🚀 بدء تشغيل البوت...")
        
        bot = ApplicationBuilder().token(TOKEN).build()
        
        # Add command handlers
        bot.add_handler(CommandHandler("start", start_command))
        bot.add_handler(CommandHandler("battery", battery_command))
        bot.add_handler(CommandHandler("stop", stop_command))
        bot.add_handler(CommandHandler("update_api", update_api_command))
        
        print("✅ البوت جاهز للعمل...")
        
        # Start polling
        bot.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")
        raise e

if __name__ == "__main__":
    main()
