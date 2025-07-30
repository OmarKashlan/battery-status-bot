# ============================== IMPORTS ============================== #
import os
import requests
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import datetime
import pytz
from typing import Optional, Dict
import json

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
API_TIMEOUT = 10  # Increased timeout for stability
MONITORING_INTERVAL = 10  # Check every 10 seconds for stability
MAX_RETRIES = 3  # More retries for better reliability

# Global variables
last_power_usage = None
last_electricity_time = None
electricity_start_time = None
fridge_warning_sent = False
admin_chat_id = None
last_api_data = None  # Cache for last data

# ============================== API HANDLING ============================== #
def get_system_data() -> Optional[Dict]:
    """Get power system data from API with proper error handling"""
    global last_electricity_time, electricity_start_time, last_api_data
    
    if not API_URL:
        print("خطأ: عنوان API غير محدد")
        return last_api_data  # Return cached data if available
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(API_URL, timeout=API_TIMEOUT)
            
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
                
            else:
                print(f"API returned status code: {response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"API timeout - attempt {attempt + 1}/{MAX_RETRIES}")
        except requests.exceptions.ConnectionError:
            print(f"Connection error - attempt {attempt + 1}/{MAX_RETRIES}")
        except Exception as e:
            print(f"خطأ في الاتصال (محاولة {attempt + 1}/{MAX_RETRIES}): {str(e)}")
        
        # Wait briefly before retry
        if attempt < MAX_RETRIES - 1:
            import time
            time.sleep(2)
    
    print("فشل في الاتصال بالAPI - استخدام البيانات المخزنة")
    return last_api_data

# ============================== TELEGRAM COMMANDS ============================== #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    global admin_chat_id
    admin_chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "مرحباً بك في بوت مراقبة نظام الطاقة! ⚡\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة\n"
        "/stop - إيقاف المراقبة\n"
        "/update_api - تحديث عنوان API\n\n"
        "🚀 البوت جاهز للعمل!"
    )

async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command"""
    global admin_chat_id
    admin_chat_id = update.effective_chat.id
    
    # Send "getting data" message first for immediate feedback
    status_msg = await update.message.reply_text("⏳ جاري الحصول على البيانات...")
    
    # Get data in a separate thread to avoid blocking
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    
    if not data:
        await status_msg.edit_text(
            "⚠️ تعذر الحصول على البيانات الحالية\n"
            "تحقق من اتصال الإنترنت أو عنوان API"
        )
        return
    
    await status_msg.edit_text(format_status_message(data))
    await start_monitoring(update, context, data)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    
    if jobs:
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("✅ تم إيقاف المراقبة")
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
        if last_electricity_time:
            electricity_time_str = f"آخر مرة: {last_electricity_time.strftime('%I:%M %p')}"
        else:
            electricity_time_str = "غير معلوم"
    
    return (
        f"🔋 البطارية: {data['battery']:.0f}%\n"
        f"⚡ الكهرباء: {electricity_status}\n"
        f"⚙️ الاستهلاك: {data['power_usage']:.0f}W ({get_power_status(data['power_usage'])})\n"
        f"🔌 الشحن: {get_charge_status(data['charge_current'])}\n"
        f"🧊 البراد: {get_fridge_status(data)}\n"
        f"🕐 {electricity_time_str}"
    )

# ============================== MONITORING SYSTEM ============================== #
async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    """Start monitoring for changes"""
    chat_id = update.effective_chat.id
    
    # Remove any existing jobs
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    
    # Start monitoring
    context.job_queue.run_repeating(
        monitor_changes,
        interval=MONITORING_INTERVAL,
        first=MONITORING_INTERVAL,  # Start checking after interval
        chat_id=chat_id,
        name=str(chat_id),
        data=initial_data
    )
    
    await update.message.reply_text(f"🚀 بدء المراقبة (كل {MONITORING_INTERVAL} ثانية)")

async def monitor_changes(context: ContextTypes.DEFAULT_TYPE):
    """Monitor for system changes and send alerts"""
    global last_power_usage, fridge_warning_sent

    old_data = context.job.data
    
    # Get new data in executor to avoid blocking
    loop = asyncio.get_event_loop()
    new_data = await loop.run_in_executor(None, get_system_data)

    if not new_data:
        print("فشل في الحصول على البيانات - تخطي هذه الدورة")
        return

    # If this is the first run, just store data
    if not old_data:
        context.job.data = new_data
        print(f"✅ أول فحص - تم حفظ البيانات - {datetime.datetime.now().strftime('%H:%M:%S')}")
        return

    # Check for changes and send alerts
    alerts_sent = []

    # Power usage alerts
    if (new_data['power_usage'] > POWER_THRESHOLDS[1] and 
        old_data.get('power_usage', 0) <= POWER_THRESHOLDS[1]):
        alert_msg = f"⚠️ استهلاك عالي: {new_data['power_usage']:.0f}W"
        await send_alert(context, alert_msg)
        alerts_sent.append("high_power")
        
    elif (new_data['power_usage'] <= POWER_THRESHOLDS[1] and 
          old_data.get('power_usage', 0) > POWER_THRESHOLDS[1]):
        alert_msg = f"✅ انخفض الاستهلاك: {new_data['power_usage']:.0f}W"
        await send_alert(context, alert_msg)
        alerts_sent.append("normal_power")

    # Electricity status changes
    if old_data.get('charging', False) != new_data['charging']:
        if new_data['charging']:
            alert_msg = f"⚡ عادت الكهرباء! البطارية: {new_data['battery']:.0f}%"
            fridge_warning_sent = False  # Reset fridge warning
        else:
            alert_msg = f"🔋 انقطعت الكهرباء! البطارية: {new_data['battery']:.0f}%"
        
        await send_alert(context, alert_msg)
        alerts_sent.append("electricity_change")

    # Fridge warning
    if (not new_data['charging'] and 
        new_data['battery'] <= FRIDGE_WARNING_THRESHOLD and 
        new_data['battery'] > FRIDGE_ACTIVATION_THRESHOLD and 
        not fridge_warning_sent):
        
        remaining = new_data['battery'] - FRIDGE_ACTIVATION_THRESHOLD
        alert_msg = f"🧊⚠️ البراد سينطفئ خلال {remaining:.0f}%!"
        await send_alert(context, alert_msg)
        fridge_warning_sent = True
        alerts_sent.append("fridge_warning")

    # Reset fridge warning when appropriate
    if (new_data['battery'] > FRIDGE_WARNING_THRESHOLD or 
        new_data['battery'] <= FRIDGE_ACTIVATION_THRESHOLD or
        new_data['charging']):
        fridge_warning_sent = False

    # Battery level changes (significant changes only)
    battery_diff = abs(new_data['battery'] - old_data.get('battery', 0))
    if battery_diff >= BATTERY_CHANGE_THRESHOLD:
        arrow = "⬆️" if new_data['battery'] > old_data['battery'] else "⬇️"
        alert_msg = f"{arrow} البطارية: {old_data['battery']:.0f}% → {new_data['battery']:.0f}%"
        await send_alert(context, alert_msg)
        alerts_sent.append("battery_change")

    # Update stored data
    context.job.data = new_data
    
    # Log monitoring activity
    status = f"فحص مكتمل - {datetime.datetime.now().strftime('%H:%M:%S')}"
    if alerts_sent:
        status += f" - تنبيهات: {len(alerts_sent)}"
    print(f"✅ {status}")

async def send_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send alert message"""
    try:
        await context.bot.send_message(
            chat_id=context.job.chat_id, 
            text=message,
            disable_notification=False
        )
        print(f"📤 تم إرسال تنبيه: {message}")
    except Exception as e:
        print(f"فشل إرسال التنبيه: {e}")

# ============================== HELPER FUNCTIONS ============================== #
def get_charge_status(current: float) -> str:
    """Get charging status"""
    if current >= 60:
        return f"{current:.1f}A سريع جداً 🔴"
    elif current >= 30:
        return f"{current:.1f}A سريع 🟡"
    elif current >= 1:
        return f"{current:.1f}A عادي 🟢"
    return f"{current:.1f}A متوقف ⚪"

def get_fridge_status(data: dict) -> str:
    """Get fridge status"""
    if data['charging']:
        return "يعمل على الكهرباء ⚡"
    elif data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:
        return "يعمل على البطارية 🔋"
    elif data['fridge_voltage'] > 0:
        return "يعمل (بطارية منخفضة) ⚠️"
    return "مطفئ ⛔"

def get_power_status(power: float) -> str:
    """Get power consumption status"""
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
    old_url = API_URL
    API_URL = new_url
    
    # Test the new URL
    test_msg = await update.message.reply_text("⏳ اختبار العنوان الجديد...")
    
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, get_system_data)
    
    if data:
        await test_msg.edit_text("✅ تم تحديث API بنجاح!")
        print(f"API URL updated: {old_url} -> {new_url}")
    else:
        API_URL = old_url  # Restore old URL
        await test_msg.edit_text("⚠️ العنوان الجديد لا يعمل، تم الاحتفاظ بالعنوان القديم")

# ============================== MAIN EXECUTION ============================== #
def main():
    """Initialize and start the bot"""
    if not TOKEN:
        print("❌ خطأ: TELEGRAM_TOKEN غير محدد")
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
        print(f"📡 API URL: {API_URL or 'غير محدد'}")
        
        # Start the bot
        bot.run_polling(
            drop_pending_updates=True,
            allowed_updates=['message']
        )
        
    except KeyboardInterrupt:
        print("\n🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        print(f"❌ خطأ في تشغيل البوت: {e}")
        raise e

if __name__ == "__main__":
    main()
