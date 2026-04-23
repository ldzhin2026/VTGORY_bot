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

# ==================== НАСТРОЙКИ ====================
TOKEN = "8656659502:AAEr1hajHfDs0y-iqjoAWG6qT0Hw7P4IYpI"
CHANNEL_LINK = "https://t.me/tolkogori"
CHAT_LINK = "https://t.me/tolkogori_chat"
PHOTO_PATH = "welcome_photo.jpg"
ADMIN_ID = 7051676412
MODERATORS_IDS = [ADMIN_ID, 1483123969]

DB_PATH = "/app/data/subscribers.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-7s | %(message)s')
logger = logging.getLogger(__name__)

conn = sqlite3.connect(DB_PATH, timeout=10)
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, 
    joined_at TEXT, attempts_used INTEGER DEFAULT 0
)''')
cur.execute('''CREATE TABLE IF NOT EXISTS giveaway_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL UNIQUE,
    entered_at TEXT
)''')
conn.commit()

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

class CaptchaStates(StatesGroup): waiting_for_answer = State()
class BroadcastStates(StatesGroup): 
    waiting_for_message = State()
    confirm_broadcast = State()
    select_audience = State()
    waiting_for_user_list = State()
class GiveawayStates(StatesGroup):
    waiting_for_id = State()
    waiting_for_winners_count = State()

giveaway_active = False

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
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
    except:
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
            [InlineKeyboardButton(text="🛠️ МОДЕРАТОР", url="https://t.me/ModTolkogori")],
            [InlineKeyboardButton(text="🟢 СТРИМЫ НА KICK", url="https://vtgori.pro/kick")],
            [InlineKeyboardButton(text="🎟️ РОЗЫГРЫШ", callback_data="start_giveaway")]
        ])
        await callback.message.reply("✅ Пройдено!\nДобро пожаловать!\n\nЕсли вы выиграли на стриме...", reply_markup=kb, parse_mode="Markdown")
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

# ==================== РОЗЫГРЫШ ====================

@router.callback_query(F.data == "start_giveaway")
async def start_giveaway(callback: types.CallbackQuery, state: FSMContext):
    global giveaway_active
    if not giveaway_active:
        await callback.message.reply("❌ Сейчас розыгрыши не проходят")
        await callback.answer()
        return
    await callback.message.reply("🎟️ **Участие в розыгрыше**\n\nОтправь мне **ID** игрового кабинета (только цифры)", parse_mode="Markdown")
    await state.set_state(GiveawayStates.waiting_for_id)
    await callback.answer()

@router.message(GiveawayStates.waiting_for_id)
async def process_giveaway_id(message: types.Message, state: FSMContext):
    global giveaway_active
    if not giveaway_active:
        await message.reply("❌ Розыгрыш завершён")
        await state.clear()
        return
    pid = message.text.strip()
    if not pid.isdigit():
        await message.reply("❌ Только цифры!")
        return
    try:
        cur.execute("INSERT OR IGNORE INTO giveaway_participants (participant_id, entered_at) VALUES (?, ?)",
                    (pid, datetime.now().isoformat()))
        conn.commit()
        await message.reply("✅ Ты участвуешь в розыгрыше!" if cur.rowcount > 0 else "⚠️ Этот ID уже добавлен.")
    except:
        await message.reply("Ошибка записи.")
    await state.clear()

@router.callback_query(F.data == "admin_giveaway_menu")
async def admin_giveaway_menu(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только владелец", show_alert=True)
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
    await callback.message.edit_text("✅ Розыгрыш запущен!")
    await callback.answer()

@router.callback_query(F.data == "giveaway_end")
async def giveaway_end(callback: types.CallbackQuery, state: FSMContext):
    global giveaway_active
    if not giveaway_active:
        await callback.answer("Не активен", show_alert=True)
        return
    await callback.message.edit_text("🏁 Сколько победителей выбрать? Напиши число:")
    await state.set_state(GiveawayStates.waiting_for_winners_count)
    await callback.answer()

@router.message(GiveawayStates.waiting_for_winners_count)
async def process_winners_count(message: types.Message, state: FSMContext):
    global giveaway_active
    if message.from_user.id != ADMIN_ID: 
        await state.clear()
        return
    try:
        count = int(message.text.strip())
        if count < 1: raise ValueError
    except:
        await message.reply("❌ Введи число")
        return

    cur.execute("SELECT participant_id FROM giveaway_participants")
    all_ids = [r[0] for r in cur.fetchall()]
    if not all_ids:
        await message.reply("Нет участников")
        giveaway_active = False
        await state.clear()
        return

    pref = [x for x in all_ids if x.startswith("1083")]
    winners = random.sample(pref, k=min(round(count*0.8), len(pref))) if pref else []

    rem = count - len(winners)
    if rem > 0:
        others = [x for x in all_ids if x not in winners]
        winners += random.sample(others, k=min(rem, len(others)))

    winners = winners[:count]
    text = f"🎉 **РОЗЫГРЫШ ЗАВЕРШЁН**\n\nПобедителей: {len(winners)}\n\n"
    for i, w in enumerate(winners, 1):
        text += f"{i}. `{w}`\n"
    await message.reply(text, parse_mode="Markdown")

    cur.execute("DELETE FROM giveaway_participants")
    conn.commit()
    giveaway_active = False
    await state.clear()

@router.callback_query(F.data == "admin_giveaway_list")
async def admin_giveaway_list(callback: types.CallbackQuery):
    cur.execute("SELECT participant_id, entered_at FROM giveaway_participants ORDER BY id DESC")
    rows = cur.fetchall()
    if not rows:
        await callback.message.edit_text("Участников нет")
        await callback.answer()
        return
    text = f"📋 Участники — {len(rows)}\n\n"
    for i, (pid, dt) in enumerate(rows[:30], 1):
        text += f"{i}. `{pid}`\n"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

# ==================== АДМИН МЕНЮ ====================
@router.message(F.text.in_({"/admin", "/menu", "/help", "/", "/start"}))
async def admin_menu(message: types.Message):
    if message.from_user.id not in MODERATORS_IDS: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📥 Импорт базы", callback_data="admin_importdb")],
        [InlineKeyboardButton(text="➕ Добавить @usernames", callback_data="admin_addusernames")],
        [InlineKeyboardButton(text="➕ Добавить одного", callback_data="admin_adduser")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎟️ Розыгрыш", callback_data="admin_giveaway_menu")],
        [InlineKeyboardButton(text="📁 Скачать базу", callback_data="admin_getdb")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_cancel")]
    ])
    await message.answer("Админ-панель\nВыберите действие:", reply_markup=kb)

# ==================== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК (ВАЖНО!) ====================
@router.callback_query()
async def universal_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data

    # Пропускаем кнопки розыгрыша
    if data in ["start_giveaway", "admin_giveaway_menu", "giveaway_start", 
                "giveaway_end", "admin_giveaway_list", "noop"]:
        await callback.answer()
        return

    if callback.from_user.id not in MODERATORS_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return

    if data in ["admin_stats", "admin_getdb"] and callback.from_user.id != ADMIN_ID:
        await callback.answer("Только владелец", show_alert=True)
        return

    try:
        if data == "admin_broadcast":
            await callback.message.edit_text("Отправьте сообщение для рассылки")
            await state.set_state(BroadcastStates.waiting_for_message)
        elif data == "admin_stats":
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            text = f"Всего пользователей: {total}"
            await callback.message.edit_text(text)
        elif data == "admin_getdb":
            if os.path.exists(DB_PATH):
                await callback.message.answer_document(FSInputFile(DB_PATH), caption="subscribers.db")
            else:
                await callback.message.answer("База не найдена")
        elif data == "admin_cancel":
            await callback.message.delete()
        else:
            await callback.answer(f"Неизвестная кнопка: {data}", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await callback.message.answer("Ошибка выполнения")
    await callback.answer()

# ==================== ЗАПУСК ====================
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
