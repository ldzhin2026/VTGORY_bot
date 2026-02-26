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
PHOTO_PATH = "welcome_photo.jpg"          # приветственное фото (или None)
ADMIN_ID = 7051676412                     # твой Telegram ID

# Путь к базе — используем volume /app/data
DB_DIR = "/app/data"
DB_FILENAME = "subscribers.db"
DB_PATH = os.path.join(DB_DIR, DB_FILENAME)

# Создаём папку, если её нет (на всякий случай)
os.makedirs(DB_DIR, exist_ok=True)

# ────────────────────────────────────────────────
# Логирование
# ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ────────────────────────────────────────────────
# Подключение к базе
# ────────────────────────────────────────────────

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    joined_at   TEXT,
    attempts_used INTEGER DEFAULT 0
)''')
conn.commit()

logging.info(f"База данных подключена: {DB_PATH}")
logging.info(f"Файл существует? {os.path.exists(DB_PATH)}")
if os.path.exists(DB_PATH):
    size = os.path.getsize(DB_PATH)
    logging.info(f"Размер базы: {size} байт ({size / 1024:.2f} КБ)")

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
    logging.info(f"Сохранён пользователь {user.id} (@{username or 'нет'}) — попыток: {attempts_used}")

# ────────────────────────────────────────────────
# Хендлеры
# ────────────────────────────────────────────────

@router.message(F.text.startswith("/start"))
async def start_handler(message: types.Message, state: FSMContext):
    logging.info(f"/start от {message.from_user.id}")
    
    text = (
        "📜 **Правила канала ВЫШЕ ТОЛЬКО ГОРЫ**\n\n"
        "• Обязательная подписка на канал\n"
        "• Запрещены: спам, оскорбления, реклама без разрешения\n"
        "• Нажимая кнопку ниже, вы соглашаетесь с правилами\n\n"
        "Пройдите простую проверку ↓"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 ПОДПИСАТЬСЯ", callback_data="start_captcha")
    ]])
    
    if PHOTO_PATH and os.path.exists(PHOTO_PATH):
        try:
            await message.answer_photo(
                photo=FSInputFile(PHOTO_PATH),
                caption=text,
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logging.warning(f"Не удалось отправить фото: {e}")
    
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "start_captcha")
async def start_captcha(callback: types.CallbackQuery, state: FSMContext):
    question, correct, variants = generate_task()
    
    await state.update_data(
        correct=correct,
        attempts=3,
        question=question,
        variants=variants,
        attempts_used=0
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[:2]],
        [InlineKeyboardButton(text=str(v), callback_data=f"captcha_{v}") for v in variants[2:]]
    ])
    
    await callback.message.reply(
        f"Решите пример:\n\n<b>{question}</b>\n\n"
        "Выберите правильный ответ\n"
        "У вас 3 попытки",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer("Капча запущена!")


@router.callback_query(F.data.startswith("captcha_"))
async def check_answer(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    correct = data["correct"]
    attempts = data.get("attempts", 3)
    attempts_used = data.get("attempts_used", 0) + (3 - attempts)
    
    answer_str = callback.data.split("_")[1]
    try:
        answer = int(answer_str)
    except ValueError:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    
    if answer == correct:
        save_user(callback.from_user, attempts_used + 1)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 НАШ ТЕЛЕГРАМ КАНАЛ ТУТ 🎁", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="💬 НАШ ЧАТ ТУТ 💬", url=CHAT_LINK)],
            [InlineKeyboardButton(text="🟢 KICK СТРИМЫ НА KICK 🟢", url="https://vtgori.pro/kick")]
        ])
        
        await callback.message.reply(
            "✅ Отлично! Вы прошли проверку.\n"
            "Добро пожаловать в Телеграм канал стримеров ВЫШЕ ТОЛЬКО ГОРЫ!\n\n"
            "Основные ссылки:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await state.clear()
        await callback.answer("Добро пожаловать!")
    else:
        attempts -= 1
        attempts_used += 1
        await state.update_data(attempts=attempts, attempts_used=attempts_used)
        
        if attempts > 0:
            await callback.answer(f"Неверно • Осталось попыток: {attempts}", show_alert=True)
        else:
            await callback.message.reply("❌ Попытки закончились.\nПопробуйте снова — /start")
            await state.clear()
            await callback.answer("Попытки исчерпаны", show_alert=True)


@router.message(F.text.startswith("/stats"))
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Доступ запрещён.")
        return
    
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    
    if total == 0:
        await message.reply("База пустая.")
        return
    
    cur.execute("""
        SELECT user_id, username, first_name, joined_at, attempts_used
        FROM users
        ORDER BY joined_at DESC
    """)
    users = cur.fetchall()
    
    response = f"📊 Всего пользователей: {total}\n\n"
    for i, (uid, un, fn, ja, att) in enumerate(users, 1):
        un = f"@{un}" if un else "нет"
        date = ja[:19].replace("T", " ")
        response += f"{i}. {un} ({fn}) — {date} — попыток: {att}\n"
        
        if len(response) > 3800:
            await message.reply(response, parse_mode="Markdown")
            response = ""
    
    if response:
        await message.reply(response, parse_mode="Markdown")


@router.message(F.text.startswith("/broadcast"))
async def broadcast_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Доступ запрещён.")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply("Используй: /broadcast текст сообщения")
        return
    
    text = parts[1].strip()
    await message.reply("Рассылка запущена...")
    
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    
    if not users:
        await message.reply("База пуста.")
        return
    
    success = failed = 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text, parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            failed += 1
            logging.warning(f"Не отправлено {user_id}: {e}")
    
    await message.reply(
        f"Рассылка завершена:\n"
        f"Успешно: {success}\n"
        f"Не удалось: {failed}\n"
        f"Всего: {len(users)}"
    )


@router.message(F.text.startswith("/getdb"))
async def get_db_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Только админ может скачать базу.")
        return
    
    if not os.path.exists(DB_PATH):
        await message.reply("Файл базы не найден.")
        return
    
    size_kb = os.path.getsize(DB_PATH) / 1024
    caption = f"subscribers.db • {size_kb:.1f} КБ • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    try:
        await message.answer_document(
            document=FSInputFile(DB_PATH),
            caption=caption
        )
        logging.info(f"База отправлена админу {message.from_user.id}")
    except Exception as e:
        await message.reply(f"Ошибка при отправке файла: {str(e)}")


# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────

async def main():
    logging.info("Бот запускается...")
    await dp.start_polling(bot, allowed_updates=types.AllowedUpdates.MESSAGE + types.AllowedUpdates.CALLBACK_QUERY)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        conn.close()
        logging.info("Соединение с базой закрыто")
