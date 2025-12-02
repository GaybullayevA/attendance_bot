import asyncio
import os
import json
import logging
from datetime import datetime
from pyexpat.errors import messages
import calendar

import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- НАСТРОЙКА БОТА ---
API_TOKEN = "8008942725:AAEE_Z1-CQRErZ3i2GLsuXRHhLxjNfcv9uw"
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- ПАПКИ ---
os.makedirs("data", exist_ok=True)

# --- FSM ---
class ReasonState(StatesGroup):
    waiting_for_reason = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_tashkent_day():
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.now(tz)
    return now.strftime("%A")

def get_today_filename(subject):
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    safe_subject = subject.replace(" ", "_")
    return f"data/attendance_{today}_{safe_subject}.json"

def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return default

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=3, ensure_ascii=False)

def get_attendance(date, subject):
    subject = subject.replace("-", "_")

    filename = f"data/attendance_{date}_{subject}.json"
    return load_json(filename, {})

def check_admin(tg_id: int) -> bool:
    try:
        admins = load_json("admins.json", {"admins_id": []})
        return tg_id in admins.get("admins_id", [])
    except Exception as e:
        logger.error(f"Ошибка admin.json: {e}")
        return False

# --- КНОПКИ ---
def subject_keyboard(subjects):
    keyboard = [[InlineKeyboardButton(text=s, callback_data=f"subject_{s}") ] for s in subjects]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def subject_keyboard_jurnal(subjects, date):
    date_str = date.strftime("%Y-%m-%d")
    keyboard = []

    for s in subjects:
        # Безопасный callback_data
        safe_subject = s.replace(" ", "-")
        keyboard.append([
            InlineKeyboardButton(
                text=s,
                callback_data=f"jurnalsubject_{safe_subject}_{date_str}"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def menu_keyboard():
    keyboard = [[InlineKeyboardButton(text="Отметить посещаймость", callback_data="attendance")],
                [InlineKeyboardButton(text="Журнал посещаймости", callback_data="jurnal")]]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
def student_keyboard(students, attendance):
    buttons = []
    for s in students["names"]:
        data = attendance.get(s, {"status": "absent", "reason": ""})
        status = data["status"]
        reason = data.get("reason", "")

        if status == "present":
            emoji = "✅"
        elif status == "reason":
            emoji = "📝"
        else:
            emoji = "❌"

        label = f"{emoji} {s}"
        if reason:
            label += f" ({reason})"

        row = [InlineKeyboardButton(text=label, callback_data=f"toggle_{s}")]
        row.append(InlineKeyboardButton(text="✏️ Изменить", callback_data=f"reason_{s}"))
        if reason:
            row.append(InlineKeyboardButton(text="🗑", callback_data=f"delreason_{s}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="done_marking")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def dates_keyboard(active_dates, year=None, month=None):
    # Если год и месяц не переданы — используем текущие
    today = datetime.now()
    year = year or today.year
    month = month or today.month

    month_days = calendar.monthcalendar(year, month)
    keyboard = []

    # Заголовок месяца
    keyboard.append([
        InlineKeyboardButton(text="⏪", callback_data=f"month_{year}_{month-1 if month > 1 else 12}_{year-1 if month == 1 else year}"),
        InlineKeyboardButton(text=f"{calendar.month_name[month]} {year}", callback_data="ignore"),
        InlineKeyboardButton(text="⏩", callback_data=f"month_{year}_{month+1 if month < 12 else 1}_{year+1 if month == 12 else year}")
    ])

    # Дни недели
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=d, callback_data="ignore") for d in week_days])

    # Дни месяца
    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                if any(d.strftime("%Y-%m-%d") == date_str for d in active_dates):
                    # Активный день — кликабельный
                    row.append(InlineKeyboardButton(text=str(day), callback_data=f"date_{date_str}"))
                else:
                    # Неактивный день — просто текст
                    row.append(InlineKeyboardButton(text=f"~~{day}~~", callback_data="ignore"))
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)




# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def start(message: Message):
    if not check_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав администратора.")
        return

    schedule = load_json("schedules.json", {})
    today = get_tashkent_day()
    subjects = schedule.get(today, [])

    if not subjects:
        await message.answer(f"📅 Сегодня ({today}) нет занятий.")
        return

    keyboard = menu_keyboard()
    await message.answer(f"📚 Предметы на сегодня ({today}):", reply_markup=keyboard)

@dp.callback_query(F.data == "attendance")
async def attendance(callback: CallbackQuery):
    schedule = load_json("schedules.json", {})
    today = get_tashkent_day()
    subjects = schedule.get(today, [])
    keyboard = subject_keyboard(subjects)
    await callback.message.edit_text("Выберите действие:", reply_markup=keyboard)

# --- ВЫБОР ПРЕДМЕТА ---
@dp.callback_query(F.data.startswith("subject_"))
async def choose_subject(callback: CallbackQuery):
    subject = callback.data.replace("subject_", "")
    students = load_json("students.json", {"names": []})
    filename = get_today_filename(subject)
    attendance = load_json(filename, {})

    for s in students["names"]:
        attendance.setdefault(s, {"status": "absent", "reason": ""})

    save_json(filename, attendance)
    kb = student_keyboard(students, attendance)

    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=kb)
    await callback.answer()

# --- ПЕРЕКЛЮЧЕНИЕ СТАТУСА ---
@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_attendance(callback: CallbackQuery):
    subject_line = callback.message.text.split(":")[0].replace("📘 Предмет", "").strip()
    subject = callback.message.text.split(":")[1].split("\n")[0].strip()

    student = callback.data.replace("toggle_", "")
    students = load_json("students.json", {"names": []})
    filename = get_today_filename(subject)
    attendance = load_json(filename, {})

    cur = attendance[student]["status"]
    if cur == "present":
        attendance[student] = {"status": "absent", "reason": ""}
    else:
        attendance[student] = {"status": "present", "reason": ""}

    save_json(filename, attendance)
    kb = student_keyboard(students, attendance)
    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=kb)
    await callback.answer()

# --- НАЖАТИЕ "ИЗМЕНИТЬ" ---
# --- ВЫБОР ПРИЧИНЫ ---
@dp.callback_query(F.data.startswith("reason_"))
async def ask_reason(callback: types.CallbackQuery, state: FSMContext):
    student = callback.data.replace("reason_", "")

    # извлекаем предмет из текста текущего сообщения (прямо сейчас, пока оно есть)
    text = callback.message.text
    subject = text.split(":")[1].split("\n")[0].strip() if ":" in text else "Неизвестный предмет"

    await state.update_data(student=student, subject=subject, message_id=callback.message.message_id)

    await callback.message.answer(f"✏️ Введите причину для {student}:")
    await state.set_state(ReasonState.waiting_for_reason)
    await callback.answer()

# --- СОХРАНЕНИЕ ПРИЧИНЫ ---
@dp.message(ReasonState.waiting_for_reason)
async def save_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    student = data["student"]
    subject = data["subject"]
    reason = message.text.strip()

    filename = get_today_filename(subject)
    attendance = load_json(filename, {})
    attendance[student] = {"status": "reason", "reason": reason}
    save_json(filename, attendance)

    students = load_json("students.json", {"names": []})
    kb = student_keyboard(students, attendance)

    await message.answer(f"📝 Причина для {student} сохранена: {reason}")

    # редактируем исходное сообщение
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data["message_id"],
        text=f"📘 Предмет: {subject}\nОтметьте студентов:",
        reply_markup=kb
    )

    await state.clear()

# --- УДАЛЕНИЕ ПРИЧИНЫ ---
@dp.callback_query(F.data.startswith("delreason_"))
async def delete_reason(callback: CallbackQuery):
    subject = callback.message.text.split(":")[1].split("\n")[0].strip()
    student = callback.data.replace("delreason_", "")

    filename = get_today_filename(subject)
    attendance = load_json(filename, {})
    if student in attendance:
        attendance[student]["reason"] = ""
        attendance[student]["status"] = "absent"

    save_json(filename, attendance)

    students = load_json("students.json", {"names": []})
    kb = student_keyboard(students, attendance)

    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=kb)
    await callback.answer("Причина удалена ✅")

# --- ГОТОВО ---
@dp.callback_query(F.data == "done_marking")
async def done(callback: CallbackQuery):
    await callback.message.edit_text("✅ Посещаемость сохранена!")
    await callback.answer()


@dp.callback_query(F.data == "jurnal")
async def jurnal(callback: CallbackQuery):
    files = os.listdir("data")
    dates = []

    for filename in files:
        try:
            s = filename.split("_")[1]
            date = datetime.strptime(s, "%Y-%m-%d")
            dates.append(date)
        except (IndexError, ValueError):
            continue

    dates = list(set(dates))

    # Создаём клавиатуру
    keyboard = dates_keyboard(dates)
    await callback.message.edit_text("📅 Выберите день:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("month_"))
async def change_month(callback: CallbackQuery):
    _, y, m, y2 = callback.data.split("_")
    year = int(y2)
    month = int(m)

    # Собираем активные даты снова
    files = os.listdir("data")
    dates = []
    for filename in files:
        try:
            s = filename.split("_")[1]
            date = datetime.strptime(s, "%Y-%m-%d")
            dates.append(date)
        except (IndexError, ValueError):
            continue

    keyboard = dates_keyboard(dates, year, month)
    await callback.message.edit_text("📅 Выберите день:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("date_"))
async def get_date_subject(callback: CallbackQuery):
    date_str = callback.data.split("_")[1]
    date = datetime.strptime(date_str, "%Y-%m-%d")

    schedule = load_json("schedules.json", {})
    subjects = schedule.get(date.strftime("%A"), [])

    keyboard = subject_keyboard_jurnal(subjects, date)
    await callback.message.edit_text(
        "Выберите предмет, который хотите посмотреть:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("jurnalsubject_"))
async def handle_subject(callback: CallbackQuery):
    _, subject_raw, date_str = callback.data.split("_", 2)
    subject = subject_raw.replace("_", " ")
    date = datetime.strptime(date_str, "%Y-%m-%d")

    attendance = get_attendance(date.strftime('%Y-%m-%d'), subject)

    text = f"📘 Посещаемость по предмету: {subject}\n📅 Дата: {date.strftime('%d.%m.%Y')}\n\n"

    for student, info in attendance.items():
        status = info.get("status", "")
        reason = info.get("reason", "")
        icon = (
            "✅" if status == "present"
            else "❌" if status == "absent"
            else "📝"
        )

        # Если есть причина, добавляем её в строку
        if reason:
            text += f"{student}: {icon} ({reason})\n"
        else:
            text += f"{student}: {icon}\n"

    await callback.message.edit_text(text)



# --- ЗАПУСК ---
async def main():
    logger.info("Бот запущен ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
