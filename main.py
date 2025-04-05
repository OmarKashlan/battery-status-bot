# ============================== استيراد المكتبات اللازمة ============================== #
import os                      # للوصول لمتغيرات البيئة والنظام
import requests                # للقيام بطلبات HTTP للاتصال بـ API
from telegram import Update    # لاستقبال التحديثات من تيليجرام
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue  # لبناء تطبيق بوت تيليجرام
from flask import Flask        # لإنشاء خدمة ويب بسيطة للتأكد من أن البوت يعمل
import threading               # للتشغيل المتزامن للخدمات
import time                    # للتعامل مع الوقت والتأخير

# ============================ إعدادات البوت الأساسية ============================ #
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # رمز الوصول الخاص ببوت تيليجرام
API_URL = "https://web1.shinemonitor.com/public/?sign=e12088382a5d2116290b90404a8f6848fbe13063&salt=1743701724736&token=1eb0086f32762b9328fbf0d9db78be937af5f1b3f6c84e6580539f538e14f8e0&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"
# رابط API للحصول على بيانات نظام الطاقة

# ============================ إعدادات المراقبة والعتبات ============================ #
BATTERY_CHANGE_THRESHOLD = 3   # نسبة التغير في البطارية التي تستدعي إرسال تنبيه
FRIDGE_ACTIVATION_THRESHOLD = 60  # نسبة البطارية المطلوبة لتشغيل البراد/الثلاجة
POWER_THRESHOLDS = (300, 500)  # عتبات استهلاك الطاقة (عادي، متوسط، كبير) بالواط

# ============================ خدمة Flask الخلفية للتأكد من عمل البوت ============================ #
flask_app = Flask(__name__)  # إنشاء تطبيق Flask

@flask_app.route('/')
def status_check():
    """دالة تعيد رسالة تأكيد أن البوت يعمل عند الوصول لجذر الخدمة"""
    return "✅ البوت يعمل بشكل طبيعي"

def run_flask_server():
    """دالة لتشغيل خادم Flask على بوابة محددة (تستخدم للتحقق من حالة البوت)"""
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ============================ نظام جلب البيانات من API ============================ #
def get_system_data():
    """
    دالة تقوم بجلب بيانات نظام الطاقة من API
    تعيد قاموس يحتوي على المعلومات المهمة مثل نسبة البطارية وحالة الشحن
    تعيد None في حال فشل الاتصال
    """
    try:
        response = requests.get(API_URL)  # إرسال طلب GET للحصول على البيانات
        if response.status_code == 200:   # إذا كان الاتصال ناجحاً
            data = response.json()         # تحويل البيانات من JSON إلى قاموس
            # استخراج البارامترات المهمة من البيانات المستلمة
            params = {item['par']: item['val'] for item in data['dat']['parameter']}
            
            # إرجاع قاموس يحتوي على البيانات المهمة بعد تحويلها للأنواع المناسبة
            return {
                'battery': float(params['bt_battery_capacity']),         # نسبة شحن البطارية
                'voltage': float(params['bt_grid_voltage']),             # فولتية الشبكة
                'charging': float(params['bt_grid_voltage']) > 0,        # هل البطارية تشحن
                'power_usage': float(params['bt_load_active_power_sole']) * 1000,  # استهلاك الطاقة بالواط
                'fridge_voltage': float(params['bt_ac2_output_voltage']), # فولتية خرج البراد
                'charge_current': float(params.get('bt_battery_charging_current', 0))  # تيار الشحن
            }
        return None  # في حال فشل الحصول على البيانات
    except Exception as e:
        print(f"خطأ في الاتصال: {str(e)}")
        return None

# ============================ أوامر التليجرام والتفاعل مع المستخدم ============================ #
async def battery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    دالة تعالج أمر /battery وتعرض حالة البطارية والنظام
    تبدأ أيضاً المراقبة التلقائية للتغييرات
    """
    data = get_system_data()  # جلب بيانات النظام
    
    if not data:  # إذا فشل جلب البيانات
        await send_error_message(update)
        return
    
    await send_status_message(update, data)  # إرسال رسالة بحالة النظام
    start_auto_monitoring(update, context, data)  # بدء المراقبة التلقائية

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    دالة تعالج أمر /stop وتوقف المراقبة التلقائية
    """
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    
    if jobs:
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("✅ تم إيقاف المراقبة التلقائية بنجاح.")
    else:
        await update.message.reply_text("❌ المراقبة التلقائية غير مفعلة حالياً.")

async def send_error_message(update: Update):
    """دالة لإرسال رسالة خطأ عند فشل جلب البيانات"""
    await update.message.reply_photo(
        photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",
        caption="⚠️ تعذر الحصول على البيانات، الرجاء الطلب من عمورة تحديث الخدمة "
    )

async def send_status_message(update: Update, data: dict):
    """دالة لتنسيق وإرسال رسالة بحالة النظام الحالية"""
    message = (
        f"🔋 شحن البطارية: {data['battery']:.0f}%\n"
        f"⚡ فولت الكهرباء: {data['voltage']:.2f}V\n"
        f"🔌 الكهرباء: {'موجودة ويتم الشحن✔️' if data['charging'] else 'لا يوجد كهرباء ⚠️'}\n"
        f"⚙️ استهلاك البطارية: {data['power_usage']:.0f}W ({get_consumption_status(data['power_usage'])})\n"
        f"🔌 تيار الشحن: {get_charging_status(data['charge_current'])}\n"
        f"🧊 حالة البراد: {get_fridge_status(data)}"
    )
    await update.message.reply_text(message)

# ============================ نظام المراقبة التلقائية للتغييرات ============================ #
last_power_usage = None  # متغير عام لتخزين حالة استهلاك الطاقة السابق لتجنب تكرار التنبيهات

def start_auto_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_data: dict):
    """
    دالة لبدء المراقبة التلقائية للتغييرات في حالة النظام
    تقوم بإعداد مهمة دورية تتحقق من التغييرات كل 10 ثوان
    """
    chat_id = update.effective_chat.id
    # إزالة أي مهام مراقبة سابقة لنفس المحادثة
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    
    # إضافة مهمة جديدة تشغل دالة check_for_changes كل 10 ثوان
    context.job_queue.run_repeating(
        check_for_changes,
        interval=10,     # الفاصل الزمني بين كل تحقق
        first=5,         # التأخير قبل أول تحقق
        chat_id=chat_id, # معرف المحادثة لإرسال التنبيهات
        name=str(chat_id), # اسم المهمة
        data=initial_data  # البيانات الأولية للمقارنة
    )

async def check_for_changes(context: ContextTypes.DEFAULT_TYPE):
    """
    دالة تتحقق دورياً من التغييرات في النظام وترسل التنبيهات عند الحاجة
    تقارن البيانات القديمة بالجديدة وتفحص الاختلافات المهمة
    """
    global last_power_usage  # استخدام المتغير العام

    old_data = context.job.data  # البيانات السابقة
    new_data = get_system_data() # جلب البيانات الحالية

    if not new_data:  # إذا فشل جلب البيانات الجديدة
        return
    
    current_time = time.time()  # الوقت الحالي

    # تحقق من إذا كان استهلاك الطاقة قد تجاوز العتبة
    if new_data['power_usage'] > POWER_THRESHOLDS[1]:
        # تحقق من أن الاستهلاك الجديد مختلف عن الاستهلاك السابق لتجنب الإشعارات المتكررة
        if last_power_usage is None or new_data['power_usage'] != last_power_usage:
            await send_power_alert(context, new_data['power_usage'])
            last_power_usage = new_data['power_usage']  # تحديث الاستهلاك السابق

    # تحقق من إذا كان استهلاك الطاقة قد انخفض تحت العتبة بعد أن كان كبيرًا
    elif new_data['power_usage'] <= POWER_THRESHOLDS[1] and old_data['power_usage'] > POWER_THRESHOLDS[1]:
        await send_power_reduced_alert(context, new_data['power_usage'])
        last_power_usage = None  # إعادة تعيين الاستهلاك السابق

    # إذا تغيرت حالة الشحن (انقطاع أو عودة الكهرباء)، أرسل تحذير
    if old_data['charging'] != new_data['charging']:
        await send_electricity_alert(context, new_data['charging'])
    
    # إذا تغيرت نسبة البطارية بشكل كبير، أرسل تحذير البطارية
    if abs(new_data['battery'] - old_data['battery']) >= BATTERY_CHANGE_THRESHOLD:
        await send_battery_alert(context, old_data['battery'], new_data['battery'])

    context.job.data = new_data  # تحديث البيانات القديمة بالبيانات الجديدة للمقارنة التالية

# ============================ دوال إرسال التنبيهات ============================ #
async def send_power_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """دالة لإرسال تنبيه عند ارتفاع استهلاك الطاقة بشكل كبير"""
    message = f"⚠️ تحذير! استهلاك الطاقة كبير جدًا: {power_usage:.0f}W"
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_power_reduced_alert(context: ContextTypes.DEFAULT_TYPE, power_usage: float):
    """دالة لإرسال إشعار عند انخفاض استهلاك الطاقة بعد أن كان مرتفعاً"""
    message = f"👍 تم خفض استهلاك الطاقة إلى {power_usage:.0f}W."
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_electricity_alert(context: ContextTypes.DEFAULT_TYPE, is_charging: bool):
    """دالة لإرسال تنبيه عند انقطاع الكهرباء أو عودتها"""
    message = "⚡ عادت الكهرباء! الشحن جارٍ الآن." if is_charging else "⚠️ انقطعت الكهرباء! يتم التشغيل على البطارية."
    await context.bot.send_message(chat_id=context.job.chat_id, text=message)

async def send_battery_alert(context: ContextTypes.DEFAULT_TYPE, old_value: float, new_value: float):
    """دالة لإرسال تنبيه عند تغير نسبة البطارية بشكل كبير"""
    arrow = "⬆️ زيادة" if new_value > old_value else "⬇️ انخفاض"
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"{arrow}\nالشحن: {old_value:.0f}% → {new_value:.0f}%"
    )

# ============================ دوال مساعدة لتحديد حالة النظام ============================ #
def get_charging_status(current: float) -> str:
    """
    دالة تحدد حالة شحن البطارية بناءً على التيار
    تعيد وصف حالة الشحن مع الرمز المناسب
    """
    if current >= 60:
        return f"{current:.1f}A (الشحن سريع جداً 🔴)"
    elif 30 <= current < 60:
        return f"{current:.1f}A (الشحن سريع 🟡)"
    elif 1 <= current < 30:
        return f"{current:.1f}A (الشحن طبيعي 🟢)"
    return f"{current:.1f}A (لا يوجد شحن ⚪)"

def get_fridge_status(data: dict) -> str:
    """
    دالة تحدد حالة البراد (الثلاجة) بناءً على حالة البطارية والكهرباء
    
    آلية الحساب:
    - إذا كانت نسبة البطارية > العتبة: البراد يعمل بشكل طبيعي
    - إذا كانت نسبة البطارية < العتبة لكن فولتية البراد > 0 والكهرباء غير متصلة:
      * يتم حساب الوقت المتبقي التقريبي بناءً على استهلاك الطاقة الحالي
      * نفترض أن كل 1% من البطارية يعادل حوالي 30 واط ساعة (قيمة تقريبية)
    """
    if data['battery'] > FRIDGE_ACTIVATION_THRESHOLD:
        return "يعمل ✅"
    elif data['battery'] < FRIDGE_ACTIVATION_THRESHOLD and data['fridge_voltage'] > 0 and not data['charging']:
        if data['power_usage'] > 0:
            # حساب وقت التشغيل المتبقي بطريقة محسنة
            # افتراض أن كل 1% من البطارية يعادل 30 واط ساعة (قيمة تقريبية)
            battery_watt_hours = data['battery'] * 30
            # استخدام 80% فقط من سعة البطارية للتشغيل
            usable_watt_hours = battery_watt_hours * 0.8
            hours = usable_watt_hours / data['power_usage']
            return f"يعمل ({int(hours)}h {int((hours*60)%60)}m) ⏳"
        return "يعمل (وقت غير محدد) ⚠️"
    return "مطفئ ⛔"

def get_consumption_status(power: float) -> str:
    """
    دالة تحدد حالة استهلاك الطاقة بناءً على العتبات المحددة
    تعيد وصف حالة الاستهلاك مع الرمز المناسب
    """
    if power <= POWER_THRESHOLDS[0]:
        return "عادي 🟢"
    elif POWER_THRESHOLDS[0] < power <= POWER_THRESHOLDS[1]:
        return "متوسط 🟡"
    return "كبير 🔴"

# ============================ التشغيل الرئيسي للبوت ============================ #
def main():
    """
    الدالة الرئيسية التي تشغل البوت وتعد جميع المكونات
    تقوم بإعداد البوت وإضافة الأوامر وبدء خادم Flask وتشغيل البوت
    """
    bot = ApplicationBuilder().token(TOKEN).build()  # إنشاء تطبيق البوت باستخدام التوكن
    bot.add_handler(CommandHandler("battery", battery_command))  # إضافة معالج لأمر /battery
    bot.add_handler(CommandHandler("stop", stop_command))  # إضافة معالج لأمر /stop
    threading.Thread(target=run_flask_server).start()  # تشغيل خادم Flask في خيط منفصل
    bot.run_polling()  # بدء استطلاع تحديثات تيليجرام

if __name__ == "__main__":
    main()  # تشغيل الدالة الرئيسية عند تنفيذ البرنامج مباشرة
