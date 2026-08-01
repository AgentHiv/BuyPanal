"""Application builder: wires Database, BuyListener, handlers and notifier.

SPEC-v2 §8.3: builds the shared ``deps`` namespace, registers the advanced
command module and the button-menu callbacks (both behind ImportError
guards so the bot keeps working while the parallel modules are developed),
and wires the price monitor / new-token scanner as background tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler

from chain.incubation import get_curve_info
from chain.listener import BuyListener
from core.config import load_config
from core.db import Database
from core.i18n import t

from bot import notifier
from bot.handlers import BOT_COMMANDS, HANDLERS

logger = logging.getLogger(__name__)

# --- guarded parallel-module imports (SPEC-v2: modules built by Coder E) ---
try:  # button-menu callbacks live in this branch; guard kept for symmetry
    from bot import callbacks as ui_callbacks
except ImportError:  # pragma: no cover
    ui_callbacks = None
    logger.warning("bot.callbacks not available; button menus disabled")

try:
    from bot import advanced_handlers
except ImportError:
    advanced_handlers = None
    logger.info("bot.advanced_handlers not available yet; advanced commands disabled")

try:
    from chain.monitor import PriceMonitor
except ImportError:
    PriceMonitor = None
    logger.info("chain.monitor not available yet; price alerts disabled")

try:
    from chain.scanner import NewTokenScanner
except ImportError:
    NewTokenScanner = None
    logger.info("chain.scanner not available yet; launch scanner disabled")

try:
    from core.models import SellEvent
except ImportError:
    SellEvent = None


def _link_buttons(config, token_address: str) -> InlineKeyboardMarkup:
    """[Chart][Buy] buttons for monitor/scanner messages."""
    chart_url = f"{config.EXPLORER_URL.rstrip('/')}/token/{token_address}"
    template = config.BUY_URL_TEMPLATE or "https://nad.fun/token/{token}"
    buy_url = template.replace("{token}", token_address) if "{token}" in template else template
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Chart", url=chart_url), InlineKeyboardButton("Buy", url=buy_url)]]
    )


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

    # --- buy/sell listener wiring ------------------------------------------
    def _current_mon_usd() -> float:
        """Best known MON price in USD (auto cache, else manual override)."""
        return float(
            app.bot_data.get("mon_usd_price")
            or getattr(config, "MON_USD_PRICE", 0.0)
            or 0.0
        )

    async def on_event(chat_ids: list[int], event) -> None:
        """Handle BuyEvent and SellEvent from the listener (SPEC-v2 §8.3)."""
        is_sell = SellEvent is not None and isinstance(event, SellEvent)
        mon_usd = _current_mon_usd()  # SPEC-v3: USDT thresholds need the rate
        curve = None
        if getattr(event, "kind", None) == "curve":
            try:
                curve = await get_curve_info(event.token_address)
            except Exception:
                logger.exception("get_curve_info failed for %s", event.token_address)
        for chat_id in chat_ids:
            try:
                settings = db.get_settings(chat_id)
                if is_sell:
                    # sell alerts are opt-in per group
                    if not getattr(settings, "sell_alerts", False):
                        continue
                    send_sell = getattr(notifier, "send_sell_alert", None)
                    if send_sell is None:
                        logger.warning("notifier.send_sell_alert not available yet")
                        continue
                    await send_sell(app.bot, chat_id, settings, event, curve, mon_usd=mon_usd)
                else:
                    db.record_buy(chat_id, event)
                    await notifier.send_buy_alert(app.bot, chat_id, settings, event, curve, mon_usd=mon_usd)
            except Exception:
                logger.exception("failed to notify chat %s", chat_id)

    listener = BuyListener(on_event)
    listener.set_chat_resolver(lambda address: db.all_tracked_tokens().get(address, []))
    set_sell_callback = getattr(listener, "set_sell_callback", None)
    if callable(set_sell_callback):
        set_sell_callback(on_event)
    app.bot_data["listener"] = listener

    # --- price monitor (guarded; built by Coder E) --------------------------
    monitor = None

    async def on_price_alert(alert, current_price_mon: float) -> None:
        """Deactivate the triggered alert and notify the chat with buttons."""
        try:
            db.deactivate_price_alert(alert.id, alert.chat_id)
        except Exception:
            logger.exception("deactivate_price_alert failed for %s", alert.id)
        try:
            lang = db.get_settings(alert.chat_id).language
            # SPEC-v3: USD alerts report USDT values; MON alerts keep MON.
            if getattr(alert, "currency", "MON") == "USD" and alert.target_usd is not None:
                mon_usd = _current_mon_usd()
                price_str = f"{current_price_mon * mon_usd:,.4f} USDT"
                target_str = f"{alert.target_usd:g} USDT"
            else:
                price_str = f"{current_price_mon:g} MON"
                target_str = f"{alert.target_mon:g} MON"
            text = t(
                lang,
                "adv.pricealert_triggered",
                symbol=alert.token_address,
                direction=alert.direction,
                target=target_str,
                price=price_str,
            )
            if text == "adv.pricealert_triggered":  # adv locales not merged yet
                text = (
                    f"🔔 Price alert: {alert.token_address} is now "
                    f"{price_str} ({alert.direction} {target_str})"
                )
            await app.bot.send_message(
                chat_id=alert.chat_id,
                text=text,
                reply_markup=_link_buttons(config, alert.token_address),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("failed to send price alert to chat %s", alert.chat_id)

    if PriceMonitor is not None:
        try:
            monitor = PriceMonitor(on_price_alert)
            provider = getattr(db, "all_active_price_alerts", None)
            if callable(provider) and hasattr(monitor, "set_alert_provider"):
                monitor.set_alert_provider(provider)
            # SPEC-v3: USD-denominated alerts need the MON->USD rate
            if hasattr(monitor, "set_price_provider"):
                monitor.set_price_provider(_current_mon_usd)
        except Exception:
            logger.exception("failed to initialise PriceMonitor")
            monitor = None

    # --- new-token scanner (guarded; built by Coder E) ----------------------
    scanner = None

    async def on_new_token(event) -> None:
        """Broadcast a new incubation-token launch to opted-in chats."""
        try:
            chat_ids = db.list_known_chats()
        except AttributeError:
            # db without v2 helper yet: fall back to chats that track tokens
            seen = set()
            for ids in db.all_tracked_tokens().values():
                seen.update(ids)
            chat_ids = sorted(seen)
        for chat_id in chat_ids:
            try:
                settings = db.get_settings(chat_id)
                if not getattr(settings, "scanner_alerts", False):
                    continue
                lang = settings.language
                text = t(
                    lang,
                    "adv.scanner.new_token",
                    name=event.token_name,
                    symbol=event.token_symbol,
                    address=event.token_address,
                )
                if text == "adv.scanner.new_token":
                    text = (
                        f"🆕 New token launch: {event.token_name} "
                        f"(${event.token_symbol})\n`{event.token_address}`"
                    )
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=_link_buttons(config, event.token_address),
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception("failed to send scanner alert to chat %s", chat_id)

    if NewTokenScanner is not None and getattr(config, "SCANNER_ENABLED", True):
        try:
            scanner = NewTokenScanner(on_new_token)
        except Exception:
            logger.exception("failed to initialise NewTokenScanner")
            scanner = None

    # --- shared deps + command/callback registration ------------------------
    deps = SimpleNamespace(db=db, listener=listener, monitor=monitor, scanner=scanner, config=config)
    app.bot_data["deps"] = deps

    for name, fn in HANDLERS.items():
        app.add_handler(CommandHandler(name, fn))

    if ui_callbacks is not None:
        try:
            ui_callbacks.register_callbacks(app, deps)
        except Exception:
            logger.exception("failed to register UI callbacks")

    if advanced_handlers is not None:
        try:
            advanced_handlers.register(app, deps)
        except Exception:
            logger.exception("failed to register advanced handlers")

    # --- MON/USD price auto-refresh (SPEC-v3 §3) ----------------------------
    async def _mon_price_loop() -> None:
        """Refresh the on-chain MON/USD price into bot_data periodically.

        Manual override: when Config.MON_USD_PRICE > 0 the override is used
        and no on-chain call is made (handled inside get_mon_usd_price).
        """
        try:
            from chain.monprice import get_mon_usd_price
        except ImportError:  # pragma: no cover
            logger.warning("chain.monprice not available; MON/USD auto-price disabled")
            return
        interval = max(30, int(getattr(config, "MON_PRICE_REFRESH_SEC", 300) or 300))
        while True:
            try:
                price = await get_mon_usd_price()
                if price > 0:
                    app.bot_data["mon_usd_price"] = price
                    logger.debug("MON/USD price refreshed: %s", price)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MON/USD price refresh failed")
            await asyncio.sleep(interval)

    # --- lifecycle ---------------------------------------------------------
    async def post_init(application: Application) -> None:
        commands = list(BOT_COMMANDS)
        advanced = getattr(advanced_handlers, "ADVANCED_COMMANDS", None) if advanced_handlers else None
        if advanced:
            commands += [BotCommand(name, desc) for name, desc in advanced]
        await application.bot.set_my_commands(commands)

        # cache the bot username for the "Add me" URL button
        try:
            from bot import keyboards

            me = await application.bot.get_me()
            keyboards.set_bot_username(me.username)
        except Exception:
            logger.debug("could not resolve bot username", exc_info=True)

        # register tokens already stored in the db
        for address in db.all_tracked_tokens():
            try:
                await listener.add_token(address)
            except Exception:
                logger.exception("listener.add_token failed for %s", address)
        application.bot_data["listener_task"] = asyncio.create_task(listener.start())
        logger.info("buy listener started")

        if monitor is not None:
            application.bot_data["monitor_task"] = asyncio.create_task(monitor.start())
            logger.info("price monitor started")
        if scanner is not None:
            application.bot_data["scanner_task"] = asyncio.create_task(scanner.start())
            logger.info("new-token scanner started")

        application.bot_data["monprice_task"] = asyncio.create_task(_mon_price_loop())
        logger.info("MON/USD price refresh started")

    async def _stop_task(name: str, obj, application: Application) -> None:
        try:
            await obj.stop()
        except Exception:
            logger.exception("%s.stop failed", name)
        task = application.bot_data.get(f"{name}_task")
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def post_shutdown(application: Application) -> None:
        await _stop_task("listener", listener, application)
        if monitor is not None:
            await _stop_task("monitor", monitor, application)
        if scanner is not None:
            await _stop_task("scanner", scanner, application)
        monprice_task = application.bot_data.get("monprice_task")
        if monprice_task is not None:
            monprice_task.cancel()
            try:
                await monprice_task
            except asyncio.CancelledError:
                pass

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info("starting Telegram polling")
    app.run_polling(close_loop=False)
