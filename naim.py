import asyncio
import random
import logging
import sqlite3
import os
import json
import re
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


TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Укажите переменную BOT_TOKEN в Railway Variables (без кавычек и пробелов)."
    )

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

cur.execute('''CREATE TABLE IF NOT EXISTS broadcast_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    text TEXT,
    raw_text TEXT,
    parse_mode TEXT,
    entities_json TEXT,
    media_type TEXT,
    media_file_id TEXT,
    buttons_json TEXT,
    created_by INTEGER,
    created_at TEXT
)''')

try:
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_giveaway_participant_id_unique ON giveaway_participants(participant_id)")
except sqlite3.Error as e:
    logger.warning(f"Не удалось создать уникальный индекс participant_id: {e}")
for migration in [
    "ALTER TABLE broadcast_templates ADD COLUMN raw_text TEXT",
    "ALTER TABLE broadcast_templates ADD COLUMN parse_mode TEXT",
    "ALTER TABLE broadcast_templates ADD COLUMN entities_json TEXT"
]:
    try:
        cur.execute(migration)
    except sqlite3.Error:
        pass

cur.execute('''CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY,
    banned_at TEXT NOT NULL,
    banned_by INTEGER,
    reason TEXT
)''')
conn.commit()

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


def is_staff(user_id: int) -> bool:
    return user_id in MODERATORS_IDS


def is_banned(user_id: int) -> bool:
    cur.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    return cur.fetchone() is not None


def ban_user_record(target_id: int, by_id: int, reason: str | None = None) -> None:
    cur.execute(
        "INSERT OR REPLACE INTO banned_users (user_id, banned_at, banned_by, reason) VALUES (?, ?, ?, ?)",
        (target_id, datetime.now().isoformat(), by_id, reason),
    )
    conn.commit()


def unban_user_record(target_id: int) -> bool:
    cur.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (target_id,))
    if not cur.fetchone():
        return False
    cur.execute("DELETE FROM banned_users WHERE user_id = ?", (target_id,))
    conn.commit()
    return True


@router.message.middleware()
async def ban_guard_message(handler, event: types.Message, data: dict):
    if event.from_user and not is_staff(event.from_user.id) and is_banned(event.from_user.id):
        await event.answer("⛔ Вам ограничен доступ к этому боту.")
        return
    return await handler(event, data)


@router.callback_query.middleware()
async def ban_guard_callback(handler, event: types.CallbackQuery, data: dict):
    if event.from_user and not is_staff(event.from_user.id) and is_banned(event.from_user.id):
        await event.answer("⛔ Доступ ограничен", show_alert=True)
        return
    return await handler(event, data)

class CaptchaStates(StatesGroup):
    waiting_for_answer = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    confirm_broadcast = State()
    select_audience = State()
    waiting_for_user_list = State()
    waiting_for_buttons = State()
    waiting_for_template_name = State()
    waiting_for_template_content = State()
    waiting_for_template_buttons = State()

class GiveawayStates(StatesGroup):
    waiting_for_id = State()
    waiting_for_winners_count = State()
    waiting_for_search_query = State()


class BanStates(StatesGroup):
    waiting_ban_user_id = State()
    waiting_unban_user_id = State()

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


def get_main_channel_chat_id() -> str | None:
    configured = os.getenv("MAIN_CHANNEL_ID", "").strip()
    if configured:
        return configured
    return get_winners_chat_id()


def parse_buttons(raw: str) -> list[dict]:
    buttons = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            raise ValueError("Каждая строка должна быть в формате: Текст | https://url")
        text, url = line.split("|", 1)
        text = text.strip()
        url = url.strip()
        if not text or not url.startswith(("http://", "https://", "tg://")):
            raise ValueError("Некорректный формат кнопки. Нужен текст и валидный URL.")
        buttons.append({"text": text[:64], "url": url})
    return buttons


def build_buttons_markup(buttons: list[dict] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = [[InlineKeyboardButton(text=b["text"], url=b["url"])] for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def extract_message_payload(message: types.Message) -> dict:
    html_text = message.html_text if message.text else None
    html_caption = None
    if message.caption:
        html_caption = getattr(message, "html_caption", None)
        if not html_caption:
            # Совместимость с версиями aiogram, где html_caption отсутствует
            html_caption = message.caption
    entities = message.entities if message.text else message.caption_entities
    entities_json = []
    if entities:
        for ent in entities:
            if hasattr(ent, "model_dump"):
                entities_json.append(ent.model_dump(exclude_none=True))
            else:
                entities_json.append(dict(ent))
    payload = {
        "text": html_text or html_caption or message.text or message.caption or "",
        "raw_text": message.text or message.caption or "",
        "parse_mode": "HTML",
        "entities_json": json.dumps(entities_json, ensure_ascii=False),
        "media_type": None,
        "media_file_id": None
    }
    if message.photo:
        payload["media_type"] = "photo"
        payload["media_file_id"] = message.photo[-1].file_id
    elif message.video:
        payload["media_type"] = "video"
        payload["media_file_id"] = message.video.file_id
    elif message.document:
        payload["media_type"] = "document"
        payload["media_file_id"] = message.document.file_id
    return payload


def classify_send_error(error: Exception) -> str:
    msg = str(error).lower()
    if "bot was blocked" in msg:
        return "blocked"
    if "chat not found" in msg:
        return "chat_not_found"
    if "user is deactivated" in msg:
        return "deactivated"
    if "forbidden" in msg:
        return "forbidden"
    return "other"


async def render_giveaway_page(message: types.Message, page: int = 0):
    page_size = 20
    page = max(page, 0)
    offset = page * page_size
    cur.execute("SELECT COUNT(*) FROM giveaway_participants")
    total = cur.fetchone()[0]
    if total == 0:
        await message.edit_text("Пока нет участников.")
        return

    cur.execute(
        """
        SELECT g.telegram_user_id, g.participant_id, g.entered_at, u.username, u.first_name
        FROM giveaway_participants g
        LEFT JOIN users u ON g.telegram_user_id = u.user_id
        ORDER BY g.entered_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset)
    )
    rows = cur.fetchall()
    max_page = (total + page_size - 1) // page_size
    text = f"📋 **Участники розыгрыша** — {total}\nСтраница {page + 1}/{max_page}\n\n"
    for i, (tg_id, pid, _dt, username, first_name) in enumerate(rows, offset + 1):
        user_display = f"@{username}" if username else (first_name or str(tg_id))
        text += f"{i}. {user_display} → `{pid}`\n"

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"admin_giveaway_page_{page-1}"))
    if page + 1 < max_page:
        nav.append(InlineKeyboardButton(text="След ➡️", callback_data=f"admin_giveaway_page_{page+1}"))
    kb = []
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔎 Поиск ID", callback_data="admin_giveaway_search")])
    kb.append([InlineKeyboardButton(text="← Назад", callback_data="admin_giveaway_menu")])
    await message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

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

        # Проверяем, что этот Dragon ID не использует другой Telegram пользователь
        cur.execute(
            "SELECT telegram_user_id FROM giveaway_participants WHERE participant_id = ? AND telegram_user_id != ?",
            (pid, user_id)
        )
        if cur.fetchone():
            await message.reply("❌ Этот ID уже используется другим участником. Укажи другой ID.")
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

    except sqlite3.IntegrityError:
        await message.reply("❌ Этот ID уже участвует в розыгрыше.")
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

    winners_chat_id = get_main_channel_chat_id()
    if winners_chat_id:
        try:
            await bot.send_message(chat_id=winners_chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Не удалось опубликовать победителей в канал: {e}")
            await message.answer("⚠️ Не удалось опубликовать победителей в основной канал.")

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
    await render_giveaway_page(callback.message, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_giveaway_page_"))
async def admin_giveaway_page(callback: types.CallbackQuery):
    if callback.from_user.id not in MODERATORS_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("Неверная страница", show_alert=True)
        return
    await render_giveaway_page(callback.message, page)
    await callback.answer()


@router.callback_query(F.data == "admin_giveaway_search")
async def admin_giveaway_search(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in MODERATORS_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("🔎 Введите Dragon ID (полностью или часть):")
    await state.set_state(GiveawayStates.waiting_for_search_query)
    await callback.answer()


@router.message(GiveawayStates.waiting_for_search_query)
async def process_giveaway_search_query(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("❌ Пустой запрос. Введите ID или часть ID.")
        return
    cur.execute(
        """
        SELECT g.telegram_user_id, g.participant_id, u.username, u.first_name
        FROM giveaway_participants g
        LEFT JOIN users u ON g.telegram_user_id = u.user_id
        WHERE g.participant_id LIKE ?
        ORDER BY g.entered_at DESC
        LIMIT 30
        """,
        (f"%{query}%",)
    )
    rows = cur.fetchall()
    if not rows:
        await message.answer("Ничего не найдено.")
    else:
        text = f"🔎 Результаты поиска по `{query}`:\n\n"
        for i, (tg_id, pid, username, first_name) in enumerate(rows, 1):
            user_display = f"@{username}" if username else (first_name or str(tg_id))
            text += f"{i}. {user_display} → `{pid}`\n"
        await message.answer(text, parse_mode="Markdown")
    await state.clear()

# ==================== АДМИН МЕНЮ ====================
@router.message(F.text.in_({"/admin", "/menu", "/help", "/", "/start"}))
async def admin_menu(message: types.Message):
    if message.from_user.id not in MODERATORS_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🚫 Баны", callback_data="admin_bans_menu")],
        [InlineKeyboardButton(text="🧩 Шаблоны рассылок", callback_data="admin_templates_menu")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎟️ Розыгрыш", callback_data="admin_giveaway_menu")],
        [InlineKeyboardButton(text="📁 Скачать базу", callback_data="admin_getdb")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_cancel")]
    ])
    await message.answer("Админ-панель\nВыберите действие:", reply_markup=kb)
    # ===================== ТЕСТОВЫЕ КОМАНДЫ ДЛЯ РОЗЫГРЫША =====================


@router.message(BanStates.waiting_ban_user_id)
async def process_ban_by_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Нужен числовой Telegram user_id.")
        return
    target = int(raw)
    if target == message.from_user.id:
        await message.answer("❌ Нельзя заблокировать самого себя.")
        await state.clear()
        return
    if target in MODERATORS_IDS:
        await message.answer("❌ Нельзя заблокировать владельца или модератора.")
        await state.clear()
        return
    ban_user_record(target, message.from_user.id, None)
    await message.answer(f"✅ Пользователь `{target}` заблокирован.", parse_mode="Markdown")
    await state.clear()


@router.message(BanStates.waiting_unban_user_id)
async def process_unban_by_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("❌ Нужен числовой Telegram user_id.")
        return
    target = int(raw)
    if unban_user_record(target):
        await message.answer(f"✅ Пользователь `{target}` разблокирован.", parse_mode="Markdown")
    else:
        await message.answer("Пользователь не найден в списке блокировок.")
    await state.clear()

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
        elif data == "admin_bans_menu":
            await callback.message.edit_text(
                "🚫 Управление блокировками\nВыберите действие:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⛔ Забанить по user_id", callback_data="ban_prompt")],
                    [InlineKeyboardButton(text="✅ Разбанить по user_id", callback_data="unban_prompt")],
                    [InlineKeyboardButton(text="📋 Список заблокированных", callback_data="ban_list")],
                    [InlineKeyboardButton(text="← Назад", callback_data="admin_cancel")]
                ]),
            )
            await callback.answer()
        elif data == "ban_prompt":
            await callback.message.edit_text("Введите числовой Telegram user_id пользователя для бана:")
            await state.set_state(BanStates.waiting_ban_user_id)
            await callback.answer()
        elif data == "unban_prompt":
            await callback.message.edit_text("Введите Telegram user_id для разбана:")
            await state.set_state(BanStates.waiting_unban_user_id)
            await callback.answer()
        elif data == "ban_list":
            cur.execute(
                "SELECT user_id, banned_at, banned_by, reason FROM banned_users ORDER BY banned_at DESC LIMIT 30"
            )
            rows = cur.fetchall()
            if not rows:
                text = "Заблокированных нет."
            else:
                text = "📋 Последние блокировки:\n\n"
                for uid, ts, by_id, reason in rows:
                    line = f"• `{uid}` — {ts[:19]} (кто: {by_id})"
                    if reason:
                        line += f" — {reason}"
                    text += line + "\n"
            await callback.message.edit_text(text, parse_mode="Markdown")
            await callback.answer()
        elif data == "broadcast_add_buttons":
            await callback.message.edit_text(
                "Отправьте кнопки, каждая с новой строки:\n"
                "`Текст | https://url`\n\n"
                "Пример:\nСайт | https://example.com",
                parse_mode="Markdown"
            )
            await state.set_state(BroadcastStates.waiting_for_buttons)
            await callback.answer("Ожидаю кнопки")
        elif data == "broadcast_save_template":
            await callback.message.edit_text("Введите название шаблона:")
            await state.set_state(BroadcastStates.waiting_for_template_name)
            await callback.answer("Название")
        elif data == "template_skip_buttons":
            await state.update_data(buttons_json="[]")
            await callback.message.edit_text("Введите название шаблона (уникальное):")
            await state.set_state(BroadcastStates.waiting_for_template_name)
            await callback.answer("Кнопки пропущены")
        elif data == "broadcast_change":
            await callback.message.edit_text("Отправьте новое сообщение для рассылки")
            await state.set_state(BroadcastStates.waiting_for_message)
            await callback.answer("Изменяем")
        elif data == "confirm_broadcast_yes":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Всем", callback_data="audience_all")],
                [InlineKeyboardButton(text="Выборочно по ID", callback_data="audience_select")],
                [InlineKeyboardButton(text="В основной канал", callback_data="audience_channel")],
                [InlineKeyboardButton(text="Отмена", callback_data="broadcast_cancel")]
            ])
            await callback.message.edit_text("Кому отправить?", reply_markup=kb)
            await state.set_state(BroadcastStates.select_audience)
            await callback.answer("Выбор")
        elif data == "audience_all":
            await callback.message.edit_text("Рассылка запущена → всем...")
            await callback.answer()
            delivered, failed, stats = await do_broadcast(state)
            stats_text = ", ".join([f"{k}:{v}" for k, v in stats.items() if v > 0]) or "нет"
            await callback.message.answer(
                f"✅ Готово. Доставлено: {delivered}, ошибок: {failed}\nДетали: {stats_text}"
            )
            await state.clear()
        elif data == "audience_select":
            await callback.message.edit_text("Пришлите user_id (по строкам, пробелам или запятым)")
            await state.set_state(BroadcastStates.waiting_for_user_list)
            await callback.answer("Ожидаю ID")
        elif data == "audience_channel":
            ok, info = await post_to_main_channel_from_state(state)
            if ok:
                await callback.message.answer(f"✅ Отправлено в канал: {info}")
            else:
                await callback.message.answer(f"❌ Не удалось отправить в канал: {info}")
            await state.clear()
            await callback.answer("Готово")
        elif data == "admin_templates_menu":
            cur.execute("SELECT id, name FROM broadcast_templates ORDER BY id DESC LIMIT 10")
            rows = cur.fetchall()
            kb_rows = [[InlineKeyboardButton(text="➕ Создать шаблон", callback_data="template_create")]]
            for tid, name in rows:
                kb_rows.append([InlineKeyboardButton(text=f"📤 {name}", callback_data=f"template_send_{tid}")])
                kb_rows.append([InlineKeyboardButton(text=f"🗑️ Удалить {name}", callback_data=f"template_delete_{tid}")])
            kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="admin_cancel")])
            await callback.message.edit_text(
                "🧩 Шаблоны рассылок\nВыберите действие:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
            )
            await callback.answer()
        elif data == "template_create":
            await callback.message.edit_text("Отправьте сообщение (текст/фото/видео/документ) для нового шаблона.")
            await state.set_state(BroadcastStates.waiting_for_template_content)
            await callback.answer("Ожидаю шаблон")
        elif data.startswith("template_send_"):
            template_id = int(data.split("_")[-1])
            cur.execute(
                "SELECT text, raw_text, parse_mode, entities_json, media_type, media_file_id, buttons_json FROM broadcast_templates WHERE id = ?",
                (template_id,)
            )
            row = cur.fetchone()
            if not row:
                await callback.answer("Шаблон не найден", show_alert=True)
                return
            await state.update_data(
                template_payload={
                    "text": row[0],
                    "raw_text": row[1] or row[0],
                    "parse_mode": row[2] or "HTML",
                    "entities_json": row[3] or "[]",
                    "media_type": row[4],
                    "media_file_id": row[5]
                },
                buttons_json=row[6] or "[]"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Всем", callback_data="audience_all")],
                [InlineKeyboardButton(text="Выборочно по ID", callback_data="audience_select")],
                [InlineKeyboardButton(text="В основной канал", callback_data="audience_channel")],
                [InlineKeyboardButton(text="Отмена", callback_data="broadcast_cancel")]
            ])
            await callback.message.edit_text("Шаблон выбран. Кому отправить?", reply_markup=kb)
            await state.set_state(BroadcastStates.select_audience)
            await callback.answer("Шаблон готов")
        elif data.startswith("template_delete_"):
            template_id = int(data.split("_")[-1])
            cur.execute("DELETE FROM broadcast_templates WHERE id = ?", (template_id,))
            conn.commit()
            await callback.message.answer("✅ Шаблон удален.")
            await callback.answer("Удалено")
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
            await state.clear()
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
    payload = extract_message_payload(message)
    await state.update_data(
        broadcast_content=message.model_dump_json(exclude_unset=True),
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        broadcast_payload=payload,
        buttons_json="[]"
    )
    preview_text = message.text or message.caption or "Сообщение без текста"
    preview = f"Предпросмотр рассылки:\n\n{preview_text[:500]}..."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запустить рассылку", callback_data="confirm_broadcast_yes")],
        [InlineKeyboardButton(text="🔗 Добавить кнопки", callback_data="broadcast_add_buttons")],
        [InlineKeyboardButton(text="💾 Сохранить как шаблон", callback_data="broadcast_save_template")],
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
    delivered, failed, stats = await do_broadcast(state, selected_user_ids)
    stats_text = ", ".join([f"{k}:{v}" for k, v in stats.items() if v > 0]) or "нет"
    await message.answer(f"✅ Готово. Доставлено: {delivered}, ошибок: {failed}\nДетали: {stats_text}")
    await state.clear()


@router.message(BroadcastStates.waiting_for_buttons)
async def process_broadcast_buttons(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    try:
        buttons = parse_buttons(message.text or "")
    except ValueError as e:
        await message.answer(f"❌ {e}\n\nПример:\nСайт | https://example.com")
        return
    await state.update_data(buttons_json=json.dumps(buttons, ensure_ascii=False))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запустить рассылку", callback_data="confirm_broadcast_yes")],
        [InlineKeyboardButton(text="✏️ Изменить кнопки", callback_data="broadcast_add_buttons")],
        [InlineKeyboardButton(text="Отмена", callback_data="broadcast_cancel")]
    ])
    await message.answer(f"✅ Кнопок добавлено: {len(buttons)}", reply_markup=kb)
    await state.set_state(BroadcastStates.confirm_broadcast)


@router.message(BroadcastStates.waiting_for_template_content)
async def process_template_content(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    payload = extract_message_payload(message)
    await state.update_data(template_payload=payload, buttons_json="[]")
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить кнопки", callback_data="template_skip_buttons")]
    ])
    await message.answer(
        "Теперь отправьте кнопки для шаблона (каждая с новой строки):\n"
        "`Текст | https://url`\n\n"
        "Если кнопки не нужны, отправьте: `-` или нажмите кнопку ниже.",
        parse_mode="Markdown",
        reply_markup=skip_kb
    )
    await state.set_state(BroadcastStates.waiting_for_template_buttons)


async def save_template_from_state(message: types.Message, state: FSMContext, name: str) -> bool:
    data = await state.get_data()
    payload = data.get("template_payload") or data.get("broadcast_payload")
    if not payload:
        await message.answer("❌ Нет данных шаблона. Отправьте сообщение заново.")
        await state.clear()
        return False
    buttons_json = data.get("buttons_json", "[]")
    try:
        cur.execute(
            """
            INSERT INTO broadcast_templates (
                name, text, raw_text, parse_mode, entities_json,
                media_type, media_file_id, buttons_json, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                payload.get("text", ""),
                payload.get("raw_text", ""),
                payload.get("parse_mode"),
                payload.get("entities_json", "[]"),
                payload.get("media_type"),
                payload.get("media_file_id"),
                buttons_json,
                message.from_user.id,
                datetime.now().isoformat()
            )
        )
        conn.commit()
    except sqlite3.IntegrityError:
        await message.answer("❌ Шаблон с таким названием уже существует.")
        return False
    await message.answer(f"✅ Шаблон `{name}` сохранен.", parse_mode="Markdown")
    await state.clear()
    return True


@router.message(BroadcastStates.waiting_for_template_buttons)
async def process_template_buttons(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return

    raw = (message.text or "").strip()
    if raw in {"-", "нет", "Нет", "NO", "no"}:
        await state.update_data(buttons_json="[]")
    else:
        try:
            buttons = parse_buttons(raw)
            await state.update_data(buttons_json=json.dumps(buttons, ensure_ascii=False))
        except ValueError as e:
            await message.answer(
                f"❌ {e}\n\n"
                "Пример:\nСайт | https://example.com\n"
                "Или отправьте `-`, чтобы пропустить кнопки.",
                parse_mode="Markdown"
            )
            return

    await message.answer("Введите название шаблона (уникальное):")
    await state.set_state(BroadcastStates.waiting_for_template_name)


@router.message(BroadcastStates.waiting_for_template_name)
async def process_template_name(message: types.Message, state: FSMContext):
    if message.from_user.id not in MODERATORS_IDS:
        await state.clear()
        return
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("❌ Название должно быть минимум 3 символа.")
        return
    await save_template_from_state(message, state, name)


async def send_template_message(user_id: int | str, payload: dict, buttons: list[dict] | None):
    markup = build_buttons_markup(buttons)
    text = payload.get("text") or None
    raw_text = payload.get("raw_text") or text or ""
    parse_mode = payload.get("parse_mode")
    entities_json = payload.get("entities_json", "[]")
    try:
        entities = json.loads(entities_json) if entities_json else []
    except json.JSONDecodeError:
        entities = []
    media_type = payload.get("media_type")
    media_file_id = payload.get("media_file_id")
    try:
        if media_type == "photo" and media_file_id:
            await bot.send_photo(
                chat_id=user_id,
                photo=media_file_id,
                caption=raw_text,
                caption_entities=entities or None,
                parse_mode=None if entities else parse_mode,
                reply_markup=markup
            )
        elif media_type == "video" and media_file_id:
            await bot.send_video(
                chat_id=user_id,
                video=media_file_id,
                caption=raw_text,
                caption_entities=entities or None,
                parse_mode=None if entities else parse_mode,
                reply_markup=markup
            )
        elif media_type == "document" and media_file_id:
            await bot.send_document(
                chat_id=user_id,
                document=media_file_id,
                caption=raw_text,
                caption_entities=entities or None,
                parse_mode=None if entities else parse_mode,
                reply_markup=markup
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=raw_text or " ",
                entities=entities or None,
                parse_mode=None if entities else parse_mode,
                reply_markup=markup
            )
    except Exception as e:
        # Фолбэк для сложной разметки: отправляем как обычный текст без parse_mode
        if "can't parse entities" in str(e).lower():
            plain = re.sub(r"<[^>]+>", "", raw_text).strip() or " "
            if media_type == "photo" and media_file_id:
                await bot.send_photo(chat_id=user_id, photo=media_file_id, caption=plain, reply_markup=markup)
            elif media_type == "video" and media_file_id:
                await bot.send_video(chat_id=user_id, video=media_file_id, caption=plain, reply_markup=markup)
            elif media_type == "document" and media_file_id:
                await bot.send_document(chat_id=user_id, document=media_file_id, caption=plain, reply_markup=markup)
            else:
                await bot.send_message(chat_id=user_id, text=plain, reply_markup=markup)
        else:
            raise


async def post_to_main_channel_from_state(state: FSMContext) -> tuple[bool, str]:
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    buttons = json.loads(data.get("buttons_json", "[]"))
    template_payload = data.get("template_payload")
    channel_id = get_main_channel_chat_id()
    if not channel_id:
        return False, "Не задан MAIN_CHANNEL_ID и не удалось вычислить канал по CHANNEL_LINK."
    try:
        if template_payload:
            await send_template_message(channel_id, template_payload, buttons)
        else:
            await bot.copy_message(
                chat_id=channel_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                reply_markup=build_buttons_markup(buttons)
            )
        return True, str(channel_id)
    except Exception as e:
        return False, str(e)


async def do_broadcast(state: FSMContext, selected_user_ids: list[int] | None = None) -> tuple[int, int, dict]:
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    buttons = json.loads(data.get("buttons_json", "[]"))
    template_payload = data.get("template_payload")

    if not template_payload and (not source_chat_id or not source_message_id):
        return 0, 0, {}

    if selected_user_ids is None:
        cur.execute("SELECT user_id FROM users")
        target_ids = [row[0] for row in cur.fetchall()]
    else:
        target_ids = list(dict.fromkeys(selected_user_ids))

    delivered = 0
    failed = 0
    stats = {"blocked": 0, "chat_not_found": 0, "deactivated": 0, "forbidden": 0, "other": 0}

    for user_id in target_ids:
        try:
            if template_payload:
                await send_template_message(user_id, template_payload, buttons)
            else:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                    reply_markup=build_buttons_markup(buttons)
                )
            delivered += 1
        except Exception as e:
            failed += 1
            stats[classify_send_error(e)] += 1
        await asyncio.sleep(0.03)

    return delivered, failed, stats

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
