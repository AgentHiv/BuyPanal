"""Application builder: wires Database, BuyListener, handlers and notifier."""

from __future__ import annotations

import asyncio
import logging
import os

from telegram.ext import Application, CommandHandler

from chain.incubation import get_curve_info
from chain.listener import BuyListener
from core.config import load_config
from core.db import Database
from core.models import BuyEvent

from bot import notifier
from bot.handlers import BOT_COMMANDS, HANDLERS

logger = logging.getLogger(__name__)


def run() -> None:
    """Entry point: build the Application and start polling + listener."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    config = load_config()
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN is not set (see .env.example)")

    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    db = Database(config.DB_PATH)

    notifier.set_config(config)

    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.bot_data["db"] = db
    app.bot_data["config"] = config

    # --- buy listener wiring ---------------------------------------------
    async def on_buy(chat_ids: list[int], buy: BuyEvent) -> None:
        curve = None
        if buy.kind == "curve":
            try:
                curve = await get_curve_info(buy.token_address)
            except Exception:
                logger.exception("get_curve_info failed for %s", buy.token_address)
        for chat_id in chat_ids:
            try:
                settings = db.get_settings(chat_id)
                db.record_buy(chat_id, buy)
                await notifier.send_buy_alert(app.bot, chat_id, settings, buy, curve)
            except Exception:
                logger.exception("failed to notify chat %s", chat_id)

    listener = BuyListener(on_buy)
    listener.set_chat_resolver(lambda address: db.all_tracked_tokens().get(address, []))
    app.bot_data["listener"] = listener

    for name, fn in HANDLERS.items():
        app.add_handler(CommandHandler(name, fn))

    # --- lifecycle ---------------------------------------------------------
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(BOT_COMMANDS)
        # register tokens already stored in the db
        for address in db.all_tracked_tokens():
            try:
                await listener.add_token(address)
            except Exception:
                logger.exception("listener.add_token failed for %s", address)
        application.bot_data["listener_task"] = asyncio.create_task(listener.start())
        logger.info("buy listener started")

    async def post_shutdown(application: Application) -> None:
        task = application.bot_data.get("listener_task")
        try:
            await listener.stop()
        except Exception:
            logger.exception("listener.stop failed")
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info("starting Telegram polling")
    app.run_polling(close_loop=False)
