import asyncio
import random
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройки
TOKEN = "8656659502:AAEr1hajHfDs0y-iqjoAWG6qT0Hw7P4IYpI"
CHANNEL_LINK = "https://t.me/tolkogori"
CHAT_LINK = "https://t.me/tolkogori_chat"
PHOTO_PATH = "welcome_photo.jpg"  # или None

ADMIN_ID = 7051676412  # твой ID

# База данных
conn = sqlite3.connect("subscribers.db")
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT,
    attempts_used INTEGER
)''')
conn.commit()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

class CaptchaStates(StatesGroup):
    waiting_for_answer = State()

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
    cur.execute('''INSERT OR IGNORE INTO users 
                   (user_id, username, first_name, joined_at, attempts_used)
                   VALUES (?, ?, ?, ?, ?)''',
                (user.id, username, user.first_name, now, attempts_used))
    conn.commit()
    logging.info(f"Добавлен: {user.id} (@{username or 'нет'}) — попыток: {attempts_used}")

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
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

    if PHOTO_PATH:
        try:
            await message.answer_photo(
                photo=FSInputFile(PHOTO_PATH),
                caption=text,
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logging.warning(f"Фото не отправлено: {e}")

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
            "Добро пожаловать на канал стримеров ВЫШЕ ТОЛЬКО ГОРЫ!\n\n"
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
            await callback.message.reply("❌ Попытки закончились.\n"
                                         "Попробуйте снова — /start")
            await state.clear()
            await callback.answer("Попытки исчерпаны", show_alert=True)

# Команда /stats — просмотр всей базы (только для тебя)
@router.message(F.command("stats"))
async def stats_handler(message: types.Message):
    logging.info(f"Получена команда /stats от {message.from_user.id}")

    if message.from_user.id != ADMIN_ID:
        await message.reply("Доступ запрещён. Только админ может смотреть базу.")
        return

    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]

    if total == 0:
        await message.reply("База пустая. Никто ещё не прошёл проверку.")
        return

    cur.execute("""
        SELECT user_id, username, first_name, joined_at, attempts_used 
        FROM users 
        ORDER BY joined_at DESC
    """)
    users = cur.fetchall()

    response = f"📊 Статистика базы:\nВсего пользователей: {total}\n\n"
    response += "Список (от новых к старым):\n\n"

    for i, (user_id, username, first_name, joined_at, attempts) in enumerate(users, 1):
        username = f"@{username}" if username else "нет username"
        date = joined_at[:19]  # обрезаем до даты и времени
        response += f"{i}. {username} ({first_name}) — {date} — попыток: {attempts}\n"

    await message.reply(response, parse_mode="Markdown")

# Рассылка — команда /broadcast (только для тебя)
@router.message(F.text.startswith('/broadcast'))
async def broadcast_handler(message: types.Message):
    logging.info(f"Получена команда /broadcast от {message.from_user.id}")

    if message.from_user.id != ADMIN_ID:
        await message.reply("Доступ запрещён. Только админ может рассылать.")
        return

    if len(message.text.split()) < 2:
        await message.reply("Используй: /broadcast текст для рассылки")
        return

    text = message.text.split(maxsplit=1)[1].strip()
    if not text:
        await message.reply("Напиши текст после /broadcast")
        return

    await message.reply("Рассылка начата...")

    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    if not users:
        await message.reply("В базе никого нет. Пройди капчу сам для теста.")
        return

    success = 0
    failed = 0

    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text, parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.5)  # задержка
        except Exception as e:
            failed += 1
            logging.warning(f"Не удалось отправить {user_id}: {e}")

    await message.reply(
        f"Рассылка завершена!\n"
        f"Отправлено: {success}\n"
        f"Не удалось: {failed}\n"
        f"Всего в базе: {len(users)}"
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        conn.close()


