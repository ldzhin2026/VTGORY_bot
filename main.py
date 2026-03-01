import asyncio
import random
import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ────────────────────────────────────────────────
# Настройки
# ────────────────────────────────────────────────
TOKEN = "8656659502:AAEr1hajHfDs0y-iqjoAWG6qT0Hw7P4IYpI"
CHANNEL_LINK = "https://t.me/tolkogori"
CHAT_LINK = "https://t.me/tolkogori_chat"
PHOTO_PATH = "welcome_photo.jpg"
ADMIN_ID = 7051676412
DB_PATH = "/app/data/subscribers.db"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ────────────────────────────────────────────────
# Логирование (теперь будет видно всё)
# ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
# База
# ────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH, timeout=10)
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    attempts_used INTEGER DEFAULT 0
)''')
conn.commit()

# ────────────────────────────────────────────────
# Aiogram
# ────────────────────────────────────────────────
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

# ────────────────────────────────────────────────
# Вспомогательные функции
# ────────────────────────────────────────────────
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
    cur.execute('''INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?)''',
                (user.id, username, user.first_name, now, attempts_used))
    conn.commit()

# ────────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ────────────────────────────────────────────────
@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    # ... (твой обычный старт — без изменений)
    text = "📜 **Правила канала ВЫШЕ ТОЛЬКО ГОРЫ**\n\n• Обязательная подписка\n• Запрещены: спам, оскорбления\n\nПройдите проверку ↓"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 ПОДПИСАТЬСЯ", callback_data="start_captcha")]])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "start_captcha")
async def start_captcha(callback: types.CallbackQuery, state: FSMContext):
    # ... (капча без изменений)
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
    # ... (капча без изменений — оставил как было)
    # (полный код капчи можешь взять из предыдущей версии, он не менялся)

# ─── АДМИН-МЕНЮ ───
@router.message(F.text.in_({"/admin", "/menu", "/help", "/", "/start"}))
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📥 Импорт базы", callback_data="admin_importdb")],
        [InlineKeyboardButton(text="➕ Добавить @usernames", callback_data="admin_addusernames")],
        [InlineKeyboardButton(text="➕ Добавить одного", callback_data="admin_adduser")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📁 Скачать базу", callback_data="admin_getdb")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_cancel")]
    ])
    await message.answer("Админ-панель\nВыберите действие:", reply_markup=kb)

# ─── ГЛАВНЫЙ ХЕНДЛЕР ВСЕХ КНОПОК (самое важное исправление) ───
@router.callback_query()
async def all_callbacks(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = callback.data
    logger.info(f"✅ CALLBACK ПОЛУЧЕН: {data} от пользователя {callback.from_user.id}")

    if data == "admin_broadcast":
        await callback.message.edit_text("Отправьте сообщение для рассылки (текст, фото, видео и т.д.)")
        await state.set_state(BroadcastStates.waiting_for_message)

    elif data == "admin_importdb":
        await callback.message.edit_text("Пришлите файл базы (.db)")

    elif data == "admin_addusernames":
        await callback.message.edit_text("Пришлите список @username (каждый с новой строки)")

    elif data == "admin_adduser":
        await callback.message.edit_text("Напишите: /adduser @username 123456789 или просто ID")

    elif data == "admin_stats":
        await stats_handler(callback.message)

    elif data == "admin_getdb":
        await get_db_handler(callback.message)

    elif data == "admin_cancel":
        await callback.message.delete()

    else:
        await callback.answer(f"Неизвестная кнопка: {data}", show_alert=True)

    await callback.answer()   # обязательно!

# ─── РАССЫЛКА, ИМПОРТ, СТАТИСТИКА и т.д. (полностью как раньше) ───
# (я оставил их без изменений, чтобы не раздувать сообщение — они работают как в предыдущей версии)

# ────────────────────────────────────────────────
# Запуск (самое важное исправление здесь!)
# ────────────────────────────────────────────────
async def main():
    logger.info("Бот запущен — ожидаем callback_query...")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query"]   # ← КРИТИЧНО!
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        conn.close()
