# ============================== IMPORTS ============================== #
import os
import hashlib
import time
import threading
import requests
import urllib.parse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import datetime
import pytz
import asyncio

# ============================== CONFIGURATION ============================== #
try:
    from config import (
        TELEGRAM_TOKEN as TOKEN,
        DESS_USERNAME, DESS_PASSWORD, DESS_COMPANY_KEY,
        DESS_DEVICE_PN, DESS_DEVCODE, DESS_DEVADDR, DESS_DEVICE_SN
    )
except ImportError:
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    DESS_USERNAME = os.environ.get("DESS_USERNAME")
    DESS_PASSWORD = os.environ.get("DESS_PASSWORD")
    DESS_COMPANY_KEY = os.environ.get("DESS_COMPANY_KEY")
    DESS_DEVICE_PN = os.environ.get("DESS_DEVICE_PN")
    DESS_DEVCODE = os.environ.get("DESS_DEVCODE")
    DESS_DEVADDR = os.environ.get("DESS_DEVADDR")
    DESS_DEVICE_SN = os.environ.get("DESS_DEVICE_SN")

# Set timezone to Damascus (Syria)
TIMEZONE = pytz.timezone('Asia/Damascus')

# Thresholds
BATTERY_CHANGE_THRESHOLD = 10
FRIDGE_ACTIVATION_THRESHOLD = 65
FRIDGE_WARNING_THRESHOLD = 68
POWER_THRESHOLDS = (500, 850)

# Global variables
last_power_usage = None
last_electricity_time = None
electricity_start_time = None
electricity_duration = None
fridge_warning_sent = False
admin_chat_id = None
api_failure_notified = False
last_api_failure_time = None
consecutive_failures = 0

# Legacy fallback URL (for /update_api command)
LEGACY_API_URL = None


# ============================== DESSMONITOR API CLIENT ============================== #
class DessMonitorAPI:
    """Handles authentication, token management, and data fetching from DessMonitor API."""
    
    BASE_URL = "https://web.dessmonitor.com/public/"

    def __init__(self, username, password, company_key, device_pn, devcode, devaddr, device_sn):
        self.username = username
        self.password = password
        self.company_key = company_key
        self.device_pn = device_pn
        self.devcode = devcode
        self.devaddr = devaddr
        self.device_sn = device_sn

        self.secret = None
        self.token = None
        self.token_expiry = 0
        self._lock = threading.Lock()
        self._auth_variant = 1  # Which sign variant worked for auth

    def _sha1(self, text):
        """Compute SHA-1 hash and return lowercase hex string."""
        return hashlib.sha1(text.encode('utf-8')).hexdigest()

    def _get_salt(self):
        """Get current timestamp in milliseconds as salt."""
        return str(int(time.time() * 1000))

    def _build_url(self, sign, salt, token, action_string):
        """Build request URL. The action_string is appended as-is since it's
        already formatted correctly (spaces as +, etc.) and matches the sign."""
        base = f"{self.BASE_URL}?sign={sign}&salt={salt}"
        if token:
            base += f"&token={urllib.parse.quote(token, safe='-_.')}"
        base += action_string
        return base

    def _encode_username(self, username):
        """Encode username for sign computation: spaces become +"""
        return username.replace(' ', '+')

    def authenticate(self):
        """Authenticate with DessMonitor API and get secret + token.
        
        Verified sign format from real web app:
        sign = SHA-1(salt + SHA-1(pwd) + "&action=authSource&usr=Omar+Kashlan&source=1&company-key=XXX")
        Key: spaces in username are encoded as +, and param order is usr→source→company-key
        """
        salt = self._get_salt()
        pwd_hash = self._sha1(self.password)
        encoded_usr = self._encode_username(self.username)

        # Exact format verified against real web.dessmonitor.com auth request
        action_string = (
            f"&action=authSource"
            f"&usr={encoded_usr}"
            f"&source=1"
            f"&company-key={self.company_key}"
        )
        sign = self._sha1(salt + pwd_hash + action_string)
        url = self._build_url(sign, salt, None, action_string)

        try:
            print(f"🔐 Authenticating with DessMonitor API...")
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data.get('err') == 0:
                    self.secret = data['dat']['secret']
                    self.token = data['dat']['token']
                    expire_seconds = data['dat'].get('expire', 432000)
                    self.token_expiry = time.time() + expire_seconds
                    hours = expire_seconds / 3600
                    print(f"✅ Auth successful. Token valid for {hours:.0f} hours")
                    return True
                else:
                    print(f"❌ Auth failed: err={data.get('err')} - {data.get('desc', 'Unknown error')}")
            else:
                print(f"❌ Auth HTTP error: {response.status_code}")
        except requests.exceptions.Timeout:
            print("❌ Auth timeout")
        except requests.exceptions.ConnectionError:
            print("❌ Auth connection error")
        except Exception as e:
            print(f"❌ Auth exception: {e}")

        return False

    def refresh_token(self):
        """Refresh token using updateToken endpoint."""
        if not self.secret or not self.token:
            return self.authenticate()

        salt = self._get_salt()
        action_string = "&action=updateToken&source=1"
        sign = self._sha1(salt + self.secret + self.token + action_string)
        url = self._build_url(sign, salt, self.token, action_string)

        try:
            print("🔄 Refreshing token...")
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data.get('err') == 0:
                    self.secret = data['dat']['secret']
                    self.token = data['dat']['token']
                    expire_seconds = data['dat'].get('expire', 432000)
                    self.token_expiry = time.time() + expire_seconds
                    hours = expire_seconds / 3600
                    print(f"✅ Token refreshed. Valid for {hours:.0f} hours")
                    return True
                else:
                    print(f"⚠️ Token refresh failed (err={data.get('err')}), re-authenticating...")
                    return self.authenticate()
        except Exception as e:
            print(f"⚠️ Token refresh exception: {e}, re-authenticating...")
            return self.authenticate()

        return self.authenticate()

    def ensure_token(self):
        """Ensure we have a valid token. Refresh if needed."""
        with self._lock:
            if not self.token or not self.secret:
                return self.authenticate()
            # Refresh if within 1 hour of expiry
            if time.time() > (self.token_expiry - 3600):
                return self.refresh_token()
            return True

    def query_device_data(self):
        """Fetch device parameters via queryDeviceParsEs. Returns raw API response or None."""
        if not self.ensure_token():
            return None

        salt = self._get_salt()
        action_string = (
            f"&action=queryDeviceParsEs"
            f"&source=1"
            f"&devcode={self.devcode}"
            f"&pn={self.device_pn}"
            f"&devaddr={self.devaddr}"
            f"&sn={self.device_sn}"
            f"&i18n=en_US"
        )
        sign = self._sha1(salt + self.secret + self.token + action_string)
        url = self._build_url(sign, salt, self.token, action_string)

        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data.get('err') == 0:
                    return data
                else:
                    err_code = data.get('err', -1)
                    print(f"⚠️ API error: err={err_code} - {data.get('desc', 'Unknown')}")
                    # Token might be expired/invalid - try re-auth once
                    if err_code in [10, 0x000A, 0x000B]:
                        print("🔐 Token appears invalid, re-authenticating...")
                        with self._lock:
                            if self.authenticate():
                                # Retry with fresh token
                                salt = self._get_salt()
                                sign = self._sha1(salt + self.secret + self.token + action_string)
                                url = self._build_url(sign, salt, self.token, action_string)
                                response = requests.get(url, timeout=15)
                                if response.status_code == 200:
                                    data = response.json()
                                    if data.get('err') == 0:
                                        return data
                                    print(f"❌ API still failing after re-auth: {data.get('desc')}")
            else:
                print(f"❌ API HTTP error: {response.status_code}")
        except requests.exceptions.Timeout:
            print("❌ API timeout")
        except requests.exceptions.ConnectionError:
            print("❌ API connection error")
        except Exception as e:
            print(f"❌ API exception: {e}")

        return None


# ============================== INITIALIZE API CLIENT ============================== #
dess_api = None
if DESS_USERNAME and DESS_PASSWORD and DESS_COMPANY_KEY:
    dess_api = DessMonitorAPI(
        username=DESS_USERNAME,
        password=DESS_PASSWORD,
        company_key=DESS_COMPANY_KEY,
        device_pn=DESS_DEVICE_PN or "W0040157841922",
        devcode=DESS_DEVCODE or "2451",
        devaddr=DESS_DEVADDR or "1",
        device_sn=DESS_DEVICE_SN or "96322407504037",
    )
    print("✅ DessMonitor API client initialized with credentials")
else:
    print("⚠️ DessMonitor credentials not found. Falling back to legacy URL mode.")


# ============================== LOGGING HELPERS ============================== #
def log_command(command, user_id):
    print(f"[COMMAND] {command} by user {user_id}")

def log_bot_to_user(user_id, text):
    print(f"[BOT->USER] To {user_id}: {text}")

def log_api_data(system_data):
    print(f"[API DATA] {system_data}")


# ============================== DATA FETCHING ============================== #
def get_system_data():
    """Get power system data - uses API client with auto-auth, or legacy URL as fallback."""
    global last_electricity_time, electricity_start_time, electricity_duration

    raw_data = None

    # --- Method 1: Official API with auto-auth (preferred) ---
    if dess_api:
        for attempt in range(2):
            print(f"🔄 Fetching data via API client... (Attempt {attempt + 1}/2) {datetime.datetime.now().strftime('%H:%M:%S')}")
            raw_data = dess_api.query_device_data()
            if raw_data:
                break
            if attempt == 0:
                time.sleep(1)

    # --- Method 2: Legacy URL fallback ---
    elif LEGACY_API_URL:
        for attempt in range(2):
            try:
                print(f"🔄 Fetching data via legacy URL... (Attempt {attempt + 1}/2)")
                response = requests.get(LEGACY_API_URL, timeout=15)
                if response.status_code == 200:
                    raw_data = response.json()
                    if raw_data.get('err', -1) != 0:
                        print(f"❌ Legacy API error: {raw_data.get('desc')}")
                        raw_data = None
                    else:
                        break
            except Exception as e:
                print(f"❌ Legacy API error: {e}")
            if attempt == 0:
                time.sleep(1)
    else:
        print("❌ ERROR: No API credentials or URL configured")
        return None

    if not raw_data:
        return None

    # --- Parse response (same format for both methods) ---
    try:
        params = {item['par']: item['val'] for item in raw_data['dat']['parameter']}

        system_data = {
            'battery': float(params.get('bt_battery_capacity', 0)),
            'voltage': float(params.get('bt_grid_voltage', 0)),
            'charging': float(params.get('bt_grid_voltage', 0)) > 0,
            'power_usage': float(params.get('bt_load_active_power_sole', 0)) * 1000,
            'fridge_voltage': float(params.get('bt_ac2_output_voltage', 0)),
            'charge_current': float(params.get('bt_battery_charging_current', 0))
        }

        # Update electricity tracking
        current_time_tz = datetime.datetime.now(TIMEZONE)

        if system_data['charging']:
            if electricity_start_time is None:
                electricity_start_time = current_time_tz
            last_electricity_time = current_time_tz
        else:
            if electricity_start_time is not None and last_electricity_time is not None:
                electricity_duration = last_electricity_time - electricity_start_time
            electricity_start_time = None

        print("✅ Successfully fetched and parsed data")
        log_api_data(system_data)
        return system_data

    except Exception as e:
        print(f"❌ Error parsing API response: {e}")
        return None


def format_duration(duration):
    """Format duration into readable Arabic text."""
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

    if seconds > 0 and hours == 0:
        second_text = "ثانية" if seconds == 1 else f"{seconds} ثواني" if seconds <= 10 else f"{seconds} ثانية"
        duration_parts.append(second_text)

    if not duration_parts:
        return "أقل من ثانية"

    return " و ".join(duration_parts)


# ============================== TELEGRAM COMMANDS ============================== #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    global admin_chat_id
    log_command("/start", update.effective_chat.id)
    admin_chat_id = update.effective_chat.id

    auth_status = "✅ مصادقة تلقائية (API)" if dess_api else "⚠️ وضع URL اليدوي"

    reply = (
        "مرحباً بك في بوت مراقبة نظام الطاقة! 🔋\n\n"
        f"وضع الاتصال: {auth_status}\n\n"
        "الأوامر المتاحة:\n"
        "/battery - عرض حالة النظام وبدء المراقبة التلقائية\n"
        "/stop - إيقاف المراقبة التلقائية\n"
        "/reauth - إعادة المصادقة يدوياً\n"
        "/update_api - تحديث عنوان API (الوضع اليدوي القديم)"
    )
    log_bot_to_user(update.effective_chat.id, reply)
    await update.message.reply_text(reply)


async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /battery command - show status and start monitoring."""
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
        fail_text = "⚠️ تعذر الحصول على البيانات. تحقق من السجلات أو جرّب /reauth"
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
    """Handle /stop command - stop monitoring."""
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


async def reauth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reauth command - force re-authentication."""
    global api_failure_notified, consecutive_failures
    log_command("/reauth", update.effective_chat.id)

    if not dess_api:
        await update.message.reply_text("❌ وضع المصادقة التلقائية غير مفعل. استخدم /update_api بدلاً من ذلك.")
        return

    status_msg = await update.message.reply_text("🔐 جاري إعادة المصادقة...")

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, dess_api.authenticate)

    if success:
        api_failure_notified = False
        consecutive_failures = 0
        expire_hours = (dess_api.token_expiry - time.time()) / 3600
        msg = (
            f"✅ تمت المصادقة بنجاح!\n"
            f"صلاحية التوكن: {expire_hours:.0f} ساعة\n\n"
            f"استخدم /battery لعرض الحالة وبدء المراقبة"
        )
    else:
        msg = "❌ فشلت المصادقة. تحقق من بيانات الاعتماد في config.py"

    await status_msg.edit_text(msg)


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

    # Token status indicator
    token_info = ""
    if dess_api and dess_api.token_expiry:
        remaining = dess_api.token_expiry - time.time()
        if remaining > 0:
            token_info = f"\n🔑 التوكن: صالح ({remaining/3600:.0f} ساعة متبقية)"
        else:
            token_info = "\n🔑 التوكن: منتهي (سيتم التجديد تلقائياً)"

    status_text = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {electricity_status}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}\n"
        f"⏱️ اخر توقيت لوجود الكهرباء: {electricity_time_str}"
        f"{token_info}"
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
            txt = "⚠️ تعذر الحصول على البيانات بعد 10 محاولات. جاري محاولة إعادة المصادقة..."
            log_bot_to_user(context.job.chat_id, txt)
            await context.bot.send_message(chat_id=context.job.chat_id, text=txt)

            # Try re-authenticating automatically
            if dess_api:
                reauth_success = await loop.run_in_executor(None, dess_api.authenticate)
                if reauth_success:
                    reauth_txt = "✅ تمت إعادة المصادقة تلقائياً. ستستمر المراقبة."
                    await context.bot.send_message(chat_id=context.job.chat_id, text=reauth_txt)
                    consecutive_failures = 0
                    return
                else:
                    fail_txt = "❌ فشلت إعادة المصادقة التلقائية. جرّب /reauth يدوياً."
                    await context.bot.send_message(chat_id=context.job.chat_id, text=fail_txt)

            api_failure_notified = True
            last_api_failure_time = datetime.datetime.now()
            context.job_queue.run_repeating(
                send_api_failure_reminder,
                interval=10800,
                first=10800,
                chat_id=context.job.chat_id,
                name=f"{context.job.chat_id}_reminder"
            )
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

    # --- 3. Battery 10% Change Check ---
    last_reported = old_data.get('reported_battery', new_data['battery'])

    if abs(new_data['battery'] - last_reported) >= BATTERY_CHANGE_THRESHOLD:
        await send_battery_alert(context, last_reported, new_data['battery'])
        new_data['reported_battery'] = new_data['battery']
    else:
        new_data['reported_battery'] = last_reported

    context.job.data = new_data
    print(f"🔄 Check completed - {datetime.datetime.now().strftime('%H:%M:%S')}")


async def send_api_failure_reminder(context: ContextTypes.DEFAULT_TYPE):
    global last_api_failure_time
    if last_api_failure_time:
        duration = datetime.datetime.now() - last_api_failure_time
        hours = int(duration.total_seconds() / 3600)
        txt = (
            f"🔔 تذكير: API لا يزال معطلاً منذ {hours} ساعة\n"
            "جرّب /reauth لإعادة المصادقة"
        )
        log_bot_to_user(context.job.chat_id, txt)
        await context.bot.send_message(chat_id=context.job.chat_id, text=txt)


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
        electricity_start_time = current_time
        last_electricity_time = current_time
        electricity_duration = None
        message = (
            f"✅ عادت الكهرباء! الشحن جارٍ الآن.\n"
            f"نسبة البطارية حالياً هي: {battery_level:.0f}%"
        )
    else:
        if electricity_duration is not None:
            duration_str = format_duration(electricity_duration)
            message = (
                f"⛔ انقطعت الكهرباء! يتم التشغيل على البطارية.\n"
                f"نسبة البطارية حالياً هي: {battery_level:.0f}%\n"
                f"مدة بقاء الكهرباء: {duration_str}"
            )
        else:
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
        await context.bot.send_message(chat_id=context.job.chat_id, text=message)
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


# ============================== LEGACY: API URL UPDATE COMMAND ============================== #
async def update_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update_api command - legacy fallback for manual URL mode."""
    global LEGACY_API_URL, api_failure_notified, last_api_failure_time, consecutive_failures
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
    old_url = LEGACY_API_URL
    LEGACY_API_URL = new_url

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
    else:
        LEGACY_API_URL = old_url
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
        print("❌ ERROR: TELEGRAM_TOKEN is not set.")
        return
    if not dess_api and not LEGACY_API_URL:
        print("⚠️ WARNING: No DessMonitor credentials or API URL configured.")
        print("   Set DESS_USERNAME, DESS_PASSWORD, DESS_COMPANY_KEY in config.py or env vars.")

    try:
        print("🚀 Starting the bot...")
        bot = ApplicationBuilder().token(TOKEN).build()

        bot.add_handler(CommandHandler("start", start_command))
        bot.add_handler(CommandHandler("battery", battery_command))
        bot.add_handler(CommandHandler("stop", stop_command))
        bot.add_handler(CommandHandler("reauth", reauth_command))
        bot.add_handler(CommandHandler("update_api", update_api_command))

        print("✅ Bot is ready and running...")
        bot.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ Error running the bot: {e}")
        raise e


if __name__ == "__main__":
    main()
