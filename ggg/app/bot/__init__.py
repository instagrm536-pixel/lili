import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config import settings
from app.logging import setup_logging
from app.bot.handlers.start import router as start_router
from app.bot.handlers.qr import router as qr_router
from app.bot.handlers.media import router as media_router
from app.bot.handlers.dating import router as dating_router
from app.bot.handlers.callbacks import router as callback_router
from app.database.db import init_database

log = logging.getLogger(__name__)


def build_bot() -> Bot:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty. Put it in .env")

    kwargs = {
        "token": settings.bot_token,
        "default": DefaultBotProperties(parse_mode=ParseMode.HTML),
    }

    if settings.bot_api_base_url:
        from aiogram.client.telegram import TelegramAPIServer
        from aiogram.client.session.aiohttp import AiohttpSession

        server = TelegramAPIServer.from_base(settings.bot_api_base_url)
        session = AiohttpSession(api=server)
        kwargs["session"] = session

    return Bot(**kwargs)


async def run():
    setup_logging()
    bot = build_bot()
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(qr_router)
    dp.include_router(media_router)
    dp.include_router(dating_router)
    dp.include_router(callback_router)

    await init_database()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Botni boshlash"),
            BotCommand(command="help", description="Yordam"),
            BotCommand(command="qr", description="QR Generator"),
        ]
    )
    log.info("AEwolf bot starting")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)