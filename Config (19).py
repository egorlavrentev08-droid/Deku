# config.py

import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHAT_ID = int(os.getenv("CHAT_ID", "-1002497100583"))
CHAT_LINK = os.getenv("CHAT_LINK", "https://t.me/Gar3mDi")

OWNER_ID = 6595788533

ADMIN_IDS = [
    OWNER_ID,
    1903870420,
    7975256831,
]

DB_PATH = "deku.db"
PHRASES_FILE = "phrases.json"

MSK = timezone(timedelta(hours=3))

RANKS = [
    {"name": "Без ранга", "min_streak": 0, "income": 0.10},
    {"name": "Новичок", "min_streak": 2, "income": 0.10},
    {"name": "Кандидат", "min_streak": 4, "income": 0.15},
    {"name": "Знакомый", "min_streak": 7, "income": 0.20},
    {"name": "Хороший", "min_streak": 11, "income": 0.25},
    {"name": "Душа", "min_streak": 16, "income": 0.30},
    {"name": "Старожил", "min_streak": 24, "income": 0.35},
    {"name": "Гордость", "min_streak": 34, "income": 0.40},
    {"name": "Авторитет", "min_streak": 45, "income": 0.45},
    {"name": "Незаменимый", "min_streak": 61, "income": 0.50},
]

TRIGGER_SYMBOLS = {
    '!': 'DAILY_GREETING',
    '?': 'ONE_DAY_INACTIVE',
    '¡': 'MULTI_DAY_INACTIVE',
    '¿': 'MULTI_DAY_ULTRA',
    '~': 'RETURN',
    '≈': 'RETURN_AGO',
    '∆': 'RANK_UP',
    '%': 'STREAK_ACHIEVEMENT',
}

TRIGGER_NAMES = {
    'DAILY_GREETING': 'приветствие',
    'ONE_DAY_INACTIVE': 'день неактива',
    'MULTI_DAY_INACTIVE': 'несколько дней',
    'MULTI_DAY_ULTRA': 'вечность',
    'RETURN': 'возвращение',
    'RETURN_AGO': 'долгое возвращение',
    'RANK_UP': 'повышение ранга',
    'STREAK_ACHIEVEMENT': 'достижение стрика',
}

TRIGGER_EMOJI = {
    'DAILY_GREETING': '🌅',
    'ONE_DAY_INACTIVE': '❗',
    'MULTI_DAY_INACTIVE': '❓',
    'MULTI_DAY_ULTRA': '💀',
    'RETURN': '🔄',
    'RETURN_AGO': '⏳',
    'RANK_UP': '🏆',
    'STREAK_ACHIEVEMENT': '⭐',
}

MOOD_SYMBOLS = {
    '₽': 'SARCASTIC',
    '£': 'ANGRY',
    '$': 'FURIOUS',
}

MOOD_NAMES = {
    'SARCASTIC': 'саркастичное',
    'ANGRY': 'злое',
    'FURIOUS': 'гневное',
}

MOOD_EMOJI = {
    'SARCASTIC': '😏',
    'ANGRY': '😡',
    'FURIOUS': '🤬',
}

ACHIEVEMENT_DAYS = [7, 14, 31, 99, 356]

DAILY_STREAK_MESSAGES = 100
REDEMPTION_TARGET = 200
REDEMPTION_HOURS = 24
SHIELD_PRICE = 100
SHIELD_HOURS = 36


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_allowed_chat(chat_id: int) -> bool:
    return chat_id == CHAT_ID


def get_rank_by_streak(streak: int) -> dict:
    for rank in reversed(RANKS):
        if streak >= rank["min_streak"]:
            return rank
    return RANKS[0]