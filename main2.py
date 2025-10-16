import asyncio
import calendar
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
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
API_TOKEN = "8008942725:AAEE_Z1-CQRErZ3i2GLsuXRHhLxjNfcv9uw"
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
            logging.warning("⚠️ Файл admins.json пуст или не содержит admin_ids")
            return

        # Подстраховка, если списки не совпадают по длине
        while len(admin_names) < len(admin_ids):
            admin_names.append("Unknown")

        for telegram_id, admin_name in zip(admin_ids, admin_names):
            try:
                await bot.send_message(telegram_id, text)
                logging.info(f"📤 Отчёт отправлен админу {admin_name} ({telegram_id})")
            except Exception as send_error:
                logging.error(f"⚠️ Ошибка при отправке админу {admin_name} ({telegram_id}): {send_error}")

    except Exception as e:
        logging.exception(f"Ошибка при чтении файла admins.json: {e}")


def load_json(file_path, default=None):
    """Безопасная загрузка JSON файла."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Файл {file_path} не найден. Возвращаю default.")
        return default if default is not None else {}
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка чтения JSON из {file_path}: {e}")
        return default if default is not None else {}


def save_json(filename: str, data: Any) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_attendance(date_str, subject):
    """Получение данных посещаемости."""
    path = f"data/attendance_{date_str}_{subject}.json"
    if not os.path.exists(path):
        logger.warning(f"Файл посещаемости {path} не найден.")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки посещаемости: {e}")
        return {}


def check_admin(tg_id: int) -> bool:
    try:
        cfg = load_json("admins.json", {"admins_id": []})
        return tg_id in cfg.get("admins_id", [])
    except Exception as e:
        logger.exception("Error reading admins.json")
        return False


# ---------------------------
# Keyboards
# ---------------------------
def menu_keyboard() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="Отметить посещаемость", callback_data="attendance")],
        [InlineKeyboardButton(text="Журнал посещаемости", callback_data="jurnal")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def subject_keyboard(subjects: List[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора предмета для отметки (callback содержит закодированный subject)."""
    buttons = [
        [InlineKeyboardButton(text=s, callback_data=f"subject_{quote(s, safe='')}")] for s in subjects
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def student_keyboard(students: Dict[str, List[str]], attendance: Dict[str, Dict[str, str]]) -> InlineKeyboardMarkup:
    """
    students: {"names": [...]}
    attendance: { student_name: {"status": "...", "reason": "..."} }
    """
    rows = []
    for s in students.get("names", []):
        data = attendance.get(s, {"status": "absent", "reason": ""})
        status = data.get("status", "absent")
        reason = data.get("reason", "")

        emoji = "✅" if status == "present" else "📝" if status == "reason" else "❌"
        label = f"{emoji} {s}"
        if reason:
            label += f" ({reason})"

        # кнопки: переключить, изменить причину, удалить причину (если есть)
        row = [
            InlineKeyboardButton(text=label, callback_data=f"toggle_{quote(s, safe='')}"),
            InlineKeyboardButton(text="✏️", callback_data=f"reason_{quote(s, safe='')}")
        ]
        if reason:
            row.append(InlineKeyboardButton(text="🗑", callback_data=f"delreason_{quote(s, safe='')}"))

        rows.append(row)

    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="done_marking")])
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
    """
    Рисует календарь для month/year. active_dates — список datetime объектов, которые будем считать активными.
    """
    now = tz_now()
    year = year or now.year
    month = month or now.month

    month_days = calendar.monthcalendar(year, month)
    keyboard = []

    # header with navigation
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    keyboard.append([
        InlineKeyboardButton(text="⏪", callback_data=f"month_{prev_year}_{prev_month}"),
        InlineKeyboardButton(text=f"{calendar.month_name[month]} {year}", callback_data="ignore"),
        InlineKeyboardButton(text="⏩", callback_data=f"month_{next_year}_{next_month}"),
    ])

    # week days
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=d, callback_data="ignore") for d in week_days])

    active_set = {d.strftime("%Y-%m-%d") for d in active_dates}

    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                if date_str in active_set:
                    row.append(InlineKeyboardButton(text=str(day), callback_data=f"date_{date_str}"))
                else:
                    row.append(InlineKeyboardButton(text=f"·{day}·", callback_data="ignore"))
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ---------------------------
# Handlers
# ---------------------------
@dp.message(Command("start"))
async def start(message: Message):
    if not check_admin(message.from_user.id):
        await message.answer("🚫 У вас нет прав администратора.")
        return

    schedule = load_json("schedules.json", {})
    today_name = get_tashkent_weekday()
    subjects = schedule.get(today_name, [])

    if not subjects:
        await message.answer(f"📅 Сегодня ({today_name}) нет занятий.")
        return

    await message.answer(f"📚 Предметы на сегодня ({today_name}):", reply_markup=menu_keyboard())


@dp.callback_query(F.data == "attendance")
async def attendance(callback: CallbackQuery):
    schedule = load_json("schedules.json", {})
    today_name = get_tashkent_weekday()
    subjects = schedule.get(today_name, [])
    await callback.message.edit_text("Выберите предмет:", reply_markup=subject_keyboard(subjects))
    await callback.answer()


@dp.callback_query(F.data.startswith("subject_"))
async def choose_subject(callback: CallbackQuery):
    token = callback.data.replace("subject_", "", 1)
    subject = unsafe_subject_from_token(token)

    students = load_json("students.json", {"names": []})
    filename = get_today_filename(subject)
    attendance = load_json(filename, {})
    # assure everyone exists
    for s in students.get("names", []):
        attendance.setdefault(s, {"status": "absent", "reason": ""})

    save_json(filename, attendance)
    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=student_keyboard(students, attendance))
    await callback.answer()


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_attendance(callback: CallbackQuery):
    # Получаем subject из текста сообщения (оно формируется в choose_subject)
    # Ожидается формат "📘 Предмет: {subject}\nОтметьте студентов:"
    header = callback.message.text.split("\n", 1)[0]
    if ":" in header:
        subject = header.split(":", 1)[1].strip()
    else:
        await callback.answer("Не могу определить предмет.", show_alert=True)
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
    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=student_keyboard(students, attendance))
    await callback.answer()


@dp.callback_query(F.data.startswith("reason_"))
async def ask_reason(callback: CallbackQuery, state: FSMContext):
    student_token = callback.data.replace("reason_", "", 1)
    student = unsafe_subject_from_token(student_token)

    # извлекаем предмет из текущего сообщения
    header = callback.message.text.split("\n", 1)[0]
    subject = header.split(":", 1)[1].strip() if ":" in header else "Unknown"

    await state.update_data(student=student, subject=subject, message_id=callback.message.message_id)
    await callback.message.answer(f"✏️ Введите причину для {student}:")
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

    await message.answer(f"📝 Причина для {student} сохранена: {reason}")
    # редактируем исходное сообщение, передавая ранее сохранённые chat_id/message_id
    await bot.edit_message_text(chat_id=message.chat.id, message_id=data["message_id"],
                                text=f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=kb)
    await state.clear()


@dp.callback_query(F.data.startswith("delreason_"))
async def delete_reason(callback: CallbackQuery):
    header = callback.message.text.split("\n", 1)[0]
    subject = header.split(":", 1)[1].strip() if ":" in header else "Unknown"

    student_token = callback.data.replace("delreason_", "", 1)
    student = unsafe_subject_from_token(student_token)

    filename = get_today_filename(subject)
    attendance = load_json(filename, {})
    if student in attendance:
        attendance[student]["reason"] = ""
        attendance[student]["status"] = "absent"

    save_json(filename, attendance)
    students = load_json("students.json", {"names": []})
    await callback.message.edit_text(f"📘 Предмет: {subject}\nОтметьте студентов:", reply_markup=student_keyboard(students, attendance))
    await callback.answer("Причина удалена ✅")


@dp.callback_query(F.data == "done_marking")
async def done(callback: CallbackQuery):
    try:
        # Извлекаем предмет из текста сообщения
        text = callback.message.text
        subject = text.split(":")[1].split("\n")[0].strip() if ":" in text else "Неизвестный предмет"

        # Загружаем данные посещаемости
        filename = get_today_filename(subject)
        attendance = load_json(filename, {})

        # Формируем красивый отчёт
        date_str = datetime.now().strftime("%d.%m.%Y")
        report = f"📘 Посещаемость завершена\n📚 Предмет: {subject}\n📅 Дата: {date_str}\n\n"

        present = [s for s, info in attendance.items() if info.get("status") == "present"]
        absent = [s for s, info in attendance.items() if info.get("status") == "absent"]
        reasoned = [f"{s} — {info.get('reason')}" for s, info in attendance.items() if info.get("status") == "reason"]

        report += f"✅ Присутствовали ({len(present)}):\n" + ("\n".join(present) if present else "—") + "\n\n"
        report += f"❌ Отсутствовали ({len(absent)}):\n" + ("\n".join(absent) if absent else "—") + "\n\n"
        report += f"📝 По уважительной причине ({len(reasoned)}):\n" + ("\n".join(reasoned) if reasoned else "—")

        # Отправляем сообщение пользователю
        await callback.message.edit_text("✅ Посещаемость сохранена и отправлена администраторам!")
        await callback.answer()

        # Отправляем отчёт администраторам
        await send_message_admins(report)

    except Exception as e:
        logging.exception(f"Ошибка при завершении отметки посещаемости: {e}")
        await callback.message.answer("❌ Произошла ошибка при сохранении посещаемости. Попробуйте снова.")


@dp.callback_query(F.data == "jurnal")
async def jurnal(callback: CallbackQuery):
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
    await callback.message.edit_text("📅 Выберите день:", reply_markup=keyboard)
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
    await callback.message.edit_text("📅 Выберите день:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("date_"))
async def get_date_subject(callback: CallbackQuery):
    date_str = callback.data.replace("date_", "", 1)
    date = datetime.strptime(date_str, "%Y-%m-%d")

    schedule = load_json("schedules.json", {})
    subjects = schedule.get(date.strftime("%A"), [])

    await callback.message.edit_text("Выберите предмет, который хотите посмотреть:",
                                    reply_markup=subject_keyboard_journal(subjects, date))
    await callback.answer()


@dp.callback_query(F.data.startswith("jurnalsubject_"))
async def handle_subject(callback: CallbackQuery):
    """Обработка выбора предмета для журнала."""
    try:
        # Безопасный парсинг данных
        parts = callback.data.split("_", 2)
        if len(parts) < 3:
            await callback.answer("❌ Некорректные данные кнопки.", show_alert=True)
            logger.error(f"Некорректный callback_data: {callback.data}")
            return

        _, subject_raw, date_str = parts
        subject = subject_raw.replace("_", " ")

        # Проверка формата даты
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await callback.answer("❌ Ошибка формата даты.", show_alert=True)
            logger.error(f"Ошибка парсинга даты: {date_str}")
            return

        # Загрузка посещаемости
        attendance = get_attendance(date.strftime('%Y-%m-%d'), subject)
        if not attendance:
            await callback.message.edit_text(
                f"📘 Предмет: {subject}\n📅 Дата: {date.strftime('%d.%m.%Y')}\n\nНет данных о посещаемости."
            )
            return

        # Формирование текста
        text_lines = [
            f"📘 Посещаемость по предмету: {subject}",
            f"📅 Дата: {date.strftime('%d.%m.%Y')}",
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
        await callback.answer("⚠️ Произошла ошибка. Попробуйте позже.", show_alert=True)


# ---------------------------
# Run
# ---------------------------
async def main():
    logger.info("Бот запущен ✅")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())