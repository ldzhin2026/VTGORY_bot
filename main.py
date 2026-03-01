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
# Логирование
# ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────
# База данных
# ────────────────────────────────────────────────
try:
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
    logger.info(f"База подключена: {DB_PATH}")
except Exception as e:
    logger.error(f"Ошибка базы: {e}", exc_info=True)
    raise

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
    waiting_for_message     = State()
    confirm_broadcast       = State()
    select_audience         = State()
    waiting_for_user_list   = State()

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
    await state.update_data(correct=correct, attempts=3, variants=variants, attempts_used=0)
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
            [InlineKeyboardButton(text="🎁 ТЕЛЕГРАМ КАНАЛ", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="💬 НАШ ЧАТ", url=CHAT_LINK)],
            [InlineKeyboardButton(text="🟢 СТРИМЫ НА KICK", url="https://vtgori.pro/kick")]
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

# ─── АДМИН-МЕНЮ (главное нововведение) ───
@router.message(F.text.in_({"/admin", "/menu", "/help", "/"}))
@router.message(F.text.startswith("/"))
async def admin_menu_handler(message: types.Message):
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

    await message.answer(
        "Админ-панель\nВыберите действие:",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("admin_"))
async def admin_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    action = callback.data.replace("admin_", "")

    if action == "broadcast":
        await callback.message.edit_text("Отправьте сообщение для рассылки")
        await state.set_state(BroadcastStates.waiting_for_message)

    elif action == "importdb":
        await callback.message.edit_text("Пришлите файл базы (.db) для импорта")

    elif action == "addusernames":
        await callback.message.edit_text(
            "Пришлите список @username (каждый с новой строки)"
        )

    elif action == "adduser":
        await callback.message.edit_text(
            "Формат:\n/adduser @username 123456789\nили /adduser 123456789"
        )

    elif action == "stats":
        await stats_handler(callback.message)

    elif action == "getdb":
        await get_db_handler(callback.message)

    elif action == "cancel":
        await callback.message.edit_text("Меню закрыто")
        await state.clear()

    await callback.answer()

# ─── РАССЫЛКА (broadcast) ───
@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast_content(message: types.Message, state: FSMContext):
    await state.update_data(broadcast_content=message.model_dump_json(exclude_unset=True))
    preview = "Предпросмотр:\n\n"
    if message.text:
        preview += message.text[:200] + ("..." if len(message.text) > 200 else "")
    elif message.caption:
        preview += f"Подпись: {message.caption[:150]}..."
    else:
        preview += f"Тип: {message.content_type}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запустить", callback_data="confirm_broadcast_yes")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast_cancel")]
    ])
    await message.forward(chat_id=message.chat.id)
    await message.answer(preview + "\n\nПодтвердите ↓", reply_markup=kb)
    await state.set_state(BroadcastStates.confirm_broadcast)

@router.callback_query(F.data == "confirm_broadcast_yes", BroadcastStates.confirm_broadcast)
async def ask_audience(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Всем", callback_data="audience_all")],
        [InlineKeyboardButton(text="Выборочно по ID", callback_data="audience_select")],
        [InlineKeyboardButton(text="Отмена", callback_data="broadcast_cancel")]
    ])
    await callback.message.edit_text("Кому отправить?", reply_markup=kb)
    await state.set_state(BroadcastStates.select_audience)
    await callback.answer()

@router.callback_query(F.data == "audience_all", BroadcastStates.select_audience)
async def broadcast_to_all(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Рассылка → всем...")
    await callback.answer()
    await do_broadcast(callback, state, "all")
    await state.clear()

@router.callback_query(F.data == "audience_select", BroadcastStates.select_audience)
async def ask_selective_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Пришлите user_id (по строкам, через пробел/запятую)")
    await state.set_state(BroadcastStates.waiting_for_user_list)
    await callback.answer()

@router.message(BroadcastStates.waiting_for_user_list)
async def process_selective_list(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    if not raw:
        await message.reply("Пусто. Отмена.")
        await state.clear()
        return
    ids = []
    for part in raw.replace(",", " ").split():
        if part.strip().isdigit():
            ids.append(int(part.strip()))
    if not ids:
        await message.reply("Нет валидных ID.")
        return
    unique = list(set(ids))
    await message.reply(f"Рассылка → {len(unique)} ID...")
    await do_broadcast(message, state, "selective", unique)
    await state.clear()

async def do_broadcast(event, state: FSMContext, target: str, user_ids: list | None = None):
    data = await state.get_data()
    content_json = data.get("broadcast_content")
    if not content_json:
        text = "Сообщение не найдено. Начните заново."
        if hasattr(event, 'reply'):
            await event.reply(text)
        else:
            await event.message.answer(text)
        return
    msg = types.Message.model_validate_json(content_json)
    if target == "all":
        cur.execute("SELECT user_id FROM users")
        recipients = [r[0] for r in cur.fetchall()]
    elif target == "selective" and user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        cur.execute(f"SELECT user_id FROM users WHERE user_id IN ({placeholders})", user_ids)
        recipients = [r[0] for r in cur.fetchall()]
    else:
        recipients = []
    if not recipients:
        text = "Нет получателей."
        if hasattr(event, 'reply'):
            await event.reply(text)
        else:
            await event.message.answer(text)
        return
    success = failed = 0
    for uid in recipients:
        try:
            await msg.send_copy(chat_id=uid)
            success += 1
            await asyncio.sleep(0.35)
        except Exception as e:
            failed += 1
            logger.warning(f"Не отправлено {uid}: {e}")
    report = f"Завершено:\nУспешно: {success}\nНе удалось: {failed}\nВсего: {len(recipients)}"
    if hasattr(event, 'reply'):
        await event.reply(report)
    else:
        await event.message.answer(report)

@router.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отменено")
    await callback.answer()

# ─── ИМПОРТ БАЗЫ ───
@router.message(F.document & (F.from_user.id == ADMIN_ID))
async def process_import_db(message: types.Message):
    if not message.document.file_name.lower().endswith(('.db', '.sqlite', '.sqlite3')):
        return
    await message.reply("Обрабатываю...")
    file = await bot.get_file(message.document.file_id)
    tmp = f"/tmp/import_{int(datetime.now().timestamp())}.db"
    await bot.download_file(file.file_path, tmp)
    try:
        ic = sqlite3.connect(tmp)
        icur = ic.cursor()
        icur.execute("SELECT user_id, username, first_name, joined_at, attempts_used FROM users")
        rows = icur.fetchall()
        ic.close()
        added = skipped = 0
        for uid, un, fn, ja, au in rows:
            cur.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,))
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                (uid, un, fn or "imported", ja or datetime.now().isoformat(), au or 0)
            )
            conn.commit()
            added += 1
        os.remove(tmp)
        await message.reply(f"Импорт: +{added} | уже было {skipped} | всего {len(rows)}")
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")
        if os.path.exists(tmp):
            os.remove(tmp)

# ─── ДОБАВЛЕНИЕ ПО USERNAME ───
@router.message(F.text.startswith("/addusernames"))
async def add_usernames(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    lines = [l.strip().lstrip("@") for l in message.text.splitlines()[1:] if l.strip()]
    if not lines:
        await message.reply("Список пуст.")
        return
    added = 0
    for un in lines:
        if un:
            fake = types.User(id=0, is_bot=False, first_name="imported", username=un)
            save_user(fake, 0)
            added += 1
    await message.reply(f"Добавлено {added} username (user_id=0)")

# ─── СТАТИСТИКА ───
@router.message(F.text.startswith("/stats"))
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        text = f"Всего: {total}\n"
        if total > 0:
            cur.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT 5")
            for row in cur.fetchall():
                text += f"{row[0]} @{row[1]} {row[3][:19]} попыток: {row[4]}\n"
        await message.reply(text or "База пуста")
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")

# ─── СКАЧАТЬ БАЗУ ───
@router.message(F.text.startswith("/getdb"))
async def get_db_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not os.path.exists(DB_PATH):
        await message.reply("База не найдена")
        return
    size = os.path.getsize(DB_PATH) / 1024
    await message.answer_document(FSInputFile(DB_PATH), caption=f"База • {size:.1f} КБ")

# ────────────────────────────────────────────────
# Запуск
# ────────────────────────────────────────────────
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        if 'conn' in globals():
            conn.close()
