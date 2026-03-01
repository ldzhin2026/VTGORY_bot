@router.callback_query()
async def universal_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"[CALLBACK] Получен от {callback.from_user.id}: data={callback.data}")
    
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = callback.data

    try:
        if data == "admin_broadcast":
            await callback.message.edit_text("Отправьте сообщение для рассылки")
            await state.set_state(BroadcastStates.waiting_for_message)
            await callback.answer("Начало рассылки")

        elif data == "admin_importdb":
            await callback.message.edit_text("Пришлите файл .db для импорта")
            await callback.answer("Ожидаю файл")

        elif data == "admin_addusernames":
            await callback.message.edit_text("Пришлите список @username (по строкам)")
            await callback.answer("Ожидаю usernames")

        elif data == "admin_adduser":
            await callback.message.edit_text("Введите: /adduser @username 123456789 или ID")
            await callback.answer("Ожидаю ввод")

        elif data == "admin_stats":
            # Прямо вызываем логику статистики
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
        logger.error(f"Ошибка при обработке callback {data}: {type(e).__name__} → {e}", exc_info=True)
        await callback.message.answer(f"Ошибка: {str(e)}")
        await callback.answer("Ошибка, смотрите логи")

    await callback.answer()  # обязательно завершить callback
