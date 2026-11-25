# db.py

import aiosqlite
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

from config import DB_PATH, REDEMPTION_TARGET, REDEMPTION_HOURS, SHIELD_PRICE, SHIELD_HOURS

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _connect():
    return aiosqlite.connect(DB_PATH, timeout=30.0)


async def init_db():
    async with _connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                telegram_username TEXT,
                tg_name TEXT,
                rank TEXT DEFAULT 'Без ранга',
                streak INTEGER DEFAULT 0,
                streak_record INTEGER DEFAULT 0,
                coins REAL DEFAULT 0,
                shield_count INTEGER DEFAULT 0,
                shield_until TEXT,
                last_activity TEXT,
                messages_today INTEGER DEFAULT 0,
                streak_awarded_today INTEGER DEFAULT 0,
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                redemption_active INTEGER DEFAULT 0,
                redemption_target INTEGER DEFAULT 200,
                redemption_progress INTEGER DEFAULT 0,
                redemption_streak_to_restore INTEGER DEFAULT 0,
                redemption_expires_at TEXT,
                is_banned INTEGER DEFAULT 0,
                last_ping TEXT,
                redemption_failed_notified INTEGER DEFAULT 0,
                rank_locked INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rewards_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reward_type TEXT,
                position INTEGER,
                coins INTEGER,
                streak INTEGER,
                awarded_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('auto_reply_enabled', '1')")
        await db.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('random_reply_chance', '0.2')")
        await db.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('inactive_pings_enabled', '1')")

        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_streak ON users(streak)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_messages_today ON users(messages_today)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity)")

        await db.commit()
        logger.info("✅ База данных инициализирована")


async def get_setting(key: str, default: str = "") -> str:
    async with _connect() as db:
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str):
    async with _connect() as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        await db.commit()


async def register_user(user_id: int, username: Optional[str] = None, tg_name: Optional[str] = None) -> bool:
    now_str = now_utc_iso()
    async with _connect() as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        exists = await cursor.fetchone()

        if exists:
            await db.execute(
                "UPDATE users SET telegram_username = ?, tg_name = ? WHERE user_id = ?",
                (username, tg_name, user_id)
            )
            await db.commit()
            return False

        await db.execute(
            """INSERT INTO users (user_id, name, telegram_username, tg_name, last_activity, registered_at)
               VALUES (?, NULL, ?, ?, ?, ?)""",
            (user_id, username, tg_name, now_str, now_str)
        )
        await db.commit()
        logger.info(f"✅ Новый пользователь: {tg_name or username or str(user_id)} (ID: {user_id})")
        return True


async def get_user(user_id: int) -> Optional[Dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_identifier(identifier: str) -> Optional[Dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row

        if identifier.isdigit():
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (int(identifier),))
            row = await cursor.fetchone()
            if row:
                return dict(row)

        clean = identifier.lstrip('@').lower()
        cursor = await db.execute(
            "SELECT * FROM users WHERE LOWER(telegram_username) = ?",
            (clean,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)

        cursor = await db.execute("SELECT * FROM users WHERE LOWER(name) = LOWER(?)", (identifier,))
        row = await cursor.fetchone()
        if row:
            return dict(row)

        return None


async def update_user_name(user_id: int, new_name: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE LOWER(name) = LOWER(?) AND user_id != ?",
            (new_name, user_id)
        )
        exists = await cursor.fetchone()
        if exists:
            return False

        await db.execute("UPDATE users SET name = ? WHERE user_id = ?", (new_name, user_id))
        await db.commit()
        return True


async def update_last_activity(user_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET last_activity = ? WHERE user_id = ?",
            (now_utc_iso(), user_id)
        )
        await db.commit()


async def add_coins(user_id: int, amount: float):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET coins = MAX(0, coins + ?) WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()


async def get_coins(user_id: int) -> float:
    async with _connect() as db:
        cursor = await db.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0.0


async def add_shield(user_id: int, count: int = 1):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET shield_count = MAX(0, shield_count + ?) WHERE user_id = ?",
            (count, user_id)
        )
        await db.commit()


async def buy_shield_atomic(user_id: int, price: int = SHIELD_PRICE) -> Optional[int]:
    async with _connect() as db:
        cursor = await db.execute(
            "UPDATE users SET coins = coins - ?, shield_count = shield_count + 1 "
            "WHERE user_id = ? AND coins >= ?",
            (price, user_id, price)
        )
        if cursor.rowcount == 0:
            return None
        await db.commit()
        cursor = await db.execute("SELECT shield_count FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def activate_shield_atomic(user_id: int, hours: int = SHIELD_HOURS) -> tuple[bool, int]:
    until_iso = (now_utc() + timedelta(hours=hours)).isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT shield_count, shield_until FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False, -1
        if row['shield_count'] <= 0:
            return False, 0
        existing_until = parse_iso_utc(row['shield_until'])
        if existing_until and existing_until > now_utc():
            return False, -2

        upd = await db.execute(
            "UPDATE users SET shield_count = shield_count - 1, shield_until = ? "
            "WHERE user_id = ? AND shield_count > 0",
            (until_iso, user_id)
        )
        await db.commit()
        if upd.rowcount == 0:
            return False, 0
        return True, row['shield_count'] - 1


async def get_shield_count(user_id: int) -> int:
    async with _connect() as db:
        cursor = await db.execute("SELECT shield_count FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def set_shield_until(user_id: int, hours: int):
    async with _connect() as db:
        if hours <= 0:
            await db.execute("UPDATE users SET shield_until = NULL WHERE user_id = ?", (user_id,))
        else:
            until_iso = (now_utc() + timedelta(hours=hours)).isoformat()
            await db.execute("UPDATE users SET shield_until = ? WHERE user_id = ?", (until_iso, user_id))
        await db.commit()


async def is_shield_active(user_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute("SELECT shield_until FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            shield_until = parse_iso_utc(row[0])
            return bool(shield_until and shield_until > now_utc())
        return False


async def deactivate_shield(user_id: int):
    async with _connect() as db:
        await db.execute("UPDATE users SET shield_until = NULL WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_streak(user_id: int) -> int:
    async with _connect() as db:
        cursor = await db.execute("SELECT streak FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def update_streak(user_id: int, new_streak: int) -> int:
    async with _connect() as db:
        cursor = await db.execute("SELECT streak_record FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current_record = row[0] if row else 0
        new_record = max(new_streak, current_record)

        await db.execute(
            "UPDATE users SET streak = ?, streak_record = ? WHERE user_id = ?",
            (new_streak, new_record, user_id)
        )
        await db.commit()
        return new_record


async def increment_streak_and_mark_today(user_id: int) -> int:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT streak, streak_record, streak_awarded_today FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        if row['streak_awarded_today'] >= 1:
            return row['streak']

        new_streak = row['streak'] + 1
        new_record = max(new_streak, row['streak_record'])
        await db.execute(
            "UPDATE users SET streak = ?, streak_record = ?, streak_awarded_today = 1 "
            "WHERE user_id = ? AND streak_awarded_today = 0",
            (new_streak, new_record, user_id)
        )
        await db.commit()
        return new_streak


async def increment_messages_today(user_id: int) -> int:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET messages_today = messages_today + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
        cursor = await db.execute("SELECT messages_today FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0


async def reset_daily_messages():
    async with _connect() as db:
        await db.execute("UPDATE users SET messages_today = 0, streak_awarded_today = 0")
        await db.commit()


def _format_display_name(data: dict) -> str:
    return data.get('name') or data.get('tg_name') or data.get('telegram_username') or str(data['user_id'])


async def get_top_streak(limit: int = 15) -> List[Dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT user_id, name, telegram_username, tg_name, streak, streak_record
            FROM users
            WHERE streak > 0 AND is_banned = 0
            ORDER BY streak DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['display_name'] = _format_display_name(data)
            result.append(data)
        return result


async def get_top_messages_today(limit: int = 15) -> List[Dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT user_id, name, telegram_username, tg_name, messages_today
            FROM users
            WHERE messages_today > 0 AND is_banned = 0
            ORDER BY messages_today DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['display_name'] = _format_display_name(data)
            result.append(data)
        return result


async def start_redemption(user_id: int, lost_streak: int):
    expires_at = (now_utc() + timedelta(hours=REDEMPTION_HOURS)).isoformat()

    async with _connect() as db:
        await db.execute("""
            UPDATE users
            SET redemption_active = 1,
                redemption_target = ?,
                redemption_progress = 0,
                redemption_streak_to_restore = ?,
                redemption_expires_at = ?,
                redemption_failed_notified = 0
            WHERE user_id = ?
        """, (REDEMPTION_TARGET, lost_streak, expires_at, user_id))
        await db.commit()


async def update_redemption_progress(user_id: int, increment: int = 1) -> int:
    async with _connect() as db:
        await db.execute("""
            UPDATE users
            SET redemption_progress = redemption_progress + ?
            WHERE user_id = ? AND redemption_active = 1
        """, (increment, user_id))
        await db.commit()
        cursor = await db.execute(
            "SELECT redemption_progress FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_redemption_status(user_id: int) -> Optional[Dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT redemption_active, redemption_target, redemption_progress,
                   redemption_streak_to_restore, redemption_expires_at
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        row = await cursor.fetchone()
        if row:
            return {
                'active': row['redemption_active'] == 1,
                'target': row['redemption_target'] if row['redemption_target'] is not None else REDEMPTION_TARGET,
                'progress': row['redemption_progress'] if row['redemption_progress'] is not None else 0,
                'streak_to_restore': row['redemption_streak_to_restore'] if row['redemption_streak_to_restore'] is not None else 0,
                'expires_at': row['redemption_expires_at']
            }
        return None


async def complete_redemption(user_id: int):
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT redemption_streak_to_restore, streak_record FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        streak_to_restore = row['redemption_streak_to_restore'] or 0
        new_record = max(streak_to_restore, row['streak_record'] or 0)

        await db.execute("""
            UPDATE users
            SET streak = ?,
                streak_record = ?,
                redemption_active = 0,
                redemption_progress = 0,
                redemption_target = 0,
                redemption_streak_to_restore = 0,
                redemption_expires_at = NULL
            WHERE user_id = ?
        """, (streak_to_restore, new_record, user_id))
        await db.commit()


async def fail_redemption(user_id: int):
    async with _connect() as db:
        await db.execute("""
            UPDATE users
            SET redemption_active = 0,
                redemption_progress = 0,
                redemption_target = 0,
                redemption_streak_to_restore = 0,
                redemption_expires_at = NULL
            WHERE user_id = ?
        """, (user_id,))
        await db.commit()


async def check_expired_redemptions() -> List[int]:
    current_utc = now_utc()
    expired_ids = []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT user_id, redemption_expires_at FROM users
            WHERE redemption_active = 1
        """)
        rows = await cursor.fetchall()
        for row in rows:
            exp = parse_iso_utc(row['redemption_expires_at'])
            if exp and exp < current_utc:
                expired_ids.append(row['user_id'])

    for uid in expired_ids:
        await fail_redemption(uid)
    return expired_ids


async def add_reward_history(user_id: int, reward_type: str, position: int, coins: int, streak: int = 0):
    async with _connect() as db:
        await db.execute("""
            INSERT INTO rewards_history (user_id, reward_type, position, coins, streak, awarded_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, reward_type, position, coins, streak, now_utc_iso()))
        await db.commit()


async def award_daily_top() -> List[Dict]:
    top = await get_top_messages_today(3)
    rewards = [500, 250, 100]
    awarded = []

    for i, user in enumerate(top):
        if user['messages_today'] > 0:
            coins = rewards[i] if i < len(rewards) else 0
            await add_coins(user['user_id'], coins)
            await add_reward_history(user['user_id'], 'daily_top', i + 1, coins, user['messages_today'])
            awarded.append(user)

    return awarded


async def award_weekly_top() -> List[tuple]:
    top = await get_top_streak(5)
    rewards = [(1000, 1), (1000, 2), (1000, 3), (1000, 4), (1000, 5)]

    awarded = []
    for i, (coins, position) in enumerate(rewards):
        if i < len(top) and top[i]['streak'] > 0:
            user = top[i]
            await add_coins(user['user_id'], coins)
            await add_reward_history(user['user_id'], 'weekly_top', position, coins, user['streak'])
            awarded.append((user, position, coins))

    return awarded


async def get_inactive_users() -> List[Dict]:
    current_utc = now_utc()
    cutoff = current_utc - timedelta(hours=24)
    result = []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT user_id, name, telegram_username, tg_name, last_activity, streak, shield_until
            FROM users
            WHERE is_banned = 0 AND last_activity IS NOT NULL
        """)
        rows = await cursor.fetchall()
        for row in rows:
            last_act = parse_iso_utc(row['last_activity'])
            if not last_act or last_act >= cutoff:
                continue
            shield_until = parse_iso_utc(row['shield_until'])
            if shield_until and shield_until > current_utc:
                continue
            data = dict(row)
            data['display_name'] = _format_display_name(data)
            result.append(data)
    return result


async def ban_user(user_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0] == 1:
            return False

        await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
        return True


async def pardon_user(user_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row or row[0] == 0:
            return False

        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        await db.commit()
        return True


async def is_user_banned(user_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return bool(row and row[0] == 1)


async def get_last_ping(user_id: int) -> Optional[datetime]:
    async with _connect() as db:
        cursor = await db.execute("SELECT last_ping FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return parse_iso_utc(row[0]) if row and row[0] else None


async def update_last_ping(user_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET last_ping = ? WHERE user_id = ?",
            (now_utc_iso(), user_id)
        )
        await db.commit()


async def get_redemption_failed_notified(user_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT redemption_failed_notified FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return bool(row and row[0] == 1)


async def set_redemption_failed_notified(user_id: int, value: int = 1):
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET redemption_failed_notified = ? WHERE user_id = ?",
            (value, user_id)
        )
        await db.commit()


async def reset_redemption_failed_notified(user_id: int):
    await set_redemption_failed_notified(user_id, 0)


async def get_all_stats() -> Dict:
    async with _connect() as db:
        cursor = await db.execute("""
            SELECT
                COUNT(*) as total_users,
                SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) as banned_users,
                SUM(CASE WHEN messages_today > 0 AND is_banned = 0 THEN 1 ELSE 0 END) as active_today,
                SUM(CASE WHEN redemption_active = 1 THEN 1 ELSE 0 END) as redeeming_users,
                COALESCE(SUM(coins), 0) as total_coins,
                COALESCE(SUM(messages_today), 0) as total_messages_today,
                COALESCE(MAX(streak), 0) as max_streak
            FROM users
        """)
        row = await cursor.fetchone()
        return {
            'total_users': row[0] or 0,
            'banned_users': row[1] or 0,
            'active_today': row[2] or 0,
            'redeeming_users': row[3] or 0,
            'total_coins': float(row[4] or 0),
            'total_messages_today': row[5] or 0,
            'max_streak': row[6] or 0,
        }


async def get_users_list(offset: int = 0, limit: int = 10, filter_type: str = "all") -> tuple[List[Dict], int]:
    where = "1=1"
    if filter_type == "banned":
        where = "is_banned = 1"
    elif filter_type == "active":
        where = "messages_today > 0 AND is_banned = 0"
    elif filter_type == "redeeming":
        where = "redemption_active = 1"

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        c_total = await db.execute(f"SELECT COUNT(*) FROM users WHERE {where}")
        total = (await c_total.fetchone())[0]

        cursor = await db.execute(
            f"SELECT user_id, name, telegram_username, tg_name, streak, coins, is_banned, rank "
            f"FROM users WHERE {where} ORDER BY streak DESC, coins DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['display_name'] = _format_display_name(data)
            result.append(data)
        return result, total