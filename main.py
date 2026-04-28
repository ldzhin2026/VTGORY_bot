import asyncio
import random
import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command   # ← Добавили Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ────────────────────────────────────────────────
# Настройки
# ────────────────────────────────────────────────
def parse_int_list(raw: str) -> list[int]:
    values = []
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if token:
            values.append(int(token))
    return values


TOKEN = os.getenv("BOT_TOKEN", "8656659502:AAEr1hajHfDs0y-iqjoAWG6qT0Hw7P4IYpI").strip()

CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/tolkogori")
CHAT_LINK = os.getenv("CHAT_LINK", "https://t.me/tolkogori_chat")
PHOTO_PATH = os.getenv("PHOTO_PATH", "welcome_photo.jpg")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7051676412"))

raw_moderators = os.getenv("MODERATORS_IDS", "")
if raw_moderators:
    MODERATORS_IDS = parse_int_list(raw_moderators)
    if ADMIN_ID not in MODERATORS_IDS:
        MODERATORS_IDS.append(ADMIN_ID)
else:
    MODERATORS_IDS = [ADMIN_ID, 1483123969, 996400017]

DB_PATH = os.getenv("DB_PATH", "/app/data/subscribers.db")
db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

conn = sqlite3.connect(DB_PATH, timeout=10)
cur = conn.cursor()

# Таблица пользователей
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    attempts_used INTEGER DEFAULT 0
)''')

# Таблица для розыгрыша (один пользователь = один ID)
cur.execute('''CREATE TABLE IF NOT EXISTS giveaway_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,   -- главный ключ: один TG = один ID
    participant_id TEXT NOT NULL,
    entered_at TEXT
)''')
conn.commit()

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

class CaptchaStates(StatesGroup):
    waiting_for_answer = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    confirm_broadcast = State()
    select_audience = State()
    waiting_for_user_list = State()

class GiveawayStates(StatesGroup):
    waiting_for_id = State()
    waiting_for_winners_count = State()

giveaway_active = False

# Вспомогательные функции
def generate_task():
    a = random.randint(10, 35)
    b = random.randint(1, a - 5)
    correct = a - b
    wrongs = [correct + d for d in random.sample([-7, -5, -3, 3, 5, 7, 9], 3)]
    answers = [correct] + wrongs
    random.shuffle(answers)
    return f"{a} − {b} = ?", correct, answers

def save_user(user: types.User, attempts_used: int):
    now = datetime.now().isoformat()
    username = user.username if user.username else None
    cur.execute('''INSERT OR REPLACE INTO users
                   (user_id, username, first_name, joined_at, attempts_used)
                   VALUES (?, ?, ?, ?, ?)''',
                (user.id, username, user.first_name, now, attempts_used))
    conn.commit()


def get_winners_chat_id() -> str | None:
    configured = os.getenv("WINNERS_CHANNEL_ID", "").strip()
    if configured:
        return configured
    if "t.me/" in CHANNEL_LINK:
        slug = CHANNEL_LINK.rstrip("/").split("/")[-1].strip()
        if slug:
            return f"@{slug}"
    return None

# ==================== ХЕНДЛЕРЫ ====================

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    text = "📜 **Правила канала ВЫШЕ ТОЛЬКО ГОРЫ**\n\n• Обязательная подписка\n• Запрещены: спам, оскорбления\n\nПройдите проверку ↓"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 ПОДПИСАТЬСЯ", callback_data="start_captcha")]])
    try:
        if os.path.isfile(PHOTO_PATH):
            await message.answer_photo(FSInputFile(PHOTO_PATH), caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Ошибка отправки фото: {e}")
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "start_captcha")
async def start_captcha(callback: types.CallbackQuery, state: FSMContext):
    question, correct, variants = generate_task()
    await state.update_data(correct=correct, attempts=3, variants=variants, attempts_used=0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[:2]],
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[2:]]
    ])
    await callback.message.reply(f"<b>Решите:</b>\n\n{question}\n\n(3 попытки)", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("captcha_"))
async def check_answer(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    correct = data.get("correct")
    attempts = data.get("attempts", 3)
    attempts_used = data.get("attempts_used", 0) + (3 - attempts)
    try:
        answer = int(callback.data.split("_")[1])
    except:
        await callback.answer("Ошибка", show_alert=True)
        return
    if answer == correct:
        save_user(callback.from_user, attempts_used + 1)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 ТЕЛЕГРАМ КАНАЛ", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="💬 НАШ ЧАТ", url=CHAT_LINK)],
            [InlineKeyboardButton(text="🎰 DRAGON MONEY", url="https://vtgori.pro/dragon")],
            [InlineKeyboardButton(text="🎟️ РОЗЫГРЫШ", callback_data="start_giveaway")],
            [InlineKeyboardButton(text="🛠️ МОДЕРАТОР", url="https://t.me/ModTolkogori")],
            [InlineKeyboardButton(text="🟢 СТРИМЫ НА KICK", url="https://vtgori.pro/kick")]
        ])
        
        text = (
            "✅ Пройдено!\n"
            "Для участия в розыгрыше необходимо сделать следующее:\n\n"
            "✈️ 1. Подписаться на ТЕЛЕГРАМ КАНАЛ и ЧАТ\n\n"
            "✍️ 2. Зарегистрироваться по ссылке ниже в Dragon Money"
            "(<a href='http://vtgori.pro/guide/'>инструкция здесь</a>)\n\n"
            "🎁 3. Нажать на кнопку «РОЗЫГРЫШ» ниже и вставить ID аккаунта от Dragon Money\n\n"
            "🏆 4. Если ты выиграл в розыгрыше, тогда получишь 1000 руб. с возможностью вывода денег.\n\n"
            "☝️ Начисление возможно только нашему рефералу (тому кто зарегистрировался по нашей ссылке)"
        )
        
        await callback.message.reply(text, reply_markup=kb, parse_mode="HTML")
        await state.clear()
        await callback.answer("Успех!")
    else:
        attempts -= 1
        attempts_used += 1
        await state.update_data(attempts=attempts, attempts_used=attempts_used)
        if attempts > 0:
            await callback.answer(f"Неверно • Осталось: {attempts}", show_alert=True)
        else:
            await callback.message.reply("❌ Попытки кончились. /start")
            await state.clear()
            await callback.answer("Исчерпано", show_alert=True)
            # ==================== НОВАЯ КОМАНДА ДЛЯ РОЗЫГРЫША ====================
@router.message(Command("giveaway"))
@router.message(F.text.lower().in_({"розыгрыш", "розыгрышь", "giveaway", "участие"}))
async def giveaway_command(message: types.Message, state: FSMContext):
    global giveaway_active

    # Проверка: прошёл ли капчу
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (message.from_user.id,))
    if not cur.fetchone():
        await message.answer("❌ Вы ещё не прошли проверку.\n\nНапишите /start")
        return

    if not giveaway_active:
        await message.answer("❌ Сейчас розыгрыши не проводятся.")
        return

    # Проверка: уже участвует?
    cur.execute("SELECT participant_id FROM giveaway_participants WHERE telegram_user_id = ?", 
                (message.from_user.id,))
    if cur.fetchone():
        await message.answer("⚠️ Вы уже участвуете в текущем розыгрыше!")
        return

    # Красивое сообщение как после капчи
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 ТЕЛЕГРАМ КАНАЛ", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="💬 НАШ ЧАТ", url=CHAT_LINK)],
        [InlineKeyboardButton(text="🎰 DRAGON MONEY", url="https://vtgori.pro/dragon")],
        [InlineKeyboardButton(text="🎟️ РОЗЫГРЫШ", callback_data="start_giveaway")],
        [InlineKeyboardButton(text="🛠️ МОДЕРАТОР", url="https://t.me/ModTolkogori")],
        [InlineKeyboardButton(text="🟢 СТРИМЫ НА KICK", url="https://vtgori.pro/kick")]
    ])

    text = (
        "✅ **Пройдено!**\n\n"
        "Для участия в розыгрыше необходимо сделать следующее:\n\n"
        "✈️ 1. Подписаться на ТЕЛЕГРАМ КАНАЛ и ЧАТ\n\n"
        f"✍️ 2. Зарегистрироваться по ссылке ниже в Dragon Money "
        f"(<a href='http://vtgori.pro/guide/'>инструкция здесь</a>)\n\n"
        "🎁 3. Нажать на кнопку «РОЗЫГРЫШ» ниже и вставить ID аккаунта от Dragon Money\n\n"
        "🏆 4. Если ты выиграл в розыгрыше, тогда получишь 1000 руб. с возможностью вывода денег.\n\n"
        "☝️ Начисление возможно только нашему рефералу (тому кто зарегистрировался по нашей ссылке)"
    )

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# ==================== РОЗЫГРЫШ ====================

@router.callback_query(F.data == "start_giveaway")
async def start_giveaway(callback: types.CallbackQuery, state: FSMContext):
    global giveaway_active
    if not giveaway_active:
        await callback.message.reply("❌ Сейчас розыгрыши не проходят")
        await callback.answer()
        return
    await callback.message.reply(
        "🎟️ **Участие в розыгрыше**\n\n"
        "Отправь мне **ID** твоего игрового кабинета Dragon Money\n(только цифры, без пробелов)",
        parse_mode="Markdown"
    )
    await state.set_state(GiveawayStates.waiting_for_id)
    await callback.answer()


@router.message(GiveawayStates.waiting_for_id)
async def process_giveaway_id(message: types.Message, state: FSMContext):
    global giveaway_active
    if not giveaway_active:
        await message.reply("❌ Розыгрыш уже завершён")
        await state.clear()
        return

    pid = message.text.strip()
    if not pid.isdigit():
        await message.reply("❌ ID должен состоять только из цифр.")
        return

    user_id = message.from_user.id  # ← ID Telegram пользователя

    try:
        # Проверяем, участвовал ли уже этот человек
        cur.execute("SELECT participant_id FROM giveaway_participants WHERE telegram_user_id = ?", 
                   (user_id,))
        existing = cur.fetchone()

        if existing:
            await message.reply(
                f"⚠️ Вы уже участвуете в розыгрыше!\n\n"
                f"Ваш текущий ID: `{existing[0]}`\n\n"
                "Повторное участие **запрещено**."
            )
            await state.clear()
            return

        # Добавляем нового участника
        cur.execute(
            "INSERT INTO giveaway_participants "
            "(telegram_user_id, participant_id, entered_at) "
            "VALUES (?, ?, ?)",
            (user_id, pid, datetime.now().isoformat())
        )
        conn.commit()

        await message.reply(
            "✅ Вы успешно участвуете в розыгрыше!\n\n"
            f"Ваш ID: `{pid}`\n"
            "Желаем удачи! 🎉"
        )

    except Exception as e:
        await message.reply("❌ Ошибка при записи. Попробуйте ещё раз.")
        logger.error(f"Giveaway error: {e}")

    await state.clear()


@router.callback_query(F.data == "admin_giveaway_menu")
async def admin_giveaway_menu(callback: types.CallbackQuery):
    if callback.from_user.id not in MODERATORS_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    global giveaway_active
    status = "🟢 АКТИВЕН" if giveaway_active else "🔴 НЕ АКТИВЕН"
    cur.execute("SELECT COUNT(*) FROM giveaway_participants")
    total = cur.fetchone()[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {status}", callback_data="noop")],
        [InlineKeyboardButton(text=f"Участников: {total}", callback_data="admin_giveaway_list")],
        [InlineKeyboardButton(text="🚀 ЗАПУСТИТЬ РОЗЫГРЫШ", callback_data="giveaway_start")],
        [InlineKeyboardButton(text="🏁 ЗАВЕРШИТЬ РОЗЫГРЫШ", callback_data="giveaway_end")],
        [InlineKeyboardButton(text="📋 Показать участников", callback_data="admin_giveaway_list")],
        [InlineKeyboardButton(text="← Назад", callback_data="admin_cancel")]
    ])
    await callback.message.edit_text("🎟️ **Управление розыгрышем**", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "giveaway_start")
async def giveaway_start(callback: types.CallbackQuery):
    global giveaway_active
    giveaway_active = True
    await callback.message.edit_text("✅ Розыгрыш **запущен**! Пользователи могут отправлять ID.")
    await callback.answer("Запущен")


@router.callback_query(F.data == "giveaway_end")
async def giveaway_end(callback: types.CallbackQuery, state: FSMContext):
    global giveaway_active
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только владелец может завершить розыгрыш", show_alert=True)
        return
    if not giveaway_active:
        await callback.answer("Розыгрыш не активен", show_alert=True)
        return
    await callback.message.edit_text("🏁 Сколько победителей выбрать?\nНапиши число:")
    await state.set_state(GiveawayStates.waiting_for_winners_count)
    await callback.answer()


@router.message(GiveawayStates.waiting_for_winners_count)
async def process_winners_count(message: types.Message, state: FSMContext):
    global giveaway_active

    if message.from_user.id != ADMIN_ID:
        await message.reply("❌ Только владелец может задавать количество победителей.")
        await state.clear()
        return

    try:
        count = int(message.text.strip())
        if count < 1:
            raise ValueError
    except:
        await message.reply("❌ Введи положительное целое число")
        return

    cur.execute("SELECT telegram_user_id, participant_id FROM giveaway_participants")
    participants = cur.fetchall()

    if not participants:
        await message.reply("Нет участников.")
        giveaway_active = False
        await state.clear()
        return

    winners = random.sample(participants, k=min(count, len(participants)))
    winner_ids = [pid for _, pid in winners]

    text = (
        f"🎉 **РОЗЫГРЫШ ЗАВЕРШЁН**\n\n"
        f"Выбрано: **{len(winner_ids)}** из {len(participants)} участников\n"
        f"🏆 **Победители:**\n"
    )
    for i, pid in enumerate(winner_ids, 1):
        text += f"{i}. `{pid}`\n"

    await message.reply(text, parse_mode="Markdown")

    winners_chat_id = get_winners_chat_id()
    if winners_chat_id:
        try:
            await bot.send_message(chat_id=winners_chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Не удалось опубликовать победителей в канал: {e}")

    for tg_user_id, pid in winners:
        try:
            await bot.send_message(
                tg_user_id,
                (
                    "🎉 Поздравляем! Ты выиграл в розыгрыше.\n\n"
                    f"Твой ID: `{pid}`\n\n"
                    "Выигрыш будет отправлен в кабинет Dragon Money "
                    "только если регистрация была по нашей ссылке."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить ЛС победителю {tg_user_id}: {e}")

    cur.execute("DELETE FROM giveaway_participants")
    conn.commit()
    giveaway_active = False
    await state.clear()


@router.callback_query(F.data == "admin_giveaway_list")
async def admin_giveaway_list(callback: types.CallbackQuery):
    """Показывает полный список участников розыгрыша"""
    cur.execute("""
        SELECT 
            g.telegram_user_id,
            g.participant_id,
            g.entered_at,
            u.username,
            u.first_name
        FROM giveaway_participants g
        LEFT JOIN users u ON g.telegram_user_id = u.user_id
        ORDER BY g.id DESC
    """)
    rows = cur.fetchall()
    
    if not rows:
        await callback.message.edit_text("Пока нет участников.")
        await callback.answer()
        return

    text = f"📋 **Все участники розыгрыша** — {len(rows)} человек\n\n"
    current_message = text
    messages = []

    for i, (tg_id, pid, dt, username, first_name) in enumerate(rows, 1):
        # Определяем, как отображать пользователя
        if username:
            user_display = f"@{username}"
        elif first_name:
            user_display = first_name
        else:
            user_display = str(tg_id)
        
        line = f"{i}. TG: {user_display} → ID: `{pid}`\n"
        
        # Если сообщение становится слишком длинным — начинаем новое
        if len(current_message) + len(line) > 3800:
            messages.append(current_message)
            current_message = f"📋 **Продолжение списка** ({i}/{len(rows)})\n\n"
        
        current_message += line

    # Добавляем последнее сообщение в список
    if current_message.strip():
        messages.append(current_message)

    # Отправляем первое сообщение (редактируем текущее)
    await callback.message.edit_text(messages[0], parse_mode="Markdown")
    
    # Отправляем остальные сообщения, если список большой
    for msg in messages[1:]:
        await asyncio.sleep(0.3)  # небольшая пауза, чтобы Telegram не ругался
        await callback.message.answer(msg, parse_mode="Markdown")

    await callback.answer(f"✅ Показано {len(rows)} участников")

# ==================== АДМИН МЕНЮ ====================
@router.message(F.text.in_({"/admin", "/menu", "/help", "/", "/start"}))
async def admin_menu(message: types.Message):
    if message.from_user.id not in MODERATORS_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎟️ Розыгрыш", callback_data="admin_giveaway_menu")],
        [InlineKeyboardButton(text="📁 Скачать базу", callback_data="admin_getdb")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_cancel")]
    ])
    await message.answer("Админ-панель\nВыберите действие:", reply_markup=kb)
    # ===================== ТЕСТОВЫЕ КОМАНДЫ ДЛЯ РОЗЫГРЫША =====================

# ===================== ТЕСТОВЫЕ КОМАНДЫ =====================

@router.message(F.text == "/addtest")
async def add_test_participants(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ только администратору.")
        return

    count = 75
    added = 0

    for i in range(count):
        tg_id = 100000000 + i
        
        # === НОВАЯ ЛОГИКА ТЕСТОВЫХ ID ===
        if i < 25:
            # 25 обычных участников (ниже приоритета)
            pid = f"1079{i:05d}"
        elif i < 55:
            # 30 приоритетных участников (10830000 и выше)
            pid = f"1083{i:05d}"
        else:
            # 20 высоких ID
            pid = f"109{i:06d}"

        dt = f"2026-04-27T12:{i//60:02d}:{i%60:02d}"

        try:
            cur.execute(
                "INSERT OR IGNORE INTO giveaway_participants "
                "(telegram_user_id, participant_id, entered_at) "
                "VALUES (?, ?, ?)",
                (tg_id, pid, dt)
            )
            added += 1
        except:
            pass

    conn.commit()
    
    await message.answer(
        f"✅ Добавлено **{added}** тестовых участников\n\n"
        f"Состав:\n"
        f"• 25 обычных (ID < 10830000)\n"
        f"• 30 приоритетных (1083xxxx)\n"
        f"• 20 высоких (109xxxx)\n\n"
        f"Теперь можешь завершить розыгрыш и проверить логику."
    )

@router.message(F.text == "/cleartest")
async def clear_test_participants(message: types.Message):
    """Полностью очищает таблицу участников розыгрыша"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ только администратору.")
        return
    
    cur.execute("DELETE FROM giveaway_participants")
    conn.commit()
    
    await message.answer("🗑️ **Таблица участников розыгрыша полностью очищена.**")


@router.message(F.text == "/count")
async def count_participants(message: types.Message):
    """Показывает количество участников"""
    if message.from_user.id != ADMIN_ID:
        return
    
    cur.execute("SELECT COUNT(*) FROM giveaway_participants")
    count = cur.fetchone()[0]
    await message.answer(f"📊 Всего участников в розыгрыше: **{count}**")

# ==================== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ====================
@router.callback_query()
async def universal_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data

    if data in ["start_giveaway", "admin_giveaway_menu", "giveaway_start", 
                "giveaway_end", "admin_giveaway_list", "noop"]:
        await callback.answer()
        return

    if callback.from_user.id not in MODERATORS_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if data in ["admin_stats", "admin_getdb"] and callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещён (только владелец)", show_alert=True)
        return

    try:
        if data == "admin_broadcast":
            await callback.message.edit_text("Отправьте сообщение для рассылки (текст, фото, видео и т.д.)")
            await state.set_state(BroadcastStates.waiting_for_message)
            await callback.answer("Ожидаю")
        elif data == "broadcast_change":
            await callback.message.edit_text("Отправьте новое сообщение для рассылки")
            await state.set_state(BroadcastStates.waiting_for_message)
            await callback.answer("Изменяем")
        elif data == "confirm_broadcast_yes":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Всем", callback_data="audience_all")],
                [InlineKeyboardButton(text="Выборочно по ID", callback_data="audience_select")],
                [InlineKeyboardButton(text="Отмена", callback_data="broadcast_cancel")]
            ])
            await callback.message.edit_text("Кому отправить?", reply_markup=kb)
            await state.set_state(BroadcastStates.select_audience)
            await callback.answer("Выбор")
        elif data == "audience_all":
            await callback.message.edit_text("Рассылка запущена → всем...")
            await callback.answer()
            delivered, failed = await do_broadcast(state)
            await callback.message.answer(f"✅ Готово. Доставлено: {delivered}, ошибок: {failed}")
            await state.clear()
        elif data == "audience_select":
            await callback.message.edit_text("Пришлите user_id (по строкам, пробелам или запятым)")
            await state.set_state(BroadcastStates.waiting_for_user_list)
            await callback.answer("Ожидаю ID")
        elif data == "broadcast_cancel":
            await state.clear()
            await callback.message.edit_text("Рассылка отменена")
            await callback.answer("Отменено")
        elif data == "admin_stats":
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            text = f"Всего пользователей: {total}"
            if total > 0:
                cur.execute("SELECT user_id, username, first_name, joined_at, attempts_used FROM users ORDER BY joined_at DESC LIMIT 5")
                rows = cur.fetchall()
                text += "\n\nПоследние 5:\n"
                for row in rows:
                    text += f"{row[0]} @{row[1] or 'нет'} ({row[2] or '?'}) — {row[3][:19]} — попыток: {row[4]}\n"
            await callback.message.edit_text(text or "База пуста")
            await callback.answer("Статистика готова")
        elif data == "admin_getdb":
            if not os.path.exists(DB_PATH):
                await callback.message.answer("База не найдена")
            else:
                size_kb = os.path.getsize(DB_PATH) / 1024
                caption = f"subscribers.db • {size_kb:.1f} КБ • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                await callback.message.answer_document(document=FSInputFile(DB_PATH), caption=caption)
            await callback.answer("База отправлена")
        elif data == "admin_cancel":
            await callback.message.delete()
            await callback.answer("Меню закрыто")
        else:
            await callback.answer(f"Неизвестная кнопка: {data}", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка callback {data}: {e}", exc_info=True)
        await callback.message.answer(f"Ошибка: {str(e)}")

# ==================== ТВОЯ РАССЫЛКА (исходная) ====================

@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast_content(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        return
    await state.update_data(
        broadcast_content=message.model_dump_json(exclude_unset=True),
        source_chat_id=message.chat.id,
        source_message_id=message.message_id
    )
    preview_text = message.text or message.caption or "Сообщение без текста"
    preview = f"Предпросмотр рассылки:\n\n{preview_text[:500]}..."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запустить рассылку", callback_data="confirm_broadcast_yes")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast_change")]
    ])
    await message.forward(chat_id=message.chat.id)
    await message.answer(preview + "\n\n(при рассылке будет переслан оригинал)", reply_markup=kb)
    await state.set_state(BroadcastStates.confirm_broadcast)

@router.message(BroadcastStates.waiting_for_user_list)
async def process_selective_list(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return

    raw = message.text or ""
    tokens = raw.replace(",", " ").replace("\n", " ").split()
    selected_user_ids = []
    for token in tokens:
        if token.isdigit():
            selected_user_ids.append(int(token))

    if not selected_user_ids:
        await message.answer("❌ Не найдено корректных user_id. Пришли числа через пробел/запятую.")
        return

    await message.answer(f"Рассылка запущена выборочно ({len(selected_user_ids)} ID)...")
    delivered, failed = await do_broadcast(state, selected_user_ids)
    await message.answer(f"✅ Готово. Доставлено: {delivered}, ошибок: {failed}")
    await state.clear()


async def do_broadcast(state: FSMContext, selected_user_ids: list[int] | None = None) -> tuple[int, int]:
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")

    if not source_chat_id or not source_message_id:
        return 0, 0

    if selected_user_ids is None:
        cur.execute("SELECT user_id FROM users")
        target_ids = [row[0] for row in cur.fetchall()]
    else:
        target_ids = list(dict.fromkeys(selected_user_ids))

    delivered = 0
    failed = 0

    for user_id in target_ids:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id
            )
            delivered += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.03)

    return delivered, failed

# Запуск
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        conn.close()
