# admin_panel.py

import html
import logging
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from config import (
    OWNER_ID, CHAT_ID, RANKS,
    TRIGGER_NAMES, TRIGGER_EMOJI, MOOD_NAMES, MOOD_EMOJI
)
from db import (
    get_all_stats, get_users_list, get_user, get_user_by_identifier,
    add_coins, set_shield_until, update_streak,
    ban_user, pardon_user, is_shield_active, get_shield_count,
    get_redemption_status, start_redemption, fail_redemption,
    get_setting, set_setting, check_expired_redemptions
)
from core import (
    admin_set_rank, get_phrases_stats, get_phrases_by_trigger,
    add_phrase_direct, add_phrase_from_text, delete_phrase_by_index,
    get_top_streak_text, get_top_messages_text,
    execute_daily_top, execute_weekly_top,
    check_inactive_users, handle_inactive_user
)
from bot import send_ephemeral

logger = logging.getLogger(__name__)

router = Router(name="owner_panel")
router.message.filter(F.chat.type == "private", F.from_user.id == OWNER_ID)
router.callback_query.filter(F.message.chat.type == "private", F.from_user.id == OWNER_ID)


class PanelStates(StatesGroup):
    waiting_user_search = State()
    waiting_coins_amount = State()
    waiting_shield_hours = State()
    waiting_streak_value = State()
    waiting_phrase_text = State()
    waiting_phrase_quick = State()
    waiting_broadcast_text = State()
    waiting_reply_chance = State()


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Пользователи")],
            [KeyboardButton(text="💬 Фразы"), KeyboardButton(text="🏆 Топы и награды")],
            [KeyboardButton(text="📢 Написать в чат"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Панель управления Деку..."
    )


def main_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="op:stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="op:users:all:0"),
        ],
        [
            InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="op:user_search"),
            InlineKeyboardButton(text="💬 Фразы", callback_data="op:phrases"),
        ],
        [
            InlineKeyboardButton(text="🏆 Топы и итоги", callback_data="op:tops"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="op:settings"),
        ],
        [
            InlineKeyboardButton(text="📢 Отправить в чат", callback_data="op:broadcast"),
        ]
    ])


# --- Точки входа ---

@router.message(Command("start", "panel", "menu"))
async def cmd_owner_panel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🥦 <b>Панель управления Деку</b>\n\n"
        "Добро пожаловать, создатель! Выбери раздел ниже или используй клавиатуру меню.",
        reply_markup=main_reply_keyboard()
    )
    await message.answer("⚡ <b>Быстрые действия:</b>", reply_markup=main_inline_menu())


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def msg_stats(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = await _build_stats_view()
    await message.answer(text, reply_markup=kb)


@router.message(Command("users"))
@router.message(F.text == "👥 Пользователи")
async def msg_users(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = await _build_users_view("all", 0)
    await message.answer(text, reply_markup=kb)


@router.message(Command("phrases"))
@router.message(F.text == "💬 Фразы")
async def msg_phrases(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _build_phrases_menu()
    await message.answer(text, reply_markup=kb)


@router.message(Command("topfast"))
@router.message(F.text == "🏆 Топы и награды")
async def msg_tops(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _build_tops_menu()
    await message.answer(text, reply_markup=kb)


@router.message(Command("broadcast"))
@router.message(F.text == "📢 Написать в чат")
async def msg_broadcast(message: types.Message, state: FSMContext):
    await state.set_state(PanelStates.waiting_broadcast_text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_state")]
    ])
    await message.answer(
        "📢 <b>Отправка сообщения в основной чат</b>\n\n"
        "Пришли текст, который Деку отправит в чат от своего имени:",
        reply_markup=kb
    )


@router.message(F.text == "⚙️ Настройки")
async def msg_settings(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = await _build_settings_view()
    await message.answer(text, reply_markup=kb)


# --- Построители экранов ---

async def _build_stats_view():
    s = await get_all_stats()
    p_stats = get_phrases_stats()
    total_phrases = sum(p_stats.values())

    text = (
        f"📊 <b>Статистика Деку</b>\n\n"
        f"• Всего пользователей: <b>{s['total_users']}</b>\n"
        f"• Активных сегодня: <b>{s['active_today']}</b>\n"
        f"• Сообщений сегодня: <b>{s['total_messages_today']}</b>\n"
        f"• В искуплении: <b>{s['redeeming_users']}</b>\n"
        f"• Забанено: <b>{s['banned_users']}</b>\n"
        f"• Макс. стрик: <b>{s['max_streak']} дн.</b>\n"
        f"• Всего монет в обороте: <b>{s['total_coins']:.1f}</b>\n"
        f"• Фраз в базе: <b>{total_phrases}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="op:stats")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="op:home")]
    ])
    return text, kb


async def _build_users_view(filter_type: str, page: int):
    limit = 8
    offset = page * limit
    users, total = await get_users_list(offset=offset, limit=limit, filter_type=filter_type)

    filter_titles = {
        "all": "Все",
        "active": "Активные сегодня",
        "redeeming": "В искуплении",
        "banned": "Забаненные",
    }

    text = f"👥 <b>Пользователи ({filter_titles.get(filter_type, 'Все')})</b> — всего {total}\n"
    if not users:
        text += "\nСписок пуст."

    rows = [
        [
            InlineKeyboardButton(text="Все", callback_data="op:users:all:0"),
            InlineKeyboardButton(text="Актив", callback_data="op:users:active:0"),
            InlineKeyboardButton(text="Искупл.", callback_data="op:users:redeeming:0"),
            InlineKeyboardButton(text="Бан", callback_data="op:users:banned:0"),
        ]
    ]

    for u in users:
        ban_mark = "🚫 " if u['is_banned'] else ""
        label = f"{ban_mark}{u['display_name']} | 🔥{u['streak']} | 💰{u['coins']:.0f}"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"op:u:{u['user_id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"op:users:{filter_type}:{page - 1}"))
    if offset + limit < total:
        nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"op:users:{filter_type}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="op:user_search"),
        InlineKeyboardButton(text="◀️ В меню", callback_data="op:home"),
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_user_card(user_id: int):
    user = await get_user(user_id)
    if not user:
        return "❌ Пользователь не найден.", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К списку", callback_data="op:users:all:0")]
        ])

    shield_count = await get_shield_count(user_id)
    shield_active = await is_shield_active(user_id)
    redemption = await get_redemption_status(user_id)

    name = html.escape(user.get('name') or user.get('tg_name') or 'Без имени')
    uname = f"@{html.escape(user['telegram_username'])}" if user.get('telegram_username') else "—"
    red_str = (
        f"{redemption['progress']}/{redemption['target']} (вернуть {redemption['streak_to_restore']} дн.)"
        if redemption and redemption.get('active') else "Нет"
    )

    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"• ID: <code>{user_id}</code>\n"
        f"• Имя: <b>{name}</b> ({uname})\n"
        f"• Ранг: <b>{html.escape(user.get('rank') or 'Без ранга')}</b>"
        f"{' 🔒' if user.get('rank_locked') else ''}\n"
        f"• Стрик: <b>{user['streak']} дн.</b> (рекорд: {user['streak_record']})\n"
        f"• Сообщений сегодня: <b>{user.get('messages_today', 0)}</b>\n"
        f"• Монеты: <b>{user['coins']:.1f}</b>\n"
        f"• Щиты: <b>{shield_count} шт.</b> ({'🟢 Активен' if shield_active else '🔴 Нет'})\n"
        f"• Искупление: {red_str}\n"
        f"• Статус: {'🚫 Забанен' if user.get('is_banned') else '✅ Активен'}"
    )

    ban_btn = (
        InlineKeyboardButton(text="🟢 Разбанить", callback_data=f"op:u_pardon:{user_id}")
        if user.get('is_banned')
        else InlineKeyboardButton(text="🚫 Забанить", callback_data=f"op:u_ban:{user_id}")
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 ±Монеты", callback_data=f"op:u_coins:{user_id}"),
            InlineKeyboardButton(text="🛡️ Щит (часы)", callback_data=f"op:u_shield:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔥 Задать стрик", callback_data=f"op:u_streak:{user_id}"),
            InlineKeyboardButton(text="🏆 Выбрать ранг", callback_data=f"op:u_rank_menu:{user_id}"),
        ],
        [
            ban_btn,
            InlineKeyboardButton(text="🔄 Сброс искупл.", callback_data=f"op:u_fail_red:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"op:u:{user_id}"),
            InlineKeyboardButton(text="◀️ К списку", callback_data="op:users:all:0"),
        ]
    ])
    return text, kb


def _build_phrases_menu():
    stats = get_phrases_stats()
    total = sum(stats.values())
    text = f"💬 <b>Управление фразами</b> (всего: {total})\n\nВыбери триггер для просмотра или добавь фразу:"

    rows = []
    for trig, name in TRIGGER_NAMES.items():
        emoji = TRIGGER_EMOJI.get(trig, "•")
        cnt = stats.get(trig, 0)
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {name.capitalize()} ({cnt})",
            callback_data=f"op:ph_list:{trig}:0"
        )])

    rows.append([InlineKeyboardButton(text="➕ Быстро добавить (!₽ текст)", callback_data="op:ph_quick")])
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="op:home")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _build_trigger_phrases_view(trigger: str, page: int):
    phrases = get_phrases_by_trigger(trigger)
    limit = 6
    offset = page * limit
    slice_p = phrases[offset:offset + limit]

    emoji = TRIGGER_EMOJI.get(trigger, "•")
    t_name = TRIGGER_NAMES.get(trigger, trigger)
    text = f"{emoji} <b>Фразы: {t_name}</b> ({len(phrases)})\n\n"

    rows = []
    for idx, p in enumerate(slice_p, start=offset):
        m_emoji = MOOD_EMOJI.get(p.get('mood', 'SARCASTIC'), '😏')
        preview = p['text'][:42] + ("..." if len(p['text']) > 42 else "")
        text += f"<b>{idx + 1}.</b> {m_emoji} {html.escape(p['text'])}\n"
        rows.append([InlineKeyboardButton(
            text=f"🗑️ Удалить #{idx + 1}: {preview[:25]}",
            callback_data=f"op:ph_del:{trigger}:{idx}:{page}"
        )])

    if not slice_p:
        text += "Фраз пока нет."

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"op:ph_list:{trigger}:{page - 1}"))
    if offset + limit < len(phrases):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"op:ph_list:{trigger}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="➕ Добавить в этот триггер", callback_data=f"op:ph_add_mood:{trigger}")])
    rows.append([InlineKeyboardButton(text="◀️ К триггерам", callback_data="op:phrases")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _build_tops_menu():
    text = "🏆 <b>Топы и принудительные действия</b>\n\nМожно просмотреть текущие рейтинги или досрочно подвести итоги:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏆 Текущий топ стриков", callback_data="op:view_top_streak"),
            InlineKeyboardButton(text="💬 Текущий топ сообщений", callback_data="op:view_top_msgs"),
        ],
        [
            InlineKeyboardButton(text="⚡ Подвести Топ дня", callback_data="op:force_daily"),
            InlineKeyboardButton(text="⚡ Подвести Топ недели", callback_data="op:force_weekly"),
        ],
        [
            InlineKeyboardButton(text="⏳ Проверить неактивных", callback_data="op:run_inactive"),
            InlineKeyboardButton(text="🔄 Проверить искупления", callback_data="op:run_redemptions"),
        ],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="op:home")]
    ])
    return text, kb


async def _build_settings_view():
    auto_reply = await get_setting('auto_reply_enabled', '1') == '1'
    inactive_pings = await get_setting('inactive_pings_enabled', '1') == '1'
    chance = await get_setting('random_reply_chance', '0.2')

    text = (
        f"⚙️ <b>Настройки Деку</b>\n\n"
        f"• Случайные ответы в чате: {'🟢 Вкл' if auto_reply else '🔴 Выкл'}\n"
        f"• Шанс случайного ответа: <b>{float(chance) * 100:.0f}%</b>\n"
        f"• Автопинги неактивных: {'🟢 Вкл' if inactive_pings else '🔴 Выкл'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔴 Выкл' if auto_reply else '🟢 Вкл'} случайные ответы",
            callback_data="op:toggle_auto_reply"
        )],
        [InlineKeyboardButton(
            text=f"{'🔴 Выкл' if inactive_pings else '🟢 Вкл'} пинги неактивных",
            callback_data="op:toggle_inactive_pings"
        )],
        [InlineKeyboardButton(text="🎲 Изменить шанс ответа", callback_data="op:set_chance")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="op:home")]
    ])
    return text, kb


# --- Callback обработчики панели ---

@router.callback_query(F.data.startswith("op:"))
async def handle_panel_callbacks(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split(":")
    action = parts[1]

    if action == "home":
        await state.clear()
        await callback.message.edit_text("⚡ <b>Главное меню управления Деку:</b>", reply_markup=main_inline_menu())
        return await callback.answer()

    if action == "cancel_state":
        await state.clear()
        await callback.message.edit_text("❌ Действие отменено.", reply_markup=main_inline_menu())
        return await callback.answer()

    if action == "stats":
        text, kb = await _build_stats_view()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "users":
        filter_type = parts[2]
        page = int(parts[3])
        text, kb = await _build_users_view(filter_type, page)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "user_search":
        await state.set_state(PanelStates.waiting_user_search)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_state")]])
        await callback.message.edit_text(
            "🔍 Введите <code>@username</code>, <code>user_id</code> или имя пользователя:",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "u":
        uid = int(parts[2])
        text, kb = await _build_user_card(uid)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "u_ban":
        uid = int(parts[2])
        await ban_user(uid)
        text, kb = await _build_user_card(uid)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("🚫 Пользователь забанен")

    if action == "u_pardon":
        uid = int(parts[2])
        await pardon_user(uid)
        text, kb = await _build_user_card(uid)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("🟢 Пользователь разбанен")

    if action == "u_fail_red":
        uid = int(parts[2])
        await fail_redemption(uid)
        text, kb = await _build_user_card(uid)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("🔄 Искупление сброшено")

    if action == "u_coins":
        uid = int(parts[2])
        await state.set_state(PanelStates.waiting_coins_amount)
        await state.update_data(target_uid=uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"op:u:{uid}")]])
        await callback.message.edit_text(
            f"💰 Введите количество монет для начисления/списания (например <code>100</code> или <code>-50</code>):",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "u_shield":
        uid = int(parts[2])
        await state.set_state(PanelStates.waiting_shield_hours)
        await state.update_data(target_uid=uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"op:u:{uid}")]])
        await callback.message.edit_text(
            "🛡️ Введите длительность щита в часах (<code>0</code> чтобы снять, макс. <code>240</code>):",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "u_streak":
        uid = int(parts[2])
        await state.set_state(PanelStates.waiting_streak_value)
        await state.update_data(target_uid=uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"op:u:{uid}")]])
        await callback.message.edit_text(
            "🔥 Введите новое значение стрика (целое число >= 0):",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "u_rank_menu":
        uid = int(parts[2])
        rows = [
            [InlineKeyboardButton(text=r['name'], callback_data=f"op:u_set_rank:{uid}:{r['name']}")]
            for r in RANKS
        ]
        rows.append([InlineKeyboardButton(text="🔓 Сбросить фиксацию", callback_data=f"op:u_set_rank:{uid}:сброс")])
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"op:u:{uid}")])
        await callback.message.edit_text("🏆 Выберите ранг:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return await callback.answer()

    if action == "u_set_rank":
        uid = int(parts[2])
        rank_name = parts[3]
        ok, msg = await admin_set_rank(OWNER_ID, str(uid), rank_name)
        await callback.answer(msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""), show_alert=True)
        text, kb = await _build_user_card(uid)
        await callback.message.edit_text(text, reply_markup=kb)
        return

    # --- Фразы ---
    if action == "phrases":
        text, kb = _build_phrases_menu()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "ph_list":
        trigger = parts[2]
        page = int(parts[3])
        text, kb = _build_trigger_phrases_view(trigger, page)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "ph_del":
        trigger = parts[2]
        idx = int(parts[3])
        page = int(parts[4])
        delete_phrase_by_index(trigger, idx)
        text, kb = _build_trigger_phrases_view(trigger, page)
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("🗑️ Фраза удалена")

    if action == "ph_add_mood":
        trigger = parts[2]
        rows = [
            [InlineKeyboardButton(
                text=f"{MOOD_EMOJI[m]} {MOOD_NAMES[m].capitalize()}",
                callback_data=f"op:ph_add_text:{trigger}:{m}"
            )]
            for m in MOOD_NAMES
        ]
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"op:ph_list:{trigger}:0")])
        await callback.message.edit_text("Выберите настроение для новой фразы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return await callback.answer()

    if action == "ph_add_text":
        trigger = parts[2]
        mood = parts[3]
        await state.set_state(PanelStates.waiting_phrase_text)
        await state.update_data(trigger=trigger, mood=mood)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"op:ph_list:{trigger}:0")]])
        await callback.message.edit_text(
            f"✍️ Введите текст фразы для <b>{TRIGGER_NAMES.get(trigger, trigger)}</b> ({MOOD_NAMES.get(mood, mood)}):",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "ph_quick":
        await state.set_state(PanelStates.waiting_phrase_quick)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="op:phrases")]])
        await callback.message.edit_text(
            "✍️ Введите фразу в формате <code>!₽ Текст</code>\n\n"
            "Триггеры: <code>! ? ¡ ¿ ~ ≈ ∆ %</code>\n"
            "Настроения: <code>₽ £ $</code>",
            reply_markup=kb
        )
        return await callback.answer()

    # --- Топы и системные задачи ---
    if action == "tops":
        text, kb = _build_tops_menu()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "view_top_streak":
        text = await get_top_streak_text()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="op:tops")]])
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "view_top_msgs":
        text = await get_top_messages_text()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="op:tops")]])
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "force_daily":
        await execute_daily_top(bot, is_forced=True)
        await callback.answer("✅ Топ дня подведён, счётчики сброшены!", show_alert=True)
        return

    if action == "force_weekly":
        await execute_weekly_top(bot, is_forced=True)
        await callback.answer("✅ Топ недели подведён!", show_alert=True)
        return

    if action == "run_inactive":
        inactive = await check_inactive_users()
        sent_cnt = 0
        for item in inactive:
            res = await handle_inactive_user(item['user']['user_id'], item['trigger'], item['mood'])
            if res:
                if res.get('ephemeral_text'):
                    await send_ephemeral(bot, CHAT_ID, res['user_id'], res['ephemeral_text'])
                if res.get('chat_text'):
                    await bot.send_message(CHAT_ID, res['chat_text'])
                sent_cnt += 1
        await callback.answer(f"✅ Проверено неактивных: {sent_cnt}", show_alert=True)
        return

    if action == "run_redemptions":
        expired = await check_expired_redemptions()
        await callback.answer(f"✅ Завершено просроченных искуплений: {len(expired)}", show_alert=True)
        return

    # --- Настройки ---
    if action == "settings":
        text, kb = await _build_settings_view()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer()

    if action == "toggle_auto_reply":
        cur = await get_setting('auto_reply_enabled', '1')
        await set_setting('auto_reply_enabled', '0' if cur == '1' else '1')
        text, kb = await _build_settings_view()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("✅ Сохранено")

    if action == "toggle_inactive_pings":
        cur = await get_setting('inactive_pings_enabled', '1')
        await set_setting('inactive_pings_enabled', '0' if cur == '1' else '1')
        text, kb = await _build_settings_view()
        await callback.message.edit_text(text, reply_markup=kb)
        return await callback.answer("✅ Сохранено")

    if action == "set_chance":
        await state.set_state(PanelStates.waiting_reply_chance)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="op:settings")]])
        await callback.message.edit_text(
            "🎲 Введите вероятность случайного ответа в процентах (от <code>0</code> до <code>100</code>):",
            reply_markup=kb
        )
        return await callback.answer()

    if action == "broadcast":
        await state.set_state(PanelStates.waiting_broadcast_text)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_state")]])
        await callback.message.edit_text(
            "📢 Пришлите текст, который Деку отправит в основной чат:",
            reply_markup=kb
        )
        return await callback.answer()


# --- FSM-обработчики ввода ---

@router.message(PanelStates.waiting_user_search)
async def fsm_user_search(message: types.Message, state: FSMContext):
    query = (message.text or "").strip().lstrip('@')
    if not query:
        return await message.answer("❌ Введите запрос.")

    user = await get_user_by_identifier(query)
    await state.clear()
    if not user:
        return await message.answer("❌ Пользователь не найден.", reply_markup=main_inline_menu())

    text, kb = await _build_user_card(user['user_id'])
    await message.answer(text, reply_markup=kb)


@router.message(PanelStates.waiting_coins_amount)
async def fsm_coins_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get('target_uid')
    try:
        amount = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        return await message.answer("❌ Введите корректное число.")

    await add_coins(uid, amount)
    await state.clear()
    text, kb = await _build_user_card(uid)
    await message.answer(f"✅ Баланс изменён на {amount:+.1f} монет.\n\n{text}", reply_markup=kb)


@router.message(PanelStates.waiting_shield_hours)
async def fsm_shield_hours(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get('target_uid')
    try:
        hours = int((message.text or "").strip())
    except ValueError:
        return await message.answer("❌ Введите целое число.")

    if hours < 0 or hours > 240:
        return await message.answer("❌ Укажите от 0 до 240 часов.")

    await set_shield_until(uid, hours)
    await state.clear()
    text, kb = await _build_user_card(uid)
    await message.answer(f"✅ Щит обновлён ({hours} ч.).\n\n{text}", reply_markup=kb)


@router.message(PanelStates.waiting_streak_value)
async def fsm_streak_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get('target_uid')
    try:
        streak = int((message.text or "").strip())
    except ValueError:
        return await message.answer("❌ Введите целое число.")

    if streak < 0:
        return await message.answer("❌ Стрик не может быть отрицательным.")

    await update_streak(uid, streak)
    await state.clear()
    text, kb = await _build_user_card(uid)
    await message.answer(f"✅ Стрик установлен: {streak} дн.\n\n{text}", reply_markup=kb)


@router.message(PanelStates.waiting_phrase_text)
async def fsm_phrase_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    trigger = data['trigger']
    mood = data['mood']
    ok, msg = add_phrase_direct(trigger, mood, message.text or "")
    await state.clear()
    text, kb = _build_trigger_phrases_view(trigger, 0)
    await message.answer(f"{msg}\n\n{text}", reply_markup=kb)


@router.message(PanelStates.waiting_phrase_quick)
async def fsm_phrase_quick(message: types.Message, state: FSMContext):
    ok, msg, _ = add_phrase_from_text(message.text or "")
    await state.clear()
    text, kb = _build_phrases_menu()
    await message.answer(f"{msg}\n\n{text}", reply_markup=kb)


@router.message(PanelStates.waiting_broadcast_text)
async def fsm_broadcast_text(message: types.Message, state: FSMContext, bot: Bot):
    text = (message.text or "").strip()
    if not text:
        return await message.answer("❌ Пришлите текстовое сообщение.")

    await bot.send_message(CHAT_ID, html.escape(text))
    await state.clear()
    await message.answer("✅ Сообщение отправлено в основной чат!", reply_markup=main_inline_menu())


@router.message(PanelStates.waiting_reply_chance)
async def fsm_reply_chance(message: types.Message, state: FSMContext):
    try:
        pct = float((message.text or "").replace("%", "").strip())
    except ValueError:
        return await message.answer("❌ Введите число от 0 до 100.")

    if pct < 0 or pct > 100:
        return await message.answer("❌ Введите число от 0 до 100.")

    await set_setting('random_reply_chance', str(pct / 100.0))
    await state.clear()
    text, kb = await _build_settings_view()
    await message.answer(f"✅ Вероятность обновлена.\n\n{text}", reply_markup=kb)
