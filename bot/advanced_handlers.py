"""Advanced command handlers (SPEC-v2 §7).

Commands: /pricealert, /alerts, /scanner, /sells, /tokeninfo, /dashboard
plus the ``adv:*`` callback queries (cancel alert, dashboard refresh).

``register(app, deps)`` wires everything into the Application. ``deps``
is an object with attributes: .db (Database), .listener (BuyListener),
.monitor (PriceMonitor), .scanner (NewTokenScanner), .config (Config).

Every user-facing string goes through core.i18n.t() with ``adv.*`` keys.
Admin commands verify group membership via get_chat_member.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from typing import Optional

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from core.i18n import t

logger = logging.getLogger(__name__)

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

DASHBOARD_INTERVAL = 300  # seconds (5 min)
_DASHBOARD_MAX_TOKENS = 10

# (command, description) — merged into set_my_commands by bot/main.py.
ADVANCED_COMMANDS = [
    ("pricealert", "Set a price alert: /pricealert <address> <above|below> <price_MON>"),
    ("alerts", "List and manage your price alerts"),
    ("scanner", "Toggle new incubation-token launch alerts"),
    ("sells", "Toggle sell alerts in this group"),
    ("tokeninfo", "Token security card (liquidity, holders)"),
    ("dashboard", "Post a live auto-updating stats dashboard"),
]


def register(app, deps) -> None:
    """Register the advanced commands and 'adv:*' callbacks on the app."""
    app.bot_data["adv_deps"] = deps
    app.add_handler(CommandHandler("pricealert", cmd_pricealert))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("scanner", cmd_scanner))
    app.add_handler(CommandHandler("sells", cmd_sells))
    app.add_handler(CommandHandler("tokeninfo", cmd_tokeninfo))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(
        CallbackQueryHandler(cb_cancel_alert, pattern=r"^adv:cancel_alert:\d+$")
    )
    app.add_handler(CallbackQueryHandler(cb_dash_refresh, pattern=r"^adv:dash_refresh$"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _deps(context: ContextTypes.DEFAULT_TYPE):
    """The deps object passed to register(); falls back to bot_data pieces."""
    deps = context.application.bot_data.get("adv_deps")
    if deps is not None:
        return deps
    bot_data = context.application.bot_data
    return SimpleNamespace(
        db=bot_data.get("db"),
        listener=bot_data.get("listener"),
        monitor=bot_data.get("monitor"),
        scanner=bot_data.get("scanner"),
        config=bot_data.get("config"),
    )


def _lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    deps = _deps(context)
    try:
        return deps.db.get_settings(update.effective_chat.id).language
    except Exception:  # noqa: BLE001
        return "en"


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True in private chats; in groups only for administrators/owner."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    if chat.type == Chat.PRIVATE:
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        logger.exception("admin check failed for user %s", user.id)
        return False


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await _is_admin(update, context):
        return True
    await update.message.reply_text(t(_lang(update, context), "error.admin_only"))
    return False


def _short_addr(address: str) -> str:
    if address and len(address) > 13:
        return f"{address[:6]}…{address[-4:]}"
    return address or "?"


async def _token_label(address: str) -> str:
    """Best-effort '$SYMBOL' label; falls back to the shortened address."""
    try:
        from chain.price import get_token_info

        info = await get_token_info(address)
        if info.symbol:
            return f"${info.symbol}"
    except Exception:  # noqa: BLE001
        pass
    return _short_addr(address)


def _resolve_token_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Optional [address] argument; falls back to the group's first token."""
    deps = _deps(context)
    if context.args:
        address = context.args[0].strip()
        if not EVM_ADDRESS_RE.match(address):
            return "__invalid__"
        return address
    tokens = deps.db.list_tokens(update.effective_chat.id)
    return tokens[0] if tokens else None


def _cancel_keyboard(alert_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(lang, "adv.btn_cancel"),
                    callback_data=f"adv:cancel_alert:{alert_id}",
                )
            ]
        ]
    )


# ---------------------------------------------------------------------------
# /pricealert
# ---------------------------------------------------------------------------


async def cmd_pricealert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = _deps(context)
    lang = _lang(update, context)
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    usage = "/pricealert <address> <above|below> <price_MON>"

    args = list(context.args or [])
    if len(args) != 3 or not EVM_ADDRESS_RE.match(args[0].strip()):
        await update.message.reply_text(t(lang, "adv.invalid_args", usage=usage))
        return
    address = args[0].strip()
    direction = args[1].strip().lower()
    if direction not in ("above", "below"):
        await update.message.reply_text(t(lang, "adv.invalid_args", usage=usage))
        return
    try:
        target = float(args[2])
        if target <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(t(lang, "adv.invalid_args", usage=usage))
        return

    alert_id = deps.db.add_price_alert(chat_id, address, direction, target, user_id)
    symbol = await _token_label(address)
    await update.message.reply_text(
        t(
            lang,
            "adv.pricealert_set",
            id=alert_id,
            symbol=symbol,
            direction=direction,
            target=f"{target:g}",
        ),
        reply_markup=_cancel_keyboard(alert_id, lang),
    )


# ---------------------------------------------------------------------------
# /alerts + adv:cancel_alert:<id>
# ---------------------------------------------------------------------------


async def _alerts_text_and_keyboard(db, chat_id: int, lang: str):
    alerts = db.list_price_alerts(chat_id, active_only=True)
    if not alerts:
        return t(lang, "adv.alerts_empty"), None
    lines = [t(lang, "adv.alerts_title")]
    buttons = []
    for alert in alerts:
        symbol = await _token_label(alert.token_address)
        lines.append(
            t(
                lang,
                "adv.alerts_row",
                id=alert.id,
                symbol=symbol,
                direction=alert.direction,
                target=f"{alert.target_mon:g}",
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{t(lang, 'adv.btn_cancel')} #{alert.id}",
                    callback_data=f"adv:cancel_alert:{alert.id}",
                )
            ]
        )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = _deps(context)
    lang = _lang(update, context)
    chat_id = update.effective_chat.id
    text, keyboard = await _alerts_text_and_keyboard(deps.db, chat_id, lang)
    await update.message.reply_text(text, reply_markup=keyboard)


async def cb_cancel_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    deps = _deps(context)
    chat_id = query.message.chat_id
    lang = deps.db.get_settings(chat_id).language

    try:
        alert_id = int(str(query.data).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return

    if deps.db.deactivate_price_alert(alert_id, chat_id):
        await query.answer(t(lang, "adv.alert_cancelled", id=alert_id))
    else:
        await query.answer(t(lang, "adv.alert_cancelled", id=alert_id))

    # Refresh the message with the remaining alerts.
    try:
        text, keyboard = await _alerts_text_and_keyboard(deps.db, chat_id, lang)
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit after cancel failed: %s", exc)
    except Exception:  # noqa: BLE001
        logger.exception("failed to refresh alerts message")


# ---------------------------------------------------------------------------
# /scanner and /sells (admin toggles)
# ---------------------------------------------------------------------------


async def cmd_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    deps = _deps(context)
    lang = _lang(update, context)
    chat_id = update.effective_chat.id
    settings = deps.db.get_settings(chat_id)
    settings.scanner_alerts = not settings.scanner_alerts
    deps.db.save_settings(settings)
    key = "adv.scanner_on" if settings.scanner_alerts else "adv.scanner_off"
    await update.message.reply_text(t(lang, key))


async def cmd_sells(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    deps = _deps(context)
    lang = _lang(update, context)
    chat_id = update.effective_chat.id
    settings = deps.db.get_settings(chat_id)
    settings.sell_alerts = not settings.sell_alerts
    deps.db.save_settings(settings)
    key = "adv.sells_on" if settings.sell_alerts else "adv.sells_off"
    await update.message.reply_text(t(lang, key))


# ---------------------------------------------------------------------------
# /tokeninfo
# ---------------------------------------------------------------------------


async def _best_liquidity_mon(address: str) -> float:
    """Best WMON-side liquidity (MON) across curve/pairs. 0.0 if unknown."""
    try:
        from web3 import Web3

        from chain.abis import CURVE_ABI, UNIV2_PAIR_ABI, WMON_ADDRESS
        from chain.client import get_w3
        from chain.incubation import _curve_reserves, _find_curve_address
        from chain.price import _recent_transfer_senders, _safe_call

        w3 = get_w3()
        token_address = Web3.to_checksum_address(address)
        best = 0.0

        curve_address = await _find_curve_address(w3, token_address)
        if curve_address is not None:
            reserves = await _curve_reserves(w3, curve_address)
            if reserves is not None:
                best = max(best, float(reserves[0]))

        for candidate in await _recent_transfer_senders(w3, token_address):
            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(candidate),
                    abi=UNIV2_PAIR_ABI + CURVE_ABI,
                )
                reserves = await _safe_call(contract, "getReserves")
                if reserves is None:
                    continue
                token0 = await _safe_call(contract, "token0")
                token1 = await _safe_call(contract, "token1")
                wmon = WMON_ADDRESS.lower()
                if token0 and str(token0).lower() == wmon:
                    best = max(best, float(reserves[0]) / 1e18)
                elif token1 and str(token1).lower() == wmon:
                    best = max(best, float(reserves[1]) / 1e18)
            except Exception:  # noqa: BLE001
                continue
        return best
    except Exception as exc:  # noqa: BLE001 - best effort only
        logger.debug("liquidity probe failed for %s: %s", address, exc)
        return 0.0


async def _top_holders_pct(address: str, decimals: int, total_supply: float) -> Optional[float]:
    """Top-10 holders % of supply (recent transfer participants). None if n/a."""
    try:
        if total_supply <= 0:
            return None
        from web3 import Web3

        from chain.abis import ERC20_ABI, TRANSFER_TOPIC
        from chain.client import get_w3
        from chain.detector import _get, _topic_address
        from chain.price import _safe_call

        w3 = get_w3()
        token_address = Web3.to_checksum_address(address)
        latest = int(await w3.eth.get_block_number())
        logs = await w3.eth.get_logs(
            {
                "fromBlock": max(0, latest - 20_000),
                "toBlock": latest,
                "address": token_address,
                "topics": [TRANSFER_TOPIC],
            }
        )
        addrs: list[str] = []
        for log in logs:
            topics = _get(log, "topics") or []
            for topic in topics[1:3]:
                addr = _topic_address(topic)
                if addr and addr not in addrs:
                    addrs.append(addr)
        addrs = addrs[-10:]
        if not addrs:
            return None

        token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        held_raw = 0.0
        for addr in addrs:
            try:
                balance = await _safe_call(
                    token, "balanceOf", Web3.to_checksum_address(addr)
                )
                if balance:
                    held_raw += float(balance)
            except Exception:  # noqa: BLE001
                continue
        held = held_raw / (10 ** (decimals or 18))
        return max(0.0, min(100.0, held / total_supply * 100.0))
    except Exception as exc:  # noqa: BLE001 - best effort only
        logger.debug("holders probe failed for %s: %s", address, exc)
        return None


async def cmd_tokeninfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = _deps(context)
    lang = _lang(update, context)
    address = _resolve_token_arg(update, context)
    if address == "__invalid__":
        await update.message.reply_text(t(lang, "error.invalid_address", address=context.args[0]))
        return
    if address is None:
        await update.message.reply_text(t(lang, "token.list_empty"))
        return

    try:
        from chain import incubation as chain_incubation
        from chain import price as chain_price

        info = await chain_price.get_token_info(address)
        price_mon = await chain_price.get_price_mon(address)
        mcap_mon = await chain_price.get_mcap_mon(address)
        liquidity_mon = await _best_liquidity_mon(address)
        holders_pct = await _top_holders_pct(info.address, info.decimals, info.total_supply)
        curve = await chain_incubation.get_curve_info(address)
    except Exception:
        logger.exception("tokeninfo failed for %s", address)
        await update.message.reply_text(t(lang, "error.generic"))
        return

    config = deps.config
    mon_usd = float(getattr(config, "MON_USD_PRICE", 0.0) or 0.0)

    def _usd(mon_value: float) -> str:
        return f" (${mon_value * mon_usd:,.2f})" if mon_usd > 0 and mon_value > 0 else ""

    if curve is not None and curve.is_incubating:
        pct = f"{curve.progress_pct:.0f}%" if curve.progress_pct is not None else "?%"
        incubation = t(lang, "adv.tokeninfo_incubating", pct=pct)
    elif curve is not None and curve.graduated:
        incubation = t(lang, "adv.tokeninfo_graduated")
    else:
        incubation = t(lang, "adv.tokeninfo_not_incubating")

    name = info.name or _short_addr(address)
    symbol = info.symbol or "?"
    lines = [
        t(lang, "adv.tokeninfo_title", name=name, symbol=symbol),
        t(
            lang,
            "adv.tokeninfo_lines",
            supply=f"{info.total_supply:,.0f}" if info.total_supply > 0 else "?",
            price=f"{price_mon:.10f}".rstrip("0").rstrip(".") or "0",
            mcap=f"{mcap_mon:,.2f} MON{_usd(mcap_mon)}" if mcap_mon > 0 else "?",
            liquidity=f"{liquidity_mon:,.2f} MON{_usd(liquidity_mon)}" if liquidity_mon > 0 else "?",
            holders=f"{holders_pct:.1f}%" if holders_pct is not None else "?",
            incubation=incubation,
        ),
        f"`{info.address}`",
    ]
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True
    )


# ---------------------------------------------------------------------------
# /dashboard + adv:dash_refresh
# ---------------------------------------------------------------------------


async def _build_dashboard_text(deps, chat_id: int, lang: str) -> str:
    """Dashboard body: 24h stats + prices of tracked tokens."""
    from chain import price as chain_price

    db = deps.db
    stats = db.get_stats_24h(chat_id)
    tokens = db.list_tokens(chat_id)

    token_lines: list[str] = []
    for address in tokens[:_DASHBOARD_MAX_TOKENS]:
        symbol = _short_addr(address)
        price_mon = 0.0
        try:
            info = await chain_price.get_token_info(address)
            if info.symbol:
                symbol = f"${info.symbol}"
        except Exception:  # noqa: BLE001
            pass
        try:
            price_mon = await chain_price.get_price_mon(address)
        except Exception:  # noqa: BLE001
            pass
        price = f"{price_mon:.10f}".rstrip("0").rstrip(".") or "0"
        token_lines.append(t(lang, "adv.dashboard_token_row", symbol=symbol, price=price))
    if len(tokens) > _DASHBOARD_MAX_TOKENS:
        token_lines.append(f"… +{len(tokens) - _DASHBOARD_MAX_TOKENS}")

    tokens_text = "\n".join(token_lines) if token_lines else t(lang, "adv.dashboard_no_tokens")
    return "\n".join(
        [
            t(lang, "adv.dashboard_title"),
            t(
                lang,
                "adv.dashboard_rows",
                count=stats["count"],
                volume_mon=f"{stats['volume_mon']:,.2f}",
                volume_usd=f"${stats['volume_usd']:,.2f}",
                tokens=tokens_text,
            ),
        ]
    )


def _dashboard_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(lang, "adv.btn_refresh"), callback_data="adv:dash_refresh")]]
    )


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    deps = _deps(context)
    lang = _lang(update, context)
    chat_id = update.effective_chat.id

    text = await _build_dashboard_text(deps, chat_id, lang)
    message = await update.message.reply_text(
        text, reply_markup=_dashboard_keyboard(lang), disable_web_page_preview=True
    )

    job_queue = context.application.job_queue
    if job_queue is None:
        logger.warning("JobQueue unavailable; dashboard will not auto-update")
        return
    job_queue.run_repeating(
        _dashboard_job,
        interval=DASHBOARD_INTERVAL,
        first=DASHBOARD_INTERVAL,
        data={"chat_id": chat_id, "message_id": message.message_id},
        name=f"adv_dash_{chat_id}_{message.message_id}",
    )


async def _dashboard_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: edit the dashboard message; stop if it was deleted."""
    job = context.job
    data = job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if chat_id is None or message_id is None:
        job.schedule_removal()
        return

    deps = context.application.bot_data.get("adv_deps")
    if deps is None:
        job.schedule_removal()
        return
    try:
        lang = deps.db.get_settings(chat_id).language
    except Exception:  # noqa: BLE001
        lang = "en"

    try:
        text = await _build_dashboard_text(deps, chat_id, lang)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=_dashboard_keyboard(lang),
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        # Message deleted (or uneditable): stop the job.
        logger.info("dashboard job stopped for chat %s: %s", chat_id, exc)
        job.schedule_removal()
    except Exception:  # noqa: BLE001 - keep the job alive on transient errors
        logger.exception("dashboard refresh failed for chat %s", chat_id)


async def cb_dash_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    deps = _deps(context)
    chat_id = query.message.chat_id
    try:
        lang = deps.db.get_settings(chat_id).language
    except Exception:  # noqa: BLE001
        lang = "en"

    try:
        text = await _build_dashboard_text(deps, chat_id, lang)
        await query.edit_message_text(
            text,
            reply_markup=_dashboard_keyboard(lang),
            disable_web_page_preview=True,
        )
        await query.answer()
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            await query.answer()
        else:
            logger.info("dashboard refresh failed: %s", exc)
            await query.answer()
    except Exception:  # noqa: BLE001
        logger.exception("dashboard refresh callback failed")
        await query.answer()
