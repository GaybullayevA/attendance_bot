import asyncio
import calendar
import json
import logging
import os
from datetime import datetime
from gc import callbacks
from typing import Any, Dict, List

import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery, ReplyKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from urllib.parse import quote, unquote

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# Config
# ---------------------------
API_TOKEN = "7770394551:AAFUOqcwVBJEz798P5MBAfwR-OR466vXqSM"
TIMEZONE = "Asia/Tashkent"
DATA_DIR = "data"

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------
# Bot init
# ---------------------------
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
# ---------------------------
# FSM
# ---------------------------
class ReasonState(StatesGroup):
    waiting_for_reason = State()

class Steps(StatesGroup):
    attendance = State()
    choose_subject = State()
    jurnal = State()


# ---------------------------
# Helpers: file I/O, time
# ---------------------------
def tz_now() -> datetime:
    tz = pytz.timezone(TIMEZONE)
    return datetime.now(tz)


def get_tashkent_weekday() -> str:
    """Возвращает день недели (English) в зоне Asia/Tashkent, например 'Monday'."""
    return tz_now().strftime("%A")


def safe_filename_subject(subject: str) -> str:
    """Подготовить subject для файла (без пробелов и спецсимволов)"""
    return quote(subject, safe="")


def unsafe_subject_from_token(token: str) -> str:
    """Восстановить subject из callback-data токена."""
    return unquote(token)


def get_today_filename(subject: str) -> str:
    date = tz_now().strftime("%Y-%m-%d")
    safe_subj = safe_filename_subject(subject)
    return os.path.join(DATA_DIR, f"attendance_{date}_{safe_subj}.json")

async def send_message_admins(text: str):
    """Отправка сообщения всем администраторам из admins.json."""
    try:
        with open("admins.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        admin_ids = data.get("admins_id", [])
        admin_names = data.get("admins_name", [])

        if not admin_ids:
            logging.warning("⚠️ admins.json fayli bo'sh yoki admin_ids mavjud emas")
            return

        # Подстраховка, если списки не совпадают по длине
        while len(admin_names) < len(admin_ids):
            admin_names.append("Unknown")

        for telegram_id, admin_name in zip(admin_ids, admin_names):
            try:
                await bot.send_message(telegram_id, text)
                logging.info(f"📤 Xisobot adminlarga yuborildi {admin_name} ({telegram_id})")
            except Exception as send_error:
                logging.error(f"⚠️ Adminlarga yuborishda xatolik {admin_name} ({telegram_id}): {send_error}")

    except Exception as e:
        logging.exception(f"admins.json faylini o'qishda xatolik: {e}")


def load_json(file_path, default=None):
    """Безопасная загрузка JSON файла."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"{file_path} fayli topilmadi. Standart qiymat qo'llanilmoqda.")
        return default if default is not None else {}
    except json.JSONDecodeError as e:
        logger.error(f"{file_path} faylini o'qishda xatolik: {e}")
        return default if default is not None else {}


def save_json(filename: str, data: Any) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_attendance(date_str, subject):
    """Получение данных посещаемости."""
    path = f"data/attendance_{date_str}_{subject}.json"
    if not os.path.exists(path):
        logger.warning(f"Davomat fayli {path} topilmadi.")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Davomat ma'lumotlarini yuklashda xatolik: {e}")
        return {}


def check_admin(tg_id: int) -> bool:
    try:
        cfg = load_json("admins.json", {"admins_id": []})
        return tg_id in cfg.get("admins_id", [])
    except Exception as e:
        logger.exception("admins.json faylini o'qishda xatolik")
        return False

def check_teacher(tg_id: int) -> bool:
    try:
        cfg = load_json("teachers.json", {"teacher_id": []})
        return tg_id in cfg.get("teacher_id", [])
    except Exception as e:
        logger.exception("teachers.json faylini o'qishda xatolik")
        return False

# ---------------------------
# Keyboards
# ---------------------------
def menu_keyboard() -> List:
    kb = [
        [InlineKeyboardButton(text="Davomat jurnali", callback_data="jurnal")]
    ]
    return kb


def subject_keyboard(subjects: List[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора предмета для отметки (callback содержит закодированный subject)."""
    buttons = [
        [InlineKeyboardButton(text=s, callback_data=f"subject_{quote(s, safe='')}")] for s in subjects
    ]
    buttons.append([InlineKeyboardButton(text="◀️Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def student_keyboard(students: Dict[str, List[str]], attendance: Dict[str, Dict[str, str]]) -> InlineKeyboardMarkup:
    """
    Talabalar uchun davomat belgilash tugmalari.
    Holatlar: present, absent, reason, late
    """
    rows = []
    for s in students.get("names", []):
        data = attendance.get(s, {"status": "absent", "reason": ""})
        status = data.get("status", "absent")
        reason = data.get("reason", "")

        if status == "present":
            emoji = "✅"
        elif status == "late":
            emoji = "⏰"
        elif status == "reason":
            emoji = "📝"
        else:
            emoji = "❌"

        label = f"{emoji} {s}"
        if reason:
            label += f" ({reason})"

        # Asosiy tugmalar
        row = [
            InlineKeyboardButton(text=label, callback_data=f"toggle_{quote(s, safe='')}"),
            InlineKeyboardButton(text="⏰ Kech keldi", callback_data=f"late_{quote(s, safe='')}"),
            InlineKeyboardButton(text="✏️ Sabab qoʻshish", callback_data=f"reason_{quote(s, safe='')}"),
        ]

        rows.append(row)

    rows.append([InlineKeyboardButton(text="✅ Tayyor", callback_data="done_marking"), InlineKeyboardButton(text="◀️Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def subject_keyboard_journal(subjects: List[str], date: datetime) -> InlineKeyboardMarkup:
    """Клавиатура списка предметов для выбранной даты (callback содержит закодированный subject и дату)."""
    date_str = date.strftime("%Y-%m-%d")
    rows = []
    for s in subjects:
        token = quote(s, safe="")
        rows.append([InlineKeyboardButton(text=s, callback_data=f"jurnalsubject_{token}_{date_str}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dates_keyboard(active_dates: List[datetime], year: int = None, month: int = None) -> InlineKeyboardMarkup:
    now = tz_now()
    year = year or now.year
    month = month or now.month

    month_days = calendar.monthcalendar(year, month)
    keyboard = []

    # header navigation
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    keyboard.append([
        InlineKeyboardButton(text="⏪", callback_data=f"month_{prev_year}_{prev_month}"),
        InlineKeyboardButton(text=f"{calendar.month_name[month]} {year}", callback_data="noop"),
        InlineKeyboardButton(text="⏩", callback_data=f"month_{next_year}_{next_month}"),
    ])

    # week days
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=d, callback_data="noop") for d in week_days])

    # prepare active dates
    active_set = {d.strftime("%Y-%m-%d") for d in active_dates}

    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                in_active = date_str in active_set

                if in_active:
                    row.append(InlineKeyboardButton(text=f"{day:2d}", callback_data=f"date_{date_str}"))
                else:
                    row.append(InlineKeyboardButton(text="".join(ch + "\u0336" for ch in f"{day}"), callback_data="noop"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text="◀️Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)



# ---------------------------
# Handlers
# ---------------------------
@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    chck_admim =  check_admin(message.from_user.id)
    chck_teacher = check_teacher(message.from_user.id)

    if not chck_admim and not chck_teacher:
        await message.answer("🚫 Sizda kerakli huquqlari yo'q.")
        return

    today_name = get_tashkent_weekday()
    await state.clear()
    menu_kb = menu_keyboard()
    if chck_admim:
        menu_kb.insert(0, [InlineKeyboardButton(text="Davomat qilish", callback_data="attendance")])
        await message.answer(f"📚 Bugungi kun ({today_name}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=menu_kb))
    elif chck_teacher:
        await message.answer(f"📚 Bugungi kun ({today_name}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=menu_kb))

@dp.callback_query(F.data == "attendance")
async def attendance(callback: CallbackQuery, state: FSMContext):
    schedule = load_json("schedules.json", {})
    today_name = get_tashkent_weekday()
    subjects = schedule.get(today_name, [])
    await state.set_state(Steps.attendance)
    await callback.message.edit_text("Fanlardan birini tanlang:", reply_markup=subject_keyboard(subjects))
    await callback.answer()


@dp.callback_query(F.data.startswith("subject_"))
async def choose_subject(callback: CallbackQuery, state: FSMContext):
    token = callback.data.replace("subject_", "", 1)
    subject = unsafe_subject_from_token(token)
    await state.set_state(Steps.choose_subject)
    students = load_json("students.json", {"names": []})
    filename = get_today_filename(subject)
    attendance = load_json(filename, {})
    # assure everyone exists
    for s in students.get("names", []):
        attendance.setdefault(s, {"status": "absent", "reason": ""})

    save_json(filename, attendance)
    await callback.message.edit_text(f"📘 Fan: {subject}\nStudentlarni bergilang:", reply_markup=student_keyboard(students, attendance))
    await callback.answer()


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_attendance(callback: CallbackQuery):
    # Получаем subject из текста сообщения (оно формируется в choose_subject)
    # Ожидается формат "📘 Предмет: {subject}\nОтметьте студентов:"
    header = callback.message.text.split("\n", 1)[0]
    if ":" in header:
        subject = header.split(":", 1)[1].strip()
    else:
        await callback.answer("Fanni aniqlab bo'lmadi.", show_alert=True)
        return

    student_token = callback.data.replace("toggle_", "", 1)
    student = unsafe_subject_from_token(student_token)

    filename = get_today_filename(subject)
    attendance = load_json(filename, {})

    cur = attendance.get(student, {"status": "absent"}).get("status", "absent")
    if cur == "present":
        attendance[student] = {"status": "absent", "reason": ""}
    else:
        attendance[student] = {"status": "present", "reason": ""}

    save_json(filename, attendance)
    students = load_json("students.json", {"names": []})
    await callback.message.edit_text(f"📘 Fan: {subject}\nStudentlarni belgilang:", reply_markup=student_keyboard(students, attendance))
    await callback.answer()

@dp.callback_query(F.data.startswith("back"), StateFilter(Steps.attendance, Steps.jurnal))
async def back(callback: CallbackQuery, state: FSMContext, ):
    await state.clear()
    chck_admim = check_admin(callback.from_user.id)
    chck_teacher = check_teacher(callback.from_user.id)

    if not chck_admim and not chck_teacher:
        await callback.message.edit_text("🚫 Sizda kerakli huquqlari yo'q.")
        return

    today_name = get_tashkent_weekday()
    menu_kb = menu_keyboard()
    if chck_admim:
        menu_kb.insert(0, [InlineKeyboardButton(text="Davomat qilish", callback_data="attendance")])
        await callback.message.edit_text(f"📚 Bugungi darslar ({today_name}):",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=menu_kb))
    elif chck_teacher:
        await callback.message.edit_text(f"📚 Bugungi darslar ({today_name}):",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=menu_kb))


@dp.callback_query(F.data.startswith("back"), StateFilter(Steps.choose_subject))
async def back(callback: CallbackQuery, state: FSMContext, ):
    await attendance(callback=callback, state=state)
@dp.callback_query(F.data.startswith("reason_"))
async def ask_reason(callback: CallbackQuery, state: FSMContext):
    student_token = callback.data.replace("reason_", "", 1)
    student = unsafe_subject_from_token(student_token)

    # извлекаем предмет из текущего сообщения
    header = callback.message.text.split("\n", 1)[0]
    subject = header.split(":", 1)[1].strip() if ":" in header else "Unknown"

    await state.update_data(student=student, subject=subject, message_id=callback.message.message_id)
    await callback.message.answer(f"✏️ {student} uchun sababni kiriting:")
    await state.set_state(ReasonState.waiting_for_reason)
    await callback.answer()


@dp.message(ReasonState.waiting_for_reason)
async def save_reason(message: Message, state: FSMContext):
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

    await message.answer(f"📝 {student} uchun sabab saqlandi: {reason}")
    # редактируем исходное сообщение, передавая ранее сохранённые chat_id/message_id
    await bot.edit_message_text(chat_id=message.chat.id, message_id=data["message_id"],
                                text=f"📘 Fan: {subject}\nStudentlarni belgilang:", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data.startswith("late_"))
async def mark_late(callback: CallbackQuery):
    header = callback.message.text.split("\n", 1)[0]
    subject = header.split(":", 1)[1].strip() if ":" in header else "Unknown"

    student_token = callback.data.replace("late_", "", 1)
    student = unsafe_subject_from_token(student_token)

    filename = get_today_filename(subject)
    attendance = load_json(filename, {})

    attendance[student] = {"status": "late", "reason": ""}

    save_json(filename, attendance)
    students = load_json("students.json", {"names": []})

    await callback.message.edit_text(
        f"📘 Fan: {subject}\nStudentlarni belgilang:",
        reply_markup=student_keyboard(students, attendance)
    )
    await callback.answer(f"⏰ {student} kechikkan sifatida belgilandi.")

@dp.callback_query(F.data == "done_marking")
async def done(callback: CallbackQuery):
    try:
        # Извлекаем предмет из текста сообщения
        text = callback.message.text
        subject = text.split(":")[1].split("\n")[0].strip() if ":" in text else "Noma'lum fan"

        # Загружаем данные посещаемости
        filename = get_today_filename(subject)
        attendance = load_json(filename, {})

        # Формируем красивый отчёт
        date_str = datetime.now().strftime("%d.%m.%Y")
        report = f"📘 Davomat yakunlandi\n📚 Fan: {subject}\n📅 Sana: {date_str}\n\n"

        present = [s for s, info in attendance.items() if info.get("status") == "present"]
        absent = [s for s, info in attendance.items() if info.get("status") == "absent"]
        reasoned = [f"{s} — {info.get('reason')}" for s, info in attendance.items() if info.get("status") == "reason"]
        late = [s for s, info in attendance.items() if info.get("status") == "late"]

        report += f"✅ Darsda bo'lganlar ({len(present)}):\n" + ("\n".join(present) if present else "—") + "\n\n"
        report += f"❌ Darsda bo'lmaganlar ({len(absent)}):\n" + ("\n".join(absent) if absent else "—") + "\n\n"
        report += f"📝 Sabablilar ({len(reasoned)}):\n" + ("\n".join(reasoned) if reasoned else "—") + "\n\n"
        report += f"⏰ Kech qolganlar: ({len(late)}):\n" + ("\n".join(late) if late else "—")

        # Отправляем сообщение пользователю
        await callback.message.edit_text("✅ Davomat saqlandi va adminlarga yuborildi!")
        await callback.answer()

        # Отправляем отчёт администраторам
        await send_message_admins(report)

    except Exception as e:
        logging.exception(f"Ошибка при завершении отметки посещаемости: {e}")
        await callback.message.answer("❌ Davomatni saqlashda xatolik yuz berdi. Qaytadan urinib ko'ring.")


@dp.callback_query(F.data == "jurnal")
async def jurnal(callback: CallbackQuery, state: FSMContext):
    files = os.listdir(DATA_DIR)
    dates = []
    for filename in files:
        try:
            # ожидаем формат attendance_YYYY-MM-DD_<subject>.json
            parts = filename.split("_")
            if len(parts) >= 2:
                s = parts[1]
                date = datetime.strptime(s, "%Y-%m-%d")
                dates.append(date)
        except Exception:
            continue

    dates = sorted({d.date() for d in dates})
    keyboard = dates_keyboard([datetime.combine(d, datetime.min.time()) for d in dates])
    await state.set_state(Steps.jurnal)
    await callback.message.edit_text("📅 Kunni tanlang:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("month_"))
async def change_month(callback: CallbackQuery):
    _, year_str, month_str = callback.data.split("_", 2)
    year = int(year_str)
    month = int(month_str)

    files = os.listdir(DATA_DIR)
    dates = []
    for filename in files:
        try:
            parts = filename.split("_")
            if len(parts) >= 2:
                s = parts[1]
                date = datetime.strptime(s, "%Y-%m-%d")
                dates.append(date)
        except Exception:
            continue

    keyboard = dates_keyboard([d for d in dates], year=year, month=month)
    await callback.message.edit_text("📅 Kunni tanlang:", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("date_"))
async def get_date_subject(callback: CallbackQuery):
    date_str = callback.data.replace("date_", "", 1)
    date = datetime.strptime(date_str, "%Y-%m-%d")

    schedule = load_json("schedules.json", {})
    subjects = schedule.get(date.strftime("%A"), [])

    await callback.message.edit_text("Ko'rmoqchi bo'lgan fanni tanlang:",
                                    reply_markup=subject_keyboard_journal(subjects, date))
    await callback.answer()


@dp.callback_query(F.data.startswith("jurnalsubject_"))
async def handle_subject(callback: CallbackQuery):
    """Обработка выбора предмета для журнала."""
    try:
        # Безопасный парсинг данных
        parts = callback.data.split("_", 2)
        if len(parts) < 3:
            await callback.answer("❌ Tugma ma'lumotlari noto'g'ri.", show_alert=True)
            logger.error(f"Некорректный callback_data: {callback.data}")
            return

        _, subject_raw, date_str = parts


        # Проверка формата даты
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await callback.answer("❌ Sana formati noto'g'ri.", show_alert=True)
            logger.error(f"Ошибка парсинга даты: {date_str}")
            return

        # Загрузка посещаемости
        attendance = get_attendance(date.strftime('%Y-%m-%d'), subject_raw)
        subject = subject_raw.replace("%20", " ")
        if not attendance:
            await callback.message.edit_text(
                f"📘 Fan: {subject}\n📅 Sana: {date.strftime('%d.%m.%Y')}\n\nDavomat bo'yicha ma'lumot mavjud emas."
            )
            return

        # Формирование текста
        text_lines = [
            f"📘 Fan bo'yicha davomat: {subject}",
            f"📅 Sana: {date.strftime('%d.%m.%Y')}",
            "",
        ]

        for student, info in attendance.items():
            status = info.get("status", "unknown")
            icon = "✅" if status == "present" else "❌" if status == "absent" else "📝"
            reason = info.get("reason", "")
            reason_text = f" — {reason}" if reason else ""
            text_lines.append(f"{student}: {icon}{reason_text}")

        text = "\n".join(text_lines)

        # Отправляем обновлённый текст
        await callback.message.edit_text(text)

    except Exception as e:
        logger.exception("Ошибка при обработке предмета:")
        await callback.answer("⚠️ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.", show_alert=True)


# ---------------------------
# Run
# ---------------------------
async def main():
    logger.info("Bot ishga tushdi ✅")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())