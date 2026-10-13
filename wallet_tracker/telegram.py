"""Telegram delivery for followed-wallet swap alerts.

Uses the same environment settings as RetailBot and Smart Money Screener:
TELEGRAM_TOKEN (RetailBot alias) / TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
"""
import html
import logging
import os
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("wallet_tracker.telegram")


class WalletTelegramNotifier:
    def __init__(self, token=None, chat_id=None, session=None):
        self.token = token if token is not None else (
            os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        )
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage" if self.token else ""
        self.session = session or requests.Session()
        self.session.mount(
            "https://",
            HTTPAdapter(max_retries=Retry(
                total=3, backoff_factor=0.3,
                status_forcelist=[429, 500, 502, 503, 504],
            )),
        )
        self._last_send = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    @staticmethod
    def format_message(alert):
        side = str(alert.get("side", "")).lower()
        is_buy = side == "buy"
        emoji = "🟢" if is_buy else "🔴"
        verb = "BUY" if is_buy else "SELL"
        chain = html.escape(str(alert.get("chain_label") or alert.get("chain") or "unknown"))
        pair = html.escape(str(alert.get("pair") or "unknown pair"))
        wallet = html.escape(str(alert.get("wallet") or ""))
        label = html.escape(str(alert.get("label") or "Followed wallet"))
        usd = float(alert.get("usd_value") or 0)
        amount = float(alert.get("token_amount") or 0)
        price = float(alert.get("price_usd") or 0)
        tx = html.escape(str(alert.get("tx_url") or ""))
        wallet_url = html.escape(str(alert.get("explorer_url") or ""))
        when = html.escape(str(alert.get("block_time") or ""))
        tx_line = f'<a href="{tx}">View transaction</a>' if tx.startswith("https://") else ""
        wallet_line = f'<a href="{wallet_url}">View wallet</a>' if wallet_url.startswith("https://") else ""
        links = " · ".join(part for part in (tx_line, wallet_line) if part)
        lines = [
            f"{emoji} <b>Followed Wallet {verb}</b> · {chain}",
            f"<b>Pair:</b> {pair}",
            f"<b>Value:</b> ${usd:,.2f}",
            f"<b>Token amount:</b> {amount:,.6f}",
            f"<b>Price:</b> ${price:,.8f}",
            f"<b>Wallet:</b> <code>{wallet}</code>",
            f"<b>Label:</b> {label}",
        ]
        if when:
            lines.append(f"<b>Trade time (UTC):</b> {when}")
        if links:
            lines.append(links)
        return "\n".join(lines)

    def send(self, alert):
        if not self.configured:
            logger.warning("Telegram alert skipped: bot token or chat ID is missing")
            return False
        with self._lock:
            elapsed = time.monotonic() - self._last_send
            if elapsed < 0.8:
                time.sleep(0.8 - elapsed)
            try:
                response = self.session.post(
                    self.url,
                    json={
                        "chat_id": self.chat_id,
                        "text": self.format_message(alert),
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("ok") is not True:
                    raise RuntimeError("Telegram API response did not report ok=true")
                self._last_send = time.monotonic()
                return True
            except Exception as exc:
                # The request URL contains the bot token. Never log the exception
                # message or traceback because requests embeds that URL in errors.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                logger.warning(
                    "Telegram wallet alert delivery failed (error_type=%s, http_status=%s)",
                    type(exc).__name__, status if status is not None else "unknown",
                )
                return False
