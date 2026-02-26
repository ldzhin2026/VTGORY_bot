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
PHOTO_PATH = "welcome_photo.jpg"          # если файла нет — будет просто текст
ADMIN_ID = 7051676412

# Путь к базе — Railway volume
DB_PATH = "/app/data/subscribers.db"

# Создаём директорию, если её нет
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ────────────────────────────────────────────────
# Логирование
# ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
# Подключение к базе (с защитой)
# ────────────────────────────────────────────────

try:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id       INTEGER PRIMARY KEY,
        username      TEXT,
        first_name    TEXT,
        joined_at     TEXT,
        attempts_used INTEGER DEFAULT 0
    )''')
    conn.commit()
    
    logger.info(f"База подключена: {DB_PATH}")
    logger.info(f"Файл существует: {os.path.exists(DB_PATH)}")
    if os.path.exists(DB_PATH):
        size = os.path.getsize(DB_PATH)
        logger.info(f"Размер базы: {size} байт ({size / 1024:.2f} КБ)")
except Exception as e:
    logger.error(f"ОШИБКА при создании/подключении базы: {type(e).__name__} → {e}", exc_info=True)
    raise  # падаем сразу, чтобы увидеть ошибку в логах Railway

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
    
    cur.execute('''INSERT OR REPLACE INTO users
                   (user_id, username, first_name, joined_at, attempts_used)
                   VALUES (?, ?, ?, ?, ?)''',
                (user.id, username, user.first_name, now, attempts_used))
    conn.commit()
    
    logger.info(f"Сохранён: {user.id} (@{username or 'нет'}) — попыток: {attempts_used}")

# ────────────────────────────────────────────────
# Хендлеры
# ────────────────────────────────────────────────

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    logger.info(f"/start от {message.from_user.id}")
    
    text = (
        "📜 **Правила канала ВЫШЕ ТОЛЬКО ГОРЫ**\n\n"
        "• Обязательная подписка на канал\n"
        "• Запрещены: спам, оскорбления, реклама без разрешения\n\n"
        "Пройдите проверку ↓"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 ПОДПИСАТЬСЯ", callback_data="start_captcha")
    ]])
    
    try:
        if PHOTO_PATH and os.path.isfile(PHOTO_PATH):
            await message.answer_photo(
                photo=FSInputFile(PHOTO_PATH),
                caption=text,
                reply_markup=kb,
                parse_mode="Markdown"
            )
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Ошибка отправки фото: {e}")
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "start_captcha")
async def start_captcha(callback: types.CallbackQuery, state: FSMContext):
    question, correct, variants = generate_task()
    
    await state.update_data(
        correct=correct,
        attempts=3,
        variants=variants,
        attempts_used=0
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[:2]],
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[2:]]
    ])
    
    await callback.message.reply(
        f"<b>Решите:</b>\n\n{question}\n\nВыберите ответ (3 попытки)",
        reply_markup=kb,
        parse_mode="HTML"
    )
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
            [InlineKeyboardButton(text="🎁 КАНАЛ", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="💬 ЧАТ", url=CHAT_LINK)],
            [InlineKeyboardButton(text="🟢 KICK", url="https://vtgori.pro/kick")]
        ])
        
        await callback.message.reply(
            "✅ Пройдено!\nДобро пожаловать!",
            reply_markup=kb,
            parse_mode="Markdown"
        )
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


@router.message(F.text.startswith("/getdb"))
async def get_db_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Нет доступа")
        return
    
    if not os.path.exists(DB_PATH):
        await message.reply("База не найдена")
        return
    
    size_kb = os.path.getsize(DB_PATH) / 1024
    caption = f"subscribers.db • {size_kb:.1f} КБ • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    try:
        await message.answer_document(
            document=FSInputFile(DB_PATH),
            caption=caption
        )
    except Exception as e:
        await message.reply(f"Ошибка отправки: {str(e)}")


# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────

async def main():
    logger.info("Бот стартует...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"]
        )
    except Exception as e:
        logger.error(f"Краш в polling: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    finally:
        if 'conn' in globals():
            conn.close()
            logger.info("База закрыта")
