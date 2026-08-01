"""Inline keyboards for buy alert messages.

Buttons: [Tx] -> explorer transaction page, [Chart] -> explorer token page,
[Buy] -> BUY_URL_TEMPLATE with the {token} placeholder replaced by the
token address.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import Config
from core.models import BuyEvent


def buy_alert_keyboard(buy: BuyEvent, config: Config) -> InlineKeyboardMarkup:
    """Build the [Tx][Chart][Buy] inline keyboard for a buy alert."""
    tx_url = f"{config.EXPLORER_URL.rstrip('/')}/tx/{buy.tx_hash}"
    chart_url = f"{config.EXPLORER_URL.rstrip('/')}/token/{buy.token_address}"
    template = config.BUY_URL_TEMPLATE or "https://nad.fun/token/{token}"
    if "{token}" in template:
        buy_url = template.replace("{token}", buy.token_address)
    else:
        buy_url = template
    buttons = [
        InlineKeyboardButton("Tx", url=tx_url),
        InlineKeyboardButton("Chart", url=chart_url),
        InlineKeyboardButton("Buy", url=buy_url),
    ]
    return InlineKeyboardMarkup([buttons])
