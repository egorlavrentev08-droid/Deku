# bot.py

import html
import logging
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    EphemeralMessageParameters
)

from config import (
    CHAT_ID, is_admin, SHIELD_PRICE, SHIELD_HOURS,
    REDEMPTION_TARGET
)
from db import (
    get_user, get_user_by_identifier, register_user, update_user_name,
    get_shield_count, is_shield_active,
    get_redemption_status, is_user_banned,
    ban_user, pardon_user, parse_iso_utc, now_utc
)
from core import (
    process_user_message, get_user_profile,
    buy_shield, activate_shield,
    get_top_streak_text, get_top_messages_text,
    admin_give_coins, admin_set_shield, admin_set_rank,
    execute_daily_top, execute_weekly_top
)

logger = logging.getLogger(__name__)


def with_publish_button(kb: Optional[InlineKeyboardMarkup] = None) -> InlineKeyboardMarkup:
    publish_row = [InlineKeyboardButton(text="📢 Отправить в чат", callback_data="publish_msg")]
    if kb and kb.inline_keyboard:
        return InlineKeyboardMarkup(inline_keyboard=[*kb.inline_keyboard, publish_row])
    return InlineKeyboardMarkup(inline_keyboard=[publish_row])


async def send_ephemeral(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    callback_query_id: Optional[str] = None,
    allow_publish: bool = False
):
    if allow_publish:
        reply_markup = with_publish_button(reply_markup)

    params = EphemeralMessageParameters(
        receiver_user_id=user_id,
        callback_query_id=callback_query_id
    )
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            ephemeral_message_parameters=params
        )
    except Exception as e:
        logger.warning(f"⚠️ Не удалось отправить ephemeral сообщение для {user_id}: {e}")
        return None


async def edit_ephemeral_safe(
    callback: types.CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
):
    bot = callback.bot
    msg = callback.message
    try:
        await bot.edit_ephemeral_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=text,
            reply_markup=reply_markup
        )
    except Exception:
        try:
            await msg.edit_text(text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {e}")


async def group_chat_filter(message: types.Message) -> bool:
    if not message.from_user or message.from_user.is_bot:
        return False

    if message.chat.id != CHAT_ID:
        return False

    if is_admin(message.from_user.id):
        return True

    if await is_user_banned(message.from_user.id):
        return False

    return True


def _build_shop_keyboard(shield_count: int, shield_active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🛡️ Купить щит ({SHIELD_PRICE} монет) [{shield_count} шт.]",
            callback_data="buy_shield"
        )],
        [InlineKeyboardButton(
            text=f"⚡ Активировать щит ({SHIELD_HOURS} ч.) {'✅' if shield_active else '❌'}",
            callback_data="activate_shield"
        )]
    ])


async def _get_shop_text_and_kb(user_id: int):
    user = await get_user(user_id)
    if not user:
        return None, None
    shield_count = await get_shield_count(user_id)
    shield_active = await is_shield_active(user_id)
    text = (
        f"🛒 <b>Магазин Деку</b>\n\n"
        f"💰 Монет: <b>{user['coins']:.1f}</b>\n"
        f"🛡️ Щитов в запасе: <b>{shield_count} шт.</b>\n"
        f"📊 Щит: {'🟢 Активен' if shield_active else '🔴 Не активен'}\n\n"
        f"💡 Щит защищает стрик от сброса на {SHIELD_HOURS} часов."
    )
    kb = _build_shop_keyboard(shield_count, shield_active)
    return text, kb


def register_handlers(dp: Dispatcher, bot: Bot):
    # Пользовательские команды (только в основном чате, отвечают индивидуально)
    dp.message.register(cmd_start, Command("start"), group_chat_filter)
    dp.message.register(cmd_help, Command("help"), group_chat_filter)
    dp.message.register(cmd_me, Command("me"), group_chat_filter)
    dp.message.register(cmd_me, Command("profile"), group_chat_filter)
    dp.message.register(cmd_name, Command("name"), group_chat_filter)
    dp.message.register(cmd_shop, Command("shop"), group_chat_filter)
    dp.message.register(cmd_top, Command("top"), group_chat_filter)
    dp.message.register(cmd_redemption, Command("redemption"), group_chat_filter)

    # Админ-команды в чате
    dp.message.register(cmd_admin_coins, Command("coins"), group_chat_filter)
    dp.message.register(cmd_admin_bypass, Command("bypass"), group_chat_filter)
    dp.message.register(cmd_admin_rank, Command("rank"), group_chat_filter)
    dp.message.register(cmd_admin_ban, Command("dban"), group_chat_filter)
    dp.message.register(cmd_admin_pardon, Command("dpardon"), group_chat_filter)
    dp.message.register(cmd_topfast, Command("topfast"), group_chat_filter)

    # Все остальные сообщения в чате
    dp.message.register(handle_all_messages, group_chat_filter)

    # Коллбэки в чате
    dp.callback_query.register(handle_publish_callback, F.data == "publish_msg")
    dp.callback_query.register(handle_shop_callback, F.data.in_(["buy_shield", "activate_shield"]))
    dp.callback_query.register(handle_top_callback, F.data.in_(["top_streak", "top_messages"]))
    dp.callback_query.register(handle_topfast_selection, F.data.in_(["tf_msgs", "tf_streak"]))
    dp.callback_query.register(handle_topfast_confirm, F.data.startswith("tf_confirm_"))
    dp.callback_query.register(handle_topfast_cancel, F.data == "tf_cancel")


async def cmd_start(message: types.Message):
    tg_name = message.from_user.first_name or ""
    if message.from_user.last_name:
        tg_name = f"{tg_name} {message.from_user.last_name}".strip()

    await register_user(message.from_user.id, message.from_user.username, tg_name)

    text = (
        "🥦 <b>Привет! Я Деку — Архитектор Дисциплины!</b>\n\n"
        "Ты зарегистрирован в системе. Все ответы приходят тебе лично прямо в чат.\n\n"
        "/me — твой профиль\n"
        "/shop — магазин щитов\n"
        "/top — топы чата\n"
        "/redemption — статус искупления\n"
        "/name Имя — сменить отображаемое имя"
    )
    await send_ephemeral(message.bot, message.chat.id, message.from_user.id, text)


async def cmd_help(message: types.Message):
    await cmd_start(message)


async def cmd_me(message: types.Message):
    profile = await get_user_profile(message.from_user.id)
    if profile:
        await send_ephemeral(
            message.bot,
            message.chat.id,
            message.from_user.id,
            profile,
            allow_publish=True
        )
    else:
        await send_ephemeral(
            message.bot,
            message.chat.id,
            message.from_user.id,
            "❌ Ты ещё не зарегистрирован. Напиши /start или любое сообщение в чат."
        )


async def cmd_name(message: types.Message):
    if not message.text:
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй: <code>/name ТвоёИмя</code>"
        )

    new_name = args[1].strip()
    if len(new_name) > 50:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Имя слишком длинное (макс. 50 символов)."
        )

    await register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name
    )
    success = await update_user_name(message.from_user.id, new_name)
    if success:
        await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            f"✅ Теперь тебя зовут <b>{html.escape(new_name)}</b>!",
            allow_publish=True
        )
    else:
        await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Это имя уже занято. Выбери другое."
        )


async def cmd_shop(message: types.Message):
    text, kb = await _get_shop_text_and_kb(message.from_user.id)
    if not text:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Ты не зарегистрирован. Напиши /start."
        )

    await send_ephemeral(message.bot, message.chat.id, message.from_user.id, text, reply_markup=kb)


async def handle_shop_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    if callback.data == "buy_shield":
        success, msg = await buy_shield(user_id)
        await callback.answer(msg, show_alert=True)
        if success:
            text, kb = await _get_shop_text_and_kb(user_id)
            if text:
                await edit_ephemeral_safe(callback, text, reply_markup=kb)

    elif callback.data == "activate_shield":
        success, msg = await activate_shield(user_id)
        await callback.answer(msg, show_alert=True)
        if success:
            text, kb = await _get_shop_text_and_kb(user_id)
            if text:
                await edit_ephemeral_safe(callback, text, reply_markup=kb)


async def cmd_top(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏆 Топ по стрикам", callback_data="top_streak"),
            InlineKeyboardButton(text="💬 Топ по сообщениям", callback_data="top_messages")
        ]
    ])
    await send_ephemeral(
        message.bot,
        message.chat.id,
        message.from_user.id,
        "📊 <b>Выбери топ:</b>",
        reply_markup=kb
    )


async def handle_top_callback(callback: types.CallbackQuery):
    await callback.answer()
    if callback.data == "top_streak":
        text = await get_top_streak_text()
    elif callback.data == "top_messages":
        text = await get_top_messages_text()
    else:
        return

    back_kb = with_publish_button(InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏆 Стрики", callback_data="top_streak"),
            InlineKeyboardButton(text="💬 Сообщения", callback_data="top_messages")
        ]
    ]))
    await edit_ephemeral_safe(callback, text, reply_markup=back_kb)


async def cmd_redemption(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Ты не зарегистрирован. Напиши /start."
        )

    redemption = await get_redemption_status(message.from_user.id)
    if not redemption or not redemption.get('active', False):
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ У тебя нет активного искупления.\nОно появляется, если ты потерял стрик."
        )

    progress = redemption.get('progress', 0)
    target = redemption.get('target', REDEMPTION_TARGET)
    remaining = max(0, target - progress)

    expires = parse_iso_utc(redemption.get('expires_at'))
    hours_left = max(0, (expires - now_utc()).total_seconds() / 3600) if expires else 24

    text = (
        f"🔄 <b>Восстановление стрика</b>\n\n"
        f"• Цель: <b>{target}</b> сообщений\n"
        f"• Прогресс: <b>{progress}</b> сообщений\n"
        f"• Осталось: <b>{remaining}</b> сообщений\n"
        f"• Восстановится стрик: <b>{redemption['streak_to_restore']} дн.</b>\n"
        f"⏳ Осталось времени: ~<b>{int(hours_left)} ч.</b>\n\n"
        f"{'🔥 ДАВАЙ, ТЫ СМОЖЕШЬ!' if progress > 100 else '💪 ПИШИ БОЛЬШЕ!'}"
    )
    await send_ephemeral(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        allow_publish=True
    )


async def handle_publish_callback(callback: types.CallbackQuery):
    msg = callback.message
    if not msg:
        return await callback.answer("❌ Сообщение недоступно.", show_alert=True)

    content = msg.html_text or msg.text or ""
    if not content:
        return await callback.answer("❌ Нечего отправлять.", show_alert=True)

    user = await get_user(callback.from_user.id)
    if user and user.get('name'):
        sender_name = html.escape(user['name'])
    else:
        sender_name = html.escape(callback.from_user.full_name or callback.from_user.username or "Участник")

    # Убираем кнопку "Отправить в чат", оставляя остальные (если есть)
    new_rows = []
    if msg.reply_markup and msg.reply_markup.inline_keyboard:
        for row in msg.reply_markup.inline_keyboard:
            filtered = [btn for btn in row if btn.callback_data != "publish_msg"]
            if filtered:
                new_rows.append(filtered)
    new_markup = InlineKeyboardMarkup(inline_keyboard=new_rows) if new_rows else None

    try:
        await callback.bot.edit_ephemeral_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=new_markup
        )
    except Exception:
        try:
            await msg.edit_reply_markup(reply_markup=new_markup)
        except Exception:
            pass

    await callback.bot.send_message(
        chat_id=CHAT_ID,
        text=f"👤 <b>{sender_name}</b> поделился(ась) в чате:\n\n{content}"
    )
    await callback.answer("✅ Отправлено в общий чат!")


async def handle_all_messages(message: types.Message):
    if message.text and message.text.startswith('/'):
        return

    user_id = message.from_user.id
    tg_name = message.from_user.first_name or ""
    if message.from_user.last_name:
        tg_name = f"{tg_name} {message.from_user.last_name}".strip()

    result = await process_user_message(
        user_id=user_id,
        chat_id=message.chat.id,
        username=message.from_user.username,
        tg_name=tg_name
    )

    texts = result.get('ephemeral_texts') or []
    if not texts:
        return

    combined_text = "\n\n".join(texts)
    await send_ephemeral(
        bot=message.bot,
        chat_id=message.chat.id,
        user_id=user_id,
        text=combined_text,
        allow_publish=result.get('allow_publish', False)
    )


# --- Админ-команды в чате (отвечают индивидуально админу) ---

async def cmd_admin_coins(message: types.Message):
    if not is_admin(message.from_user.id) or not message.text:
        return

    parts = message.text.split()
    if len(parts) < 3:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй:\n<code>/coins give @username 10</code>\n<code>/coins take @username 10</code>\n"
            "Или ответом на сообщение: <code>/coins give 10</code>"
        )

    action = parts[1].lower()
    if action not in ('give', 'take'):
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Используй: <code>/coins give @username N</code> или <code>/coins take @username N</code>"
        )

    if len(parts) >= 4:
        identifier = parts[2].lstrip('@')
        try:
            amount = float(parts[3])
        except ValueError:
            return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Сумма должна быть числом.")
    elif message.reply_to_message and message.reply_to_message.from_user:
        identifier = str(message.reply_to_message.from_user.id)
        try:
            amount = float(parts[2])
        except ValueError:
            return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Сумма должна быть числом.")
    else:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Укажи пользователя (@username или ID) или ответь на сообщение."
        )

    if amount <= 0:
        return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Сумма должна быть больше 0.")

    if action == 'take':
        amount = -amount

    _, msg = await admin_give_coins(message.from_user.id, identifier, amount)
    await send_ephemeral(message.bot, message.chat.id, message.from_user.id, msg)


async def cmd_admin_bypass(message: types.Message):
    if not is_admin(message.from_user.id) or not message.text:
        return

    parts = message.text.split()
    if len(parts) < 2:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй: <code>/bypass @username часы</code> или ответом: <code>/bypass часы</code>"
        )

    if len(parts) >= 3:
        identifier = parts[1].lstrip('@')
        try:
            hours = int(parts[2])
        except ValueError:
            return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Часы должны быть целым числом (0–240).")
    elif message.reply_to_message and message.reply_to_message.from_user:
        identifier = str(message.reply_to_message.from_user.id)
        try:
            hours = int(parts[1])
        except ValueError:
            return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Часы должны быть целым числом (0–240).")
    else:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "❌ Укажи пользователя (@username или ID) или ответь на сообщение."
        )

    if hours < 0 or hours > 240:
        return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Часы должны быть от 0 до 240.")

    _, msg = await admin_set_shield(message.from_user.id, identifier, hours)
    await send_ephemeral(message.bot, message.chat.id, message.from_user.id, msg)


async def cmd_admin_rank(message: types.Message):
    if not is_admin(message.from_user.id) or not message.text:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй:\n<code>/rank @username Без ранга</code>\n\n"
            "Доступные ранги:\n"
            "Без ранга, Новичок, Кандидат, Знакомый, Хороший, Душа, Старожил, Гордость, Авторитет, Незаменимый, или <code>сброс</code>"
        )

    identifier = parts[1].lstrip('@')
    rank_name = parts[2].strip()

    _, msg = await admin_set_rank(message.from_user.id, identifier, rank_name)
    await send_ephemeral(message.bot, message.chat.id, message.from_user.id, msg)


async def cmd_admin_ban(message: types.Message):
    if not is_admin(message.from_user.id) or not message.text:
        return

    user_id = None
    args = message.text.split(maxsplit=1)

    if len(args) >= 2:
        identifier = args[1].strip().lstrip('@')
        user = await get_user_by_identifier(identifier)
        if user:
            user_id = user['user_id']
        else:
            return await send_ephemeral(
                message.bot, message.chat.id, message.from_user.id,
                f"❌ Пользователь '{html.escape(identifier)}' не найден."
            )
    elif message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    else:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй: <code>/dban @username</code> или ответом на сообщение."
        )

    if is_admin(user_id):
        return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Нельзя забанить админа!")

    if await is_user_banned(user_id):
        return await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Пользователь уже забанен.")

    if await ban_user(user_id):
        user = await get_user(user_id)
        name = html.escape((user.get('name') or user.get('tg_name') or user.get('telegram_username') or str(user_id)) if user else str(user_id))
        await send_ephemeral(message.bot, message.chat.id, message.from_user.id, f"✅ <b>{name}</b> забанен!")


async def cmd_admin_pardon(message: types.Message):
    if not is_admin(message.from_user.id) or not message.text:
        return

    user_id = None
    args = message.text.split(maxsplit=1)

    if len(args) >= 2:
        identifier = args[1].strip().lstrip('@')
        user = await get_user_by_identifier(identifier)
        if user:
            user_id = user['user_id']
        else:
            return await send_ephemeral(
                message.bot, message.chat.id, message.from_user.id,
                f"❌ Пользователь '{html.escape(identifier)}' не найден."
            )
    elif message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    else:
        return await send_ephemeral(
            message.bot, message.chat.id, message.from_user.id,
            "📝 Используй: <code>/dpardon @username</code> или ответом на сообщение."
        )

    if await pardon_user(user_id):
        user = await get_user(user_id)
        name = html.escape((user.get('name') or user.get('tg_name') or user.get('telegram_username') or str(user_id)) if user else str(user_id))
        await send_ephemeral(message.bot, message.chat.id, message.from_user.id, f"✅ <b>{name}</b> разбанен!")
    else:
        await send_ephemeral(message.bot, message.chat.id, message.from_user.id, "❌ Пользователь не был забанен.")


async def cmd_topfast(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Топ сообщений", callback_data="tf_msgs")],
        [InlineKeyboardButton(text="🏆 Топ стриков", callback_data="tf_streak")]
    ])
    await send_ephemeral(
        message.bot, message.chat.id, message.from_user.id,
        "⚡ <b>Принудительное подведение итогов</b>\n\nВыбери топ:",
        reply_markup=kb
    )


async def handle_topfast_selection(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("❌ Нет прав.", show_alert=True)

    await callback.answer()

    if callback.data == "tf_msgs":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="tf_confirm_msgs")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tf_cancel")]
        ])
        await edit_ephemeral_safe(
            callback,
            "Выбран <b>Топ сообщений</b>.\nЭто подведёт итоги, выдаст награды и обнулит счётчики за день. Уверен?",
            reply_markup=kb
        )
    elif callback.data == "tf_streak":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="tf_confirm_streak")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tf_cancel")]
        ])
        await edit_ephemeral_safe(
            callback,
            "Выбран <b>Топ стриков</b>.\nЭто подведёт итоги и выдаст награды. Уверен?",
            reply_markup=kb
        )


async def handle_topfast_cancel(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer()

    await callback.answer()
    await edit_ephemeral_safe(callback, "❌ Действие отменено.")


async def handle_topfast_confirm(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer()

    await callback.answer()
    await edit_ephemeral_safe(callback, "⏳ Обработка...")

    if callback.data == "tf_confirm_msgs":
        await execute_daily_top(bot, is_forced=True)
        await edit_ephemeral_safe(callback, "✅ Топ сообщений подведён, награды выданы, счётчики сброшены!")
    elif callback.data == "tf_confirm_streak":
        await execute_weekly_top(bot, is_forced=True)
        await edit_ephemeral_safe(callback, "✅ Топ стриков подведён, награды выданы!")
