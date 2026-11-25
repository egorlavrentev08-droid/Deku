# core.py

import html
import logging
import random
import json
import os
import aiosqlite
from typing import Optional, Dict, List, Tuple

from config import (
    RANKS, ACHIEVEMENT_DAYS, DAILY_STREAK_MESSAGES,
    REDEMPTION_TARGET, REDEMPTION_HOURS, SHIELD_PRICE, SHIELD_HOURS,
    TRIGGER_SYMBOLS, MOOD_SYMBOLS, PHRASES_FILE,
    get_rank_by_streak, DB_PATH, CHAT_ID, is_admin
)
from db import (
    now_utc, parse_iso_utc,
    get_user, get_user_by_identifier, register_user, update_last_activity, add_coins,
    increment_streak_and_mark_today, update_streak,
    increment_messages_today,
    get_shield_count, is_shield_active, buy_shield_atomic, activate_shield_atomic,
    set_shield_until, deactivate_shield,
    start_redemption, update_redemption_progress, get_redemption_status,
    complete_redemption, fail_redemption,
    get_top_streak, get_top_messages_today,
    is_user_banned, ban_user,
    get_last_ping, update_last_ping,
    get_redemption_failed_notified, set_redemption_failed_notified, reset_redemption_failed_notified,
    get_inactive_users, award_daily_top, award_weekly_top, reset_daily_messages,
    get_setting
)

logger = logging.getLogger(__name__)

_phrase_cache: Dict[str, List[Dict]] = {}
_last_rank_notified: Dict[int, str] = {}
_achievement_sent: Dict[int, int] = {}


def load_phrases():
    global _phrase_cache
    if not os.path.exists(PHRASES_FILE):
        with open(PHRASES_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        _phrase_cache = {}
        return

    try:
        with open(PHRASES_FILE, 'r', encoding='utf-8') as f:
            _phrase_cache = json.load(f)
        logger.info(f"📚 Загружено фраз: {sum(len(v) for v in _phrase_cache.values())}")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки фраз: {e}")
        _phrase_cache = {}


def save_phrases() -> bool:
    try:
        with open(PHRASES_FILE, 'w', encoding='utf-8') as f:
            json.dump(_phrase_cache, f, ensure_ascii=False, indent=2)
        logger.info("💾 Фразы сохранены")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения фраз: {e}")
        return False


def get_phrases_stats() -> Dict[str, int]:
    return {trigger: len(phrases) for trigger, phrases in _phrase_cache.items()}


def get_phrases_by_trigger(trigger: str) -> List[Dict]:
    return list(_phrase_cache.get(trigger, []))


def get_random_phrase(trigger: str, mood: Optional[str] = None) -> Optional[str]:
    phrases = _phrase_cache.get(trigger, [])
    if not phrases:
        return None

    if mood:
        filtered = [p for p in phrases if p.get('mood') == mood]
        if filtered:
            return random.choice(filtered)['text']

    return random.choice(phrases)['text']


def get_rank_phrase(rank_name: str) -> Optional[str]:
    phrases = _phrase_cache.get('RANK_UP', [])
    rank_phrases = [p for p in phrases if rank_name.lower() in p['text'].lower()]
    if rank_phrases:
        return random.choice(rank_phrases)['text']
    return None


def get_streak_achievement(day: int) -> Optional[str]:
    phrases = _phrase_cache.get('STREAK_ACHIEVEMENT', [])
    day_phrases = [p for p in phrases if str(day) in p['text']]
    if day_phrases:
        return random.choice(day_phrases)['text']
    return None


def parse_phrase(text: str) -> Optional[Dict]:
    if len(text) < 2:
        return None

    trigger_symbol = text[0]
    mood_symbol = text[1]

    if trigger_symbol not in TRIGGER_SYMBOLS or mood_symbol not in MOOD_SYMBOLS:
        return None

    phrase_text = text[2:].strip()
    if len(phrase_text) < 3:
        return None

    return {
        'trigger': TRIGGER_SYMBOLS[trigger_symbol],
        'mood': MOOD_SYMBOLS[mood_symbol],
        'text': phrase_text
    }


def add_phrase_direct(trigger: str, mood: str, phrase_text: str) -> Tuple[bool, str]:
    phrase_text = phrase_text.strip()
    if len(phrase_text) < 3:
        return False, "❌ Текст фразы слишком короткий (минимум 3 символа)."

    if trigger not in _phrase_cache:
        _phrase_cache[trigger] = []

    for p in _phrase_cache[trigger]:
        if p['text'].lower() == phrase_text.lower():
            return False, "⚠️ Такая фраза уже есть!"

    _phrase_cache[trigger].append({'text': phrase_text, 'mood': mood})
    save_phrases()
    return True, "✅ Фраза добавлена!"


def add_phrase_from_text(text: str) -> Tuple[bool, str, Optional[Dict]]:
    parsed = parse_phrase(text)
    if not parsed:
        return False, "❌ Неверный формат! Используй: <code>!₽ Текст</code>", None

    ok, msg = add_phrase_direct(parsed['trigger'], parsed['mood'], parsed['text'])
    return ok, msg, parsed if ok else None


def delete_phrase_by_index(trigger: str, index: int) -> bool:
    phrases = _phrase_cache.get(trigger)
    if not phrases or index < 0 or index >= len(phrases):
        return False
    del phrases[index]
    save_phrases()
    return True


def delete_phrase(trigger: str, text: str) -> bool:
    if trigger not in _phrase_cache:
        return False

    for i, p in enumerate(_phrase_cache[trigger]):
        if p['text'] == text:
            del _phrase_cache[trigger][i]
            save_phrases()
            return True
    return False


def get_all_phrases() -> List[Dict]:
    result = []
    for trigger, phrases in _phrase_cache.items():
        for p in phrases:
            result.append({
                'trigger': trigger,
                'mood': p.get('mood', 'SARCASTIC'),
                'text': p['text']
            })
    return result


async def process_user_message(
    user_id: int,
    chat_id: int,
    username: Optional[str] = None,
    tg_name: Optional[str] = None
) -> Dict:
    """
    Обрабатывает сообщение пользователя в основном чате.
    Возвращает словарь с индивидуальными (ephemeral) уведомлениями и флагом публикации.
    """
    if chat_id != CHAT_ID:
        return {'ephemeral_texts': [], 'allow_publish': False}

    if await is_user_banned(user_id):
        return {'ephemeral_texts': [], 'allow_publish': False}

    await register_user(user_id, username, tg_name)
    user = await get_user(user_id)
    if not user:
        return {'ephemeral_texts': [], 'allow_publish': False}

    ephemeral_texts: List[str] = []
    allow_publish = False

    last_activity_dt = parse_iso_utc(user.get('last_activity'))
    current_utc = now_utc()
    seconds_since = (current_utc - last_activity_dt).total_seconds() if last_activity_dt else 0.0

    return_trigger = None
    if seconds_since >= 7 * 24 * 3600:
        return_trigger = 'RETURN_AGO'
    elif seconds_since >= 24 * 3600:
        return_trigger = 'RETURN'

    # 1. Проверка пропуска > 24 часов
    if last_activity_dt and seconds_since >= 24 * 3600:
        if await is_shield_active(user_id):
            await deactivate_shield(user_id)
            ephemeral_texts.append(
                "🛡️ <b>Щит спас твой стрик!</b>\n"
                "Но защита сгорела — можешь купить новый щит в /shop."
            )
        else:
            old_streak = user['streak']
            if old_streak > 0:
                await update_streak(user_id, 0)
                await start_redemption(user_id, old_streak)
                await reset_redemption_failed_notified(user_id)
                ephemeral_texts.append(
                    f"💔 Стрик в <b>{old_streak} дн.</b> сброшен.\n\n"
                    f"🔄 <b>Шанс восстановить!</b>\n"
                    f"Напиши <b>{REDEMPTION_TARGET} сообщений</b> за {REDEMPTION_HOURS} часов.\n"
                    f"Прогресс: /redemption"
                )

    # 2. Обновляем активность и счётчик сообщений за день
    await update_last_activity(user_id)
    messages_today = await increment_messages_today(user_id)

    # 3. Проверка активного искупления
    redemption = await get_redemption_status(user_id)
    if redemption and redemption.get('active', False):
        exp = parse_iso_utc(redemption.get('expires_at'))
        if exp and exp < current_utc:
            await fail_redemption(user_id)
            ephemeral_texts.append("💀 Время на восстановление стрика вышло. Начинай с нуля!")
        else:
            progress = await update_redemption_progress(user_id, 1)
            target = redemption['target']
            if progress >= target:
                restored = redemption['streak_to_restore']
                await complete_redemption(user_id)
                ephemeral_texts.append(
                    f"🎉 <b>Ты восстановил стрик в {restored} дн.!</b> 🥦"
                )
                allow_publish = True
            elif progress % 50 == 0:
                remaining = target - progress
                ephemeral_texts.append(
                    f"📊 Искупление: <b>{progress}/{target}</b> сообщений. Осталось {remaining}!"
                )

    # 4. Перечитываем актуального пользователя (после возможного сброса/восстановления)
    user = await get_user(user_id)
    streak_awarded_today = user.get('streak_awarded_today', 0)
    new_streak = user['streak']
    streak_incremented = False

    if streak_awarded_today == 0 and messages_today >= DAILY_STREAK_MESSAGES:
        new_streak = await increment_streak_and_mark_today(user_id)
        streak_incremented = True

    # Начисление монет по рангу
    rank_info = get_rank_by_streak(new_streak)
    await add_coins(user_id, rank_info['income'])

    # 5. Повышение ранга
    current_rank = user.get('rank', 'Без ранга')
    is_rank_locked = user.get('rank_locked', 0) == 1

    if not is_rank_locked and rank_info['name'] != current_rank and rank_info['name'] != 'Без ранга':
        async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
            await db.execute("UPDATE users SET rank = ? WHERE user_id = ?", (rank_info['name'], user_id))
            await db.commit()

        if _last_rank_notified.get(user_id) != rank_info['name']:
            _last_rank_notified[user_id] = rank_info['name']
            phrase = get_rank_phrase(rank_info['name'])
            msg = f"🏆 Поздравляю! Ты достиг ранга <b>{html.escape(rank_info['name'])}</b>!"
            if phrase:
                msg += f"\n\n{html.escape(phrase)}"
            ephemeral_texts.append(msg)
            allow_publish = True

    # 6. Достижение круглой даты стрика
    if streak_incremented and new_streak in ACHIEVEMENT_DAYS:
        if _achievement_sent.get(user_id) != new_streak:
            _achievement_sent[user_id] = new_streak
            phrase = get_streak_achievement(new_streak)
            msg = f"🎉 <b>{new_streak} дней стрика!</b>"
            if phrase:
                msg += f"\n\n{html.escape(phrase)}"
            else:
                msg += " 🥦"
            ephemeral_texts.append(msg)
            allow_publish = True

    # 7. Ежедневное приветствие (при достижении порога сообщений) или случайный комментарий Деку
    if streak_incremented:
        trigger = return_trigger or 'DAILY_GREETING'
        phrase = get_random_phrase(trigger, 'SARCASTIC')
        if phrase:
            ephemeral_texts.append(html.escape(phrase))
    elif not ephemeral_texts:
        auto_reply = await get_setting('auto_reply_enabled', '1')
        if auto_reply == '1':
            try:
                chance = float(await get_setting('random_reply_chance', '0.2'))
            except ValueError:
                chance = 0.2
            if random.random() <= chance:
                phrase = get_random_phrase('DAILY_GREETING', 'SARCASTIC')
                if phrase:
                    ephemeral_texts.append(html.escape(phrase))

    return {
        'ephemeral_texts': ephemeral_texts,
        'allow_publish': allow_publish,
    }


async def check_inactive_users() -> List[Dict]:
    inactive = await get_inactive_users()
    result = []
    current_utc = now_utc()

    for user in inactive:
        last_time = parse_iso_utc(user.get('last_activity'))
        if not last_time:
            continue

        seconds_since = (current_utc - last_time).total_seconds()

        if seconds_since >= 30 * 24 * 3600:
            if not await is_user_banned(user['user_id']):
                await ban_user(user['user_id'])
                logger.info(f"🚫 Автобан: {user['user_id']} за 30 дней неактива")
                result.append({
                    'user': user,
                    'trigger': 'AUTO_BAN',
                    'mood': 'FURIOUS'
                })
            continue

        h24 = 24 * 3600
        h48 = 48 * 3600
        h_ultra = 7 * 24 * 3600
        h3 = 3 * 3600

        if seconds_since >= h24:
            last_ping = await get_last_ping(user['user_id'])
            if not last_ping or (current_utc - last_ping).total_seconds() >= h3:
                if seconds_since >= h_ultra:
                    trigger = 'MULTI_DAY_ULTRA'
                elif seconds_since >= h48:
                    trigger = 'MULTI_DAY_INACTIVE'
                else:
                    trigger = 'ONE_DAY_INACTIVE'

                await update_last_ping(user['user_id'])
                result.append({
                    'user': user,
                    'trigger': trigger,
                    'mood': 'FURIOUS'
                })

    return result


async def handle_inactive_user(user_id: int, trigger: str, mood: str) -> Optional[Dict]:
    user = await get_user(user_id)
    if not user:
        return None

    display_name = html.escape(
        user.get('name') or user.get('tg_name') or user.get('telegram_username') or str(user_id)
    )

    if trigger == 'AUTO_BAN':
        return {
            'user_id': user_id,
            'ephemeral_text': None,
            'chat_text': f"🚫 <b>{display_name}</b> не появлялся в чате 30 дней и получил автобан."
        }

    if await is_user_banned(user_id):
        return None

    if await is_shield_active(user_id):
        await deactivate_shield(user_id)
        return {
            'user_id': user_id,
            'ephemeral_text': "🛡️ <b>Щит спас твой стрик!</b> Но защита сгорела — купи новый в /shop.",
            'chat_text': None
        }

    last_time = parse_iso_utc(user.get('last_activity'))
    seconds_since = (now_utc() - last_time).total_seconds() if last_time else 0
    h48 = 48 * 3600

    redemption = await get_redemption_status(user_id)
    is_already_redeeming = bool(redemption and redemption.get('active', False))

    text = ""
    lost_streak_notice = None

    if not is_already_redeeming and user['streak'] > 0:
        old_streak = user['streak']
        await update_streak(user_id, 0)
        await start_redemption(user_id, old_streak)
        await reset_redemption_failed_notified(user_id)
        text = f"💔 Стрик в <b>{old_streak} дн.</b> сброшен.\n\n"
        lost_streak_notice = (
            f"😱 <b>{display_name}</b> потерял(а) стрик в {old_streak} дн.!\n"
            f"Шанс на искупление: {REDEMPTION_TARGET} сообщений за {REDEMPTION_HOURS} часов. 👀"
        )

    phrase = get_random_phrase(trigger, mood)
    if phrase:
        text += html.escape(phrase)
    else:
        if trigger in ('MULTI_DAY_INACTIVE', 'MULTI_DAY_ULTRA'):
            text += "😡 Ты пропал на несколько дней! Твой стрик на нуле, а ты всё молчишь!"
        else:
            text += "😠 Почему молчим? Возвращайся в чат!"

    if seconds_since >= h48:
        if not await get_redemption_failed_notified(user_id):
            text += "\n\n💀 Время на восстановление вышло. Стрик не восстановлен."
            await set_redemption_failed_notified(user_id, 1)
    elif is_already_redeeming:
        progress = redemption.get('progress', 0)
        target = redemption.get('target', REDEMPTION_TARGET)
        remaining = max(0, target - progress)
        text += (
            f"\n\n🔄 <b>Шанс восстановить стрик!</b>\n"
            f"Осталось написать <b>{remaining}</b> из {target} сообщений.\n"
            f"Прогресс: /redemption"
        )

    return {
        'user_id': user_id,
        'ephemeral_text': text.strip(),
        'chat_text': lost_streak_notice
    }


async def get_user_profile(user_id: int) -> Optional[str]:
    user = await get_user(user_id)
    if not user:
        return None

    shield_count = await get_shield_count(user_id)
    shield_active = await is_shield_active(user_id)

    redemption = await get_redemption_status(user_id)
    redemption_text = ""
    if redemption and redemption.get('active', False):
        progress = redemption.get('progress', 0)
        target = redemption.get('target', REDEMPTION_TARGET)
        redemption_text = f"\n🔄 Искупление: <b>{progress}/{target}</b> сообщений"

    shield_status = "🟢 Активен" if shield_active else "🔴 Не активен"
    messages_today = user.get('messages_today', 0)
    raw_name = user.get('name') or user.get('tg_name') or 'Без имени'
    safe_name = html.escape(raw_name)
    safe_rank = html.escape(user.get('rank') or 'Без ранга')

    return (
        f"📋 <b>Профиль — {safe_name}</b>\n\n"
        f"• Ранг: <b>{safe_rank}</b>\n"
        f"• Стрик: <b>{user['streak']} дн.</b> (рекорд: {user['streak_record']} дн.)\n"
        f"• Сообщений сегодня: <b>{messages_today}</b> / {DAILY_STREAK_MESSAGES}\n"
        f"• Монеты: <b>{user['coins']:.1f}</b>\n"
        f"• Щитов в запасе: <b>{shield_count} шт.</b>\n"
        f"• Щит: {shield_status}{redemption_text}"
    )


async def buy_shield(user_id: int) -> Tuple[bool, str]:
    user = await get_user(user_id)
    if not user:
        return False, "❌ Ты не зарегистрирован!"

    new_count = await buy_shield_atomic(user_id, SHIELD_PRICE)
    if new_count is None:
        return False, f"❌ Недостаточно монет! Нужно {SHIELD_PRICE}, у тебя {user['coins']:.1f}"

    return True, f"✅ Щит куплен! Теперь у тебя {new_count} шт."


async def activate_shield(user_id: int) -> Tuple[bool, str]:
    ok, code_or_count = await activate_shield_atomic(user_id, SHIELD_HOURS)
    if not ok:
        if code_or_count == -2:
            return False, "⏳ У тебя уже активен щит!"
        return False, "❌ У тебя нет щитов! Купи в /shop."

    return True, f"⚡ Щит активирован на {SHIELD_HOURS} часов! Осталось щитов: {code_or_count}"


async def get_top_streak_text(limit: int = 15) -> str:
    top = await get_top_streak(limit)
    if not top:
        return "📊 Пока нет данных. Напиши что-нибудь!"

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "🏆 <b>ТОП СТРИКОВ</b>\n\n"

    for i, user in enumerate(top):
        medal = medals[i] if i < 10 else f"{i + 1}."
        name = html.escape(user['display_name'])
        text += f"{medal} <b>{name}</b> — {user['streak']} дн.\n"

    text += "\n✨ Награды в воскресенье (23:00 МСК):\n🥇–5️⃣ по 1000 монет"
    return text


async def get_top_messages_text(limit: int = 15) -> str:
    top = await get_top_messages_today(limit)
    if not top:
        return "📊 Сегодня пока никто не писал. Будь первым!"

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    text = "💬 <b>ТОП СООБЩЕНИЙ (за сегодня)</b>\n\n"

    for i, user in enumerate(top):
        medal = medals[i] if i < 10 else f"{i + 1}."
        name = html.escape(user['display_name'])
        text += f"{medal} <b>{name}</b> — {user['messages_today']} сообщ.\n"

    text += "\n✨ Награды (00:00 МСК):\n🥇 500 | 🥈 250 | 🥉 100 монет"
    return text


async def admin_give_coins(admin_id: int, identifier: str, amount: float) -> Tuple[bool, str]:
    if not is_admin(admin_id):
        return False, "❌ Ты не админ!"

    user = await get_user_by_identifier(identifier)
    if not user:
        return False, f"❌ Пользователь '{html.escape(identifier)}' не найден."

    await add_coins(user['user_id'], amount)
    name = html.escape(user.get('name') or user.get('tg_name') or user.get('telegram_username') or identifier)
    return True, f"✅ {amount:+.1f} монет для <b>{name}</b> (ID: <code>{user['user_id']}</code>)"


async def admin_set_shield(admin_id: int, identifier: str, hours: int) -> Tuple[bool, str]:
    if not is_admin(admin_id):
        return False, "❌ Ты не админ!"

    user = await get_user_by_identifier(identifier)
    if not user:
        return False, f"❌ Пользователь '{html.escape(identifier)}' не найден."

    await set_shield_until(user['user_id'], hours)
    name = html.escape(user.get('name') or user.get('tg_name') or user.get('telegram_username') or identifier)
    if hours <= 0:
        return True, f"✅ Щит снят у <b>{name}</b> (ID: <code>{user['user_id']}</code>)"
    return True, f"✅ Щит на {hours} ч. выдан <b>{name}</b> (ID: <code>{user['user_id']}</code>)"


async def admin_set_rank(admin_id: int, identifier: str, rank_name: str) -> Tuple[bool, str]:
    if not is_admin(admin_id):
        return False, "❌ Ты не админ!"

    user = await get_user_by_identifier(identifier)
    if not user:
        return False, f"❌ Пользователь '{html.escape(identifier)}' не найден."

    name = html.escape(user.get('name') or user.get('tg_name') or user.get('telegram_username') or identifier)

    if rank_name.lower() == 'сброс':
        auto_rank = get_rank_by_streak(user['streak'])['name']
        async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
            await db.execute("UPDATE users SET rank = ?, rank_locked = 0 WHERE user_id = ?", (auto_rank, user['user_id']))
            await db.commit()
        return True, f"✅ Фиксированный ранг для <b>{name}</b> сброшен. Автоматический: <b>{html.escape(auto_rank)}</b>"

    valid_ranks = [r['name'] for r in RANKS]
    if rank_name not in valid_ranks:
        return False, f"❌ Неверный ранг. Доступны: {', '.join(valid_ranks)}, или 'сброс'"

    async with aiosqlite.connect(DB_PATH, timeout=30.0) as db:
        await db.execute("UPDATE users SET rank = ?, rank_locked = 1 WHERE user_id = ?", (rank_name, user['user_id']))
        await db.commit()

    return True, f"✅ Ранг <b>{html.escape(rank_name)}</b> зафиксирован для <b>{name}</b> (ID: <code>{user['user_id']}</code>)"


async def execute_daily_top(bot, is_forced: bool = False):
    winners = await award_daily_top()
    if winners:
        medals = ["🥇", "🥈", "🥉"]
        title = "ДОСРОЧНО" if is_forced else "ЗА ДЕНЬ"
        text = f"📊 <b>ТОП СООБЩЕНИЙ {title}</b>\n\n"

        for i, user in enumerate(winners):
            name = html.escape(user.get('display_name') or user.get('name') or user.get('telegram_username') or 'Участник')
            reward = [500, 250, 100][i] if i < 3 else 0
            text += f"{medals[i]} <b>{name}</b> — +{reward} монет ({user['messages_today']} сообщ.)\n"

        await bot.send_message(CHAT_ID, text)

    await reset_daily_messages()


async def execute_weekly_top(bot, is_forced: bool = False):
    winners = await award_weekly_top()
    if winners:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        title = "ДОСРОЧНО" if is_forced else "ЕЖЕНЕДЕЛЬНЫЙ"
        text = f"🏆 <b>{title} ТОП СТРИКОВ</b>\n\n"

        for i, (user, position, coins) in enumerate(winners):
            name = html.escape(user.get('display_name') or user.get('name') or user.get('telegram_username') or 'Участник')
            text += f"{medals[i]} <b>{name}</b> — {user['streak']} дн. (+{coins} монет)\n"

        await bot.send_message(CHAT_ID, text)