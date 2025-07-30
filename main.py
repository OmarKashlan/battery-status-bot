# ============================== IMPORTS ============================== #
import os
import requests
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import datetime
import pytz
from typing import Optional, Dict

# ============================== CONFIGURATION ============================== #
try:
    from config import TELEGRAM_TOKEN as TOKEN, API_URL
except ImportError:
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    API_URL = os.environ.get("API_URL")

# Set timezone to Damascus (Syria)
TIMEZONE = pytz.timezone('Asia/Damascus')

# Thresholds
BATTERY_CHANGE_THRESHOLD = 3
FRIDGE_ACTIVATION_THRESHOLD = 50
FRIDGE_WARNING_THRESHOLD = 53
POWER_THRESHOLDS = (500, 850)

# Performance settings
API_TIMEOUT = 5  # تقليل timeout إلى 5 ثواني
MONITORING_INTERVAL = 5  # فحص كل 5 ثواني بدلاً من 10
MAX_RETRIES = 2  # محاولات إعادة الاتصال

# Global variables
last_power_usage = None
last_electricity_time = None
electricity_start_time = None
fridge_warning_sent = False
admin_chat_id = None
last_api_data = None  # Cache للبيانات الأخيرة
api_session = None  # Async HTTP session

# ============================== ASYNC API HANDLING ============================== #
async def init_api_session():
    """Initialize async HTTP session for better performance"""
    global api_session
    if api_session is None:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        api_session = aiohttp.ClientSession(timeout=timeout)

async def close_api_session():
    """Close async HTTP session"""
    global api_session
    if api_session:
        await api_session.close()
        api_session = None

async def get_system_data_async() -> Optional[Dict]:
    """Get power system data from API - Async version for better performance"""
    global last_electricity_time, electricity_start_time, last_api_data
    
    if not API_URL:
        print("خطأ: عنوان API غير محدد")
        return last_api_data  # Return cached data if available
    
    await init_api_session()
    
    for attempt in range(MAX_RETRIES):
        try:
            async with api_session.get(API_URL) as response:
                if response.status == 200:
                    data = await response.json()
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
                    current_time = datetime.datetime.now(TIMEZONE)
                    if system_data['charging']:
                        if electricity_start_time is None:
                            electricity_start_time = current_time
                        last_electricity_time = current_time
                    else:
                        electricity_start_time = None
                    
                    # Cache the successful data
                    last_api_data = system_data
                    return system_data
                
        except asyncio.TimeoutError:
            print(f"API timeout - attempt {attempt + 1}/{MAX_RETRIES}")
        except Exception as e:
            print(f"خطأ في الاتصال (محاولة {attempt + 1}/{MAX_RETRIES}): {str(e)}")
        
        # Wait briefly before retry
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(1)
    
    print("فشل في الاتصال بالAPI - استخدام البيانات المخزنة")
    return last_api_data

def get_system_data():
    """Sync wrapper for API calls"""
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(get_system_data_async())
    except Exception as e:
        print(f"خطأ في sync wrapper: {e}")
        return last_api_data

# ============================== TELEGRAM COMMANDS ============================== #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    global admin_chat_id
    admin_chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "مرحباً بك في بوت مراقبة نظام الطاقة! ⚡\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة السريعة\n"
        "/stop - إيقاف المراقبة\n"
        "/update_api - تحديث عنوان API\n\n"
        "🚀 تم تحسين البوت للاستجابة الفورية!"
    )

async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - optimized for speed"""
    global admin_chat_id
    admin_chat_id = update.effective_chat.id
    
    # Send "getting data" message first for immediate feedback
    status_msg = await update.message.reply_text("⏳ جاري الحصول على البيانات...")
    
    data = await get_system_data_async()
    
    if not data:
        await status_msg.edit_text(
            "⚠️ تعذر الحصول على البيانات الحالية\n"
            "جاري المحاولة مرة أخرى..."
        )
        return
    
    await status_msg.edit_text(format_status_message(data))
    await start_fast_monitoring(update, context, data)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    
    if jobs:
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("✅ تم إيقاف المراقبة السريعة")
    else:
        await update.message.reply_text("❌ المراقبة غير مفعلة حالياً")

def format_status_message(data: dict) -> str:
    """Format status message efficiently"""
    global last_electricity_time
    
    if data['charging']:
        electricity_status = "متوفرة ⚡"
        electricity_time_str = "الكهرباء متوفرة الآن"
    else:
        electricity_status = "منقطعة ⚠️"
        electricity_time_str = f"آخر مرة: {last_electricity_time.strftime('%I:%M %p')}" if last_electricity_time else "غير معلوم"
    
    return (
        f"🔋 البطارية: {data['battery']:.0f}%\n"
        f"⚡ الكهرباء: {electricity_status}\n"
        f"⚙️ الاستهلاك: {data['power_usage']:.0f}W ({get_power_status(data['power_usage'])})\n"
        f"🔌 الشحن: {get_charge_status(data['charge_current'])}\n"
        f"🧊 البراد: {get_fridge_status(data)}\n"
        f"🕐 {electricity_time_str}"
    )

# ============================== FAST MONITORING SYSTEM ============================== #
async def start_fast_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    """Start high-frequency monitoring for immediate alerts"""
    chat_id = update.effective_chat.id
    
    # Remove any existing jobs
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    
    # Start fast monitoring (every 5 seconds)
    context.job_queue.run_repeating(
        fast_monitor_changes,
        interval=MONITORING_INTERVAL,
        first=2,  # Start checking after 2 seconds
        chat_id=chat_id,
        name=str(chat_id),
        data=initial_data
    )
    
    await update.message.reply_text("🚀 بدء المراقبة السريعة (كل 5 ثواني)")

async def fast_monitor_changes(context: ContextTypes.DEFAULT_TYPE):
    """Fast monitoring with immediate alerts"""
    global last_power_usage, fridge_warning_sent

    old_data = context.job.data
    new_data = await get_system_data_async()

    if not new_data:
        # If API fails, try one more time immediately
        await asyncio.sleep(1)
        new_data = await get_system_data_async()
        if not new_data:
            return

    # If this is the first run, just store data
    if not old_data:
        context.job.data = new_data
        return

    # Create alert tasks to run concurrently
    alert_tasks = []

    # Power usage alerts (immediate)
    if new_data['power_usage'] > POWER_THRESHOLDS[1] and last_power_usage is None:
        alert_tasks.append(send_instant_alert(context, f"⚠️ استهلاك عالي: {new_data['power_usage']:.0f}W"))
        last_power_usage = new_data['power_usage']
    elif new_data['power_usage'] <= POWER_THRESHOLDS[1] and old_data.get('power_usage', 0) > POWER_THRESHOLDS[1]:
        alert_tasks.append(send_instant_alert(context, f"✅ انخفض الاستهلاك: {new_data['power_usage']:.0f}W"))
        last_power_usage = None

    # Electricity status (immediate)
    if old_data.get('charging', False) != new_data['charging']:
        if new_data['charging']:
            alert_tasks.append(send_instant_alert(context, f"⚡ عادت الكهرباء! البطارية: {new_data['battery']:.0f}%"))
            fridge_warning_sent = False
        else:
            alert_tasks.append(send_instant_alert(context, f"🔋 انقطعت الكهرباء! البطارية: {new_data['battery']:.0f}%"))

    # Fridge warning (immediate)
    if (not new_data['charging'] and 
        new_data['battery'] <= FRIDGE_WARNING_THRESHOLD and 
        new_data['battery'] > FRIDGE_ACTIVATION_THRESHOLD and 
        not fridge_warning_sent):
        remaining = new_data['battery'] - FRIDGE_ACTIVATION_THRESHOLD
        alert_tasks.append(send_instant_alert(context, f"🧊⚠️ البراد سينطفئ خلال {remaining:.0f}%!"))
        fridge_warning_sent = True

    # Reset fridge warning
    if (new_data['battery'] > FRIDGE_WARNING_THRESHOLD or 
        new_data['battery'] <= FRIDGE_ACTIVATION_THRESHOLD):
        fridge_warning_sent = False

    # Battery level changes (immediate)
    if 'battery' in old_data and abs(new_data['battery'] - old_data['battery']) >= BATTERY_CHANGE_THRESHOLD:
        arrow = "⬆️" if new_data['battery'] > old_data['battery'] else "⬇️"
        alert_tasks.append(send_instant_alert(context, f"{arrow} البطارية: {old_data['battery']:.0f}% → {new_data['battery']:.0f}%"))

    # Send all alerts concurrently for maximum speed
    if alert_tasks:
        await asyncio.gather(*alert_tasks, return_exceptions=True)

    context.job.data = new_data

async def send_instant_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send alert immediately without delay"""
    try:
        await context.bot.send_message(
            chat_id=context.job.chat_id, 
            text=message,
            disable_notification=False  # Ensure notifications are enabled
        )
    except Exception as e:
        print(f"فشل إرسال التنبيه السريع: {e}")

# ============================== HELPER FUNCTIONS ============================== #
def get_charge_status(current: float) -> str:
    """Get charging status - optimized"""
    if current >= 60:
        return f"{current:.1f}A سريع جداً 🔴"
    elif current >= 30:
        return f"{current:.1f}A سريع 🟡"
    elif current >= 1:
        return f"{current:.1f}A عادي 🟢"
    return f"{current:.1f}A متوقف ⚪"

def get_fridge_status(data: dict) -> str:
    """Get fridge status - optimized"""
    if data['charging']:
        return "يعمل على الكهرباء ⚡"
    elif data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:
        return "يعمل على البطارية 🔋"
    elif data['fridge_voltage'] > 0:
        return "يعمل (بطارية منخفضة) ⚠️"
    return "مطفئ ⛔"

def get_power_status(power: float) -> str:
    """Get power consumption status - optimized"""
    if power <= POWER_THRESHOLDS[0]:
        return "عادي 🟢"
    elif power <= POWER_THRESHOLDS[1]:
        return "متوسط 🟡"
    return "عالي 🔴"

# ============================== API URL UPDATE ============================== #
async def update_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update API URL with immediate testing"""
    global API_URL
    
    if not context.args:
        await update.message.reply_text(
            "❌ أرسل عنوان API الجديد\n"
            "مثال: /update_api https://example.com/api"
        )
        return
    
    new_url = context.args[0]
    API_URL = new_url
    
    # Test immediately
    test_msg = await update.message.reply_text("⏳ اختبار العنوان الجديد...")
    data = await get_system_data_async()
    
    if data:
        await test_msg.edit_text("✅ تم تحديث API بنجاح!")
    else:
        await test_msg.edit_text("⚠️ العنوان لا يعمل، تحقق منه")

# ============================== MAIN EXECUTION ============================== #
def main():
    """Initialize and start the optimized bot"""
    if not TOKEN:
        print("❌ خطأ: TELEGRAM_TOKEN غير محدد")
        return
        
    try:
        print("🚀 بدء تشغيل البوت السريع...")
        
        bot = ApplicationBuilder().token(TOKEN).build()
        
        # Add command handlers
        bot.add_handler(CommandHandler("start", start_command))
        bot.add_handler(CommandHandler("battery", battery_command))
        bot.add_handler(CommandHandler("stop", stop_command))
        bot.add_handler(CommandHandler("update_api", update_api_command))
        
        print("✅ البوت السريع جاهز للعمل...")
        
        # Start with optimizations
        bot.run_polling(
            drop_pending_updates=True,
            allowed_updates=['message', 'callback_query']  # Only process needed updates
        )
        
    except Exception as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")
        raise e
    finally:
        # Cleanup
        try:
            asyncio.run(close_api_session())
        except:
            pass

if __name__ == "__main__":
    main()
