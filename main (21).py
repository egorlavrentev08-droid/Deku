# main.py

import asyncio
import html
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommandScopeDefault,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChatAdministrators,
    BotCommandScopeChat
)

from config import BOT_TOKEN, CHAT_ID, OWNER_ID, MSK
from db import init_db, check_expired_redemptions, get_user, get_setting
from core import (
    load_phrases, check_inactive_users, handle_inactive_user,
    execute_daily_top, execute_weekly_top
)
from bot import register_handlers, send_ephemeral
import admin_panel
from migrate import run_migration

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def setup_bot_commands(bot: Bot):
    group_commands = [
        types.BotCommand(command="me", description="Твой профиль"),
        types.BotCommand(command="shop", description="Магазин щитов"),
        types.BotCommand(command="top", description="Топы чата"),
        types.BotCommand(command="redemption", description="Статус искупления"),
        types.BotCommand(command="name", description="Сменить имя"),
        types.BotCommand(command="help", description="Помощь"),
    ]

    # Сбрасываем более узкие админские/языковые области, которые могут перекрывать список команд
    override_scopes = [
        BotCommandScopeAllChatAdministrators(),
        BotCommandScopeChatAdministrators(chat_id=CHAT_ID),
    ]
    for scope in override_scopes:
        for lang in (None, "ru", "en"):
            try:
                await bot.delete_my_commands(scope=scope, language_code=lang)
            except Exception:
                pass

    # Устанавливаем команды для всех пользователей в группе и по умолчанию
    target_scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeChat(chat_id=CHAT_ID),
    ]
    for scope in target_scopes:
        try:
            await bot.set_my_commands(group_commands, scope=scope)
        except Exception as e:
            logger.error(f"❌ Не удалось установить команды для {scope.type}: {e}")

    # Очищаем команды в ЛС для обычных пользователей
    try:
        await bot.set_my_commands([], scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        logger.warning(f"⚠️ Не удалось очистить команды ЛС: {e}")

    # Команды меню в ЛС только для создателя
    owner_commands = [
        types.BotCommand(command="panel", description="Панель управления"),
        types.BotCommand(command="stats", description="Статистика бота"),
        types.BotCommand(command="users", description="Список пользователей"),
        types.BotCommand(command="phrases", description="Управление фразами"),
        types.BotCommand(command="topfast", description="Топы и награды"),
        types.BotCommand(command="broadcast", description="Отправить сообщение в чат"),
    ]
    try:
        await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
    except Exception as e:
        logger.warning(f"⚠️ Не удалось установить команды для OWNER_ID: {e}")

    try:
        registered = await bot.get_my_commands(scope=BotCommandScopeChat(chat_id=CHAT_ID))
        logger.info(f"✅ Команды меню в чате {CHAT_ID}: {[c.command for c in registered]}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось проверить get_my_commands: {e}")


async def main():
    if not BOT_TOKEN:
        logger.critical("❌ BOT_TOKEN не найден в .env! Запуск невозможен.")
        return

    logger.info("🥦 Запуск Деку...")

    await init_db()
    await run_migration()
    load_phrases()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Сначала роутер панели создателя (ЛС), затем обработчики чата
    dp.include_router(admin_panel.router)
    register_handlers(dp, bot)

    await setup_bot_commands(bot)

    scheduler_task = asyncio.create_task(scheduler_loop(bot))

    logger.info("🚀 Деку успешно запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()


async def scheduler_loop(bot: Bot):
    logger.info("⏰ Планировщик запущен")

    last_inactive_check = datetime.now(MSK)
    last_daily_run = None
    last_weekly_run = None
    last_redemption_run = None

    while True:
        try:
            now = datetime.now(MSK)
            current_date = now.strftime("%Y-%m-%d")
            current_hour = now.strftime("%Y-%m-%d %H")

            if (now - last_inactive_check).total_seconds() >= 1800:
                last_inactive_check = now
                if await get_setting('inactive_pings_enabled', '1') == '1':
                    asyncio.create_task(check_inactive_task(bot))

            if now.hour == 0 and now.minute == 0:
                if last_daily_run != current_date:
                    last_daily_run = current_date
                    asyncio.create_task(daily_reset_task(bot))

            if now.weekday() == 6 and now.hour == 23 and now.minute == 0:
                if last_weekly_run != current_date:
                    last_weekly_run = current_date
                    asyncio.create_task(weekly_reward_task(bot))

            if now.minute == 0:
                if last_redemption_run != current_hour:
                    last_redemption_run = current_hour
                    asyncio.create_task(check_redemptions_task(bot))

        except Exception as e:
            logger.error(f"❌ Ошибка в планировщике: {e}")

        await asyncio.sleep(10)


async def check_inactive_task(bot: Bot):
    logger.info("⏳ Проверка неактивных пользователей...")
    inactive_list = await check_inactive_users()

    for item in inactive_list:
        user_id = item['user']['user_id']
        res = await handle_inactive_user(user_id, item['trigger'], item['mood'])
        if not res:
            continue
        try:
            if res.get('ephemeral_text'):
                await send_ephemeral(bot, CHAT_ID, user_id, res['ephemeral_text'])
            if res.get('chat_text'):
                await bot.send_message(CHAT_ID, res['chat_text'])
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления неактивному {user_id}: {e}")


async def daily_reset_task(bot: Bot):
    logger.info("🔄 Ежедневный сброс")
    await execute_daily_top(bot)
    logger.info("✅ Ежедневный сброс завершён")


async def weekly_reward_task(bot: Bot):
    logger.info("🔄 Еженедельные награды")
    await execute_weekly_top(bot)
    logger.info("✅ Еженедельные награды завершены")


async def check_redemptions_task(bot: Bot):
    expired = await check_expired_redemptions()

    for user_id in expired:
        try:
            user = await get_user(user_id)
            if user:
                name = html.escape(
                    user.get('name') or user.get('tg_name') or user.get('telegram_username') or 'Кто-то'
                )
                await send_ephemeral(
                    bot,
                    CHAT_ID,
                    user_id,
                    "💀 Время вышло. Стрик не восстановлен. Начинай с нуля! 💪"
                )
                await bot.send_message(
                    CHAT_ID,
                    f"⏰ <b>{name}</b> не успел(а) восстановить стрик. Начинает с нуля. 😈"
                )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления об искуплении для {user_id}: {e}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🥦 Деку остановлен")