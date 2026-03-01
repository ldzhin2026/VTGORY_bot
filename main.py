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

# Настройки
TOKEN = "8656659502:AAEr1hajHfDs0y-iqjoAWG6qT0Hw7P4IYpI"
CHANNEL_LINK = "https://t.me/tolkogori"
CHAT_LINK = "https://t.me/tolkogori_chat"
PHOTO_PATH = "welcome_photo.jpg"
ADMIN_ID = 7051676412
DB_PATH = "/app/data/subscribers.db"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# База данных
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

# Aiogram
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

# Хендлеры капчи (без изменений)
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
            [InlineKeyboardButton(text="🟢 СТРИМЫ НА KICK", url="https://vtgori.pro/kick")]
        ])
        await callback.message.reply("✅ Пройдено!\nДобро пожаловать!", reply_markup=kb, parse_mode="Markdown")
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

# Админ-меню
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

# Универсальный callback-хендлер
@router.callback_query()
async def universal_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"[CALLBACK] Получен от {callback.from_user.id}: {callback.data}")
    
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = callback.data

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
            await callback.answer("Выбор аудитории")

        elif data == "audience_all":
            await callback.message.edit_text("Рассылка запущена → всем...")
            await callback.answer()
            await do_broadcast(callback, state, "all")
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
                await callback.message.answer_document(
                    document=FSInputFile(DB_PATH),
                    caption=caption
                )
            await callback.answer("База отправлена")

        elif data == "admin_cancel":
            await callback.message.delete()
            await callback.answer("Меню закрыто")

        else:
            await callback.answer(f"Неизвестная кнопка: {data}", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка callback {data}: {e}", exc_info=True)
        await callback.message.answer(f"Ошибка: {str(e)}")

    await callback.answer()

# Обработка сообщения для рассылки (текстовый предпросмотр)
@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast_content(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.update_data(broadcast_content=message.model_dump_json(exclude_unset=True))
    
    preview_text = message.text or message.caption or "Сообщение без текста"
    preview = f"Предпросмотр рассылки (текстовая версия):\n\n{preview_text[:500]}..."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запустить рассылку", callback_data="confirm_broadcast_yes")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast_change")]
    ])
    
    await message.forward(chat_id=message.chat.id)
    await message.answer(
        preview + "\n\n(картинка, кнопки и анимированные эмодзи сохранятся при рассылке)\nПодтвердите или измените ↓",
        reply_markup=kb
    )
    
    await state.set_state(BroadcastStates.confirm_broadcast)

# Выбор аудитории и отправка
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
    await callback.message.edit_text("Рассылка запущена → всем...")
    await callback.answer()
    await do_broadcast(callback, state, "all")
    await state.clear()

@router.callback_query(F.data == "audience_select", BroadcastStates.select_audience)
async def ask_selective_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Пришлите user_id (по строкам, пробелам или запятым)")
    await state.set_state(BroadcastStates.waiting_for_user_list)
    await callback.answer()

@router.message(BroadcastStates.waiting_for_user_list)
async def process_selective_list(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    if not raw:
        await message.reply("Пусто. Отмена.")
        await state.clear()
        return
    ids = [int(p.strip()) for p in raw.replace(",", " ").split() if p.strip().isdigit()]
    if not ids:
        await message.reply("Нет валидных ID.")
        return
    unique = list(set(ids))
    await message.reply(f"Рассылка → {len(unique)} ID...")
    await do_broadcast(message, state, "selective", unique)
    await state.clear()

# Исправленная рассылка (используем bot.send_copy)
async def do_broadcast(event, state: FSMContext, target: str, user_ids=None):
    data = await state.get_data()
    content_json = data.get("broadcast_content")
    if not content_json:
        text = "Сообщение не найдено."
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
    failed_reasons = []

    for uid in recipients:
        try:
            await bot.send_copy(
                chat_id=uid,
                from_chat_id=msg.chat.id,
                message_id=msg.message_id
            )
            success += 1
            await asyncio.sleep(0.35)
        except Exception as e:
            failed += 1
            err = str(e)
            logger.warning(f"Не отправлено {uid}: {err}")
            failed_reasons.append(f"ID {uid}: {err[:100]}...")

    report = f"Завершено:\nУспешно: {success}\nНе удалось: {failed}\nВсего: {len(recipients)}"

    if failed_reasons:
        report += "\n\nПричины неудач:\n" + "\n".join(failed_reasons[:5])

    if hasattr(event, 'reply'):
        await event.reply(report)
    else:
        await event.message.answer(report)

# Импорт базы
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

# Запуск
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query"]
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        conn.close()
