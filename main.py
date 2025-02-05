import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from flask import Flask
import threading

# إعدادات البوت
TOKEN = "7715192868:AAF5b5I0mfWBIuVc34AA6U6sEBt2Sb0PC6M"  # ضع توكن البوت الخاص بك هنا
API_URL = "https://web1.shinemonitor.com/public/?sign=e35fa2a0e99e6d7d3b4e8656cb385335a19769e7&salt=1738750708040&token=4ac3905ae2ff1552a9fb7cdf3b13c1a1a2c0211dd7549860284355099d3a17f3&action=queryDeviceParsEs&source=1&devcode=2451&pn=W0040157841922&devaddr=1&sn=96322407504037&i18n=en_US"

# المتغيرات لتخزين القيم السابقة
previous_battery = None
previous_voltage = None
previous_charging = None
previous_power = None
previous_ac2_voltage = None  # لتخزين الفولت الخاص بالبراد

# خادم Flask لضمان استمرارية التشغيل
app = Flask(__name__)

@app.route('/')
def home():
    return "The bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# دالة لجلب بيانات البطارية من API
def fetch_battery_data():
    try:
        response = requests.get(API_URL)
        if response.status_code == 200:
            data = response.json()
            parameters = data['dat']['parameter']

            # استخراج القيم المطلوبة
            battery_capacity = float(next(item['val'] for item in parameters if item['par'] == 'bt_battery_capacity'))
            grid_voltage = float(next(item['val'] for item in parameters if item['par'] == 'bt_grid_voltage'))
            active_power_kw = float(next(item['val'] for item in parameters if item['par'] == 'bt_load_active_power_sole'))
            ac2_voltage = float(next(item['val'] for item in parameters if item['par'] == 'bt_ac2_output_voltage'))  # مخرج البراد
            charging_current = float(next(item['val'] for item in parameters if item['par'] == 'bt_battery_charging_current', 0.0))  # تيار الشحن

            # تحويل الطاقة إلى W
            active_power_w = active_power_kw * 1000

            # تحديد حالة الشحن
            charging = grid_voltage > 0.0

            return battery_capacity, grid_voltage, charging, active_power_w, ac2_voltage, charging_current
        else:
            return None, None, None, None, None, None
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None, None, None, None, None, None

# دالة /battery لعرض البيانات ومراقبة الشحن
async def battery_and_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, previous_ac2_voltage
    chat_id = update.effective_chat.id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w, ac2_voltage, charging_current = fetch_battery_data()

    if current_battery is not None:
        # حساب الوقت المتبقي بناءً على استهلاك البراد
        if current_battery > 70:  # البطارية أكثر من 70%، البراد يعمل بغض النظر عن الكهرباء
            remaining_time_message = "البراد يعمل الآن"
        elif current_battery < 70 and ac2_voltage > 0 and not charging:  # إذا كانت البطارية أقل من 70% والبراد يعمل
            # تقدير الوقت المتبقي بالساعة
            remaining_time_hours = (current_battery * 0.8 * 1000) / active_power_w  # حساب الوقت المتبقي باستخدام السعة
            remaining_time_minutes = (remaining_time_hours * 60) % 60
            remaining_time_hours = int(remaining_time_hours)
            remaining_time_minutes = int(remaining_time_minutes)
            remaining_time_message = f"البراد يعمل الآن, المتبقي له: {remaining_time_hours} ساعة و {remaining_time_minutes} دقيقة"
        elif current_battery < 70:  # البطارية أقل من 70%
            remaining_time_message = "البراد متوقف الآن لأن البطارية أقل من 70%"
        elif charging:  # إذا كان الشحن قيد التشغيل
            remaining_time_message = "البراد يعمل لأنه يوجد كهرباء"
        else:
            remaining_time_message = "البراد غير قادر على العمل بسبب نقص الشحن"

        charging_status = "يوجد كهرباء ✔️ ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."
        
        # تعديل رسائل الفولت والاستهلاك
        grid_voltage_message = f"⚡ فولت الكهرباء: {grid_voltage:.2f}V" if grid_voltage > 0 else "⚡ فولت الكهرباء: 0.00V, لا يوجد كهرباء حالياً"
        active_power_message = f"⚙️ استهلاك البطارية: {active_power_w:.0f}W" if active_power_w > 0 else "⚙️ استهلاك البطارية: 0W, لا يوجد استهلاك حالياً"

        # حساب حالة الشحن بناءً على تيار الشحن
        if charging_current == 0:
            charging_speed = "لا يوجد كهرباء حالياً"
        elif 1 <= charging_current < 30:
            charging_speed = "الشحن طبيعي"
        elif 30 <= charging_current < 60:
            charging_speed = "الشحن سريع"
        else:
            charging_speed = "الشحن سريع جداً"

        message = (
            f"🔋 نسبة شحن البطارية: {current_battery:.0f}%\n"
            f"{grid_voltage_message}\n"
            f"🔌 حالة الشحن: {charging_status}\n"
            f"⚙️ استهلاك البطارية: {active_power_w:.0f}W\n"
            f"🔋 تيار الشحن: {charging_current:.2f}A, {charging_speed}\n"
            f"🧊 وضع البراد :{remaining_time_message}"
        )
        await update.message.reply_text(message)

        # حفظ القيم الحالية للمراقبة
        if previous_battery is None or previous_voltage is None or previous_charging is None or previous_power is None or previous_ac2_voltage is None:
            previous_battery = current_battery
            previous_voltage = grid_voltage
            previous_charging = charging
            previous_power = active_power_w
            previous_ac2_voltage = ac2_voltage

        # إعداد المهام المتكررة
        job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in job_removed:
            job.schedule_removal()

        context.job_queue.run_repeating(
            monitor_battery,
            interval=10,  # تحديث كل 10 ثوانٍ
            first=5,
            chat_id=chat_id,
            name=str(chat_id)
        )

    else:
        await update.message.reply_photo(
        photo="https://i.ibb.co/Sd57f0d/Whats-App-Image-2025-01-20-at-23-04-54-515fe6e6.jpg",  # ضع مسار الصورة أو رابط URL للصورة
        caption="⚠️ فشل في الحصول على بيانات البطارية, يرجى الطلب من عمر تحديث الخدمة."
    )

# دالة مراقبة البطارية بشكل دوري
async def monitor_battery(context: ContextTypes.DEFAULT_TYPE):
    global previous_battery, previous_voltage, previous_charging, previous_power, previous_ac2_voltage
    job = context.job
    chat_id = job.chat_id

    # جلب البيانات الحالية
    current_battery, grid_voltage, charging, active_power_w, ac2_voltage = fetch_battery_data()

    if current_battery is not None:
        charging_status = "يوجد كهرباء 🔌 ويتم الشحن حالياً." if charging else "لا يوجد كهرباء 🔋 والشحن متوقف."

        # تحذير عند انخفاض الفولت إلى 168V أو أقل
        if grid_voltage <= 168.0 and grid_voltage != previous_voltage:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تحذير: انخفض فولت الكهرباء إلى {grid_voltage:.2f}V!"
            )
            previous_voltage = grid_voltage

        # تنبيه عند أي تغيير بنسبة 1%
        if abs(current_battery - previous_battery) >= 3:
            change = "زاد" if current_battery > previous_battery else "انخفض"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تنبيه: {change} شحن البطارية إلى {current_battery:.0f}%!"
            )
            previous_battery = current_battery

        # تنبيه إذا تغيرت حالة الشحن
        if charging != previous_charging:
            status = "⚡ عادت الكهرباء! الشحن مستمر." if charging else "⚠️ انقطعت الكهرباء! الشحن متوقف."
            await context.bot.send_message(chat_id=chat_id, text=status)
            previous_charging = charging


# دالة لإيقاف المراقبة
async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_removed = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in job_removed:
        job.schedule_removal()

    await update.message.reply_text("⏹️ تم إيقاف مراقبة البطارية.")

# إعداد البوت
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("battery", battery_and_monitor))
    app.add_handler(CommandHandler("stop", stop_monitoring))

    job_queue = app.job_queue

    # بدء تشغيل Flask في خيط منفصل
    threading.Thread(target=run_flask).start()

    app.run_polling()

if __name__ == "__main__":
    main()
