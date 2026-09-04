"""
Telegram Notification Engine (Phase C Integration).
Delivers real-time trade signals, position updates, and strategy deployment alerts
to Telegram using Bot API.
"""
import os
import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notifications.telegram")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


def get_telegram_config() -> Dict[str, Any]:
    """Retrieves current telegram credentials from environment."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return {
        "token": token,
        "chat_id": chat_id,
        "is_configured": bool(token and chat_id)
    }


def send_telegram_message(text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
    """Sends a raw formatted message to the configured Telegram chat."""
    cfg = get_telegram_config()
    if not cfg["is_configured"]:
        logger.warning("Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return {"success": False, "error": "Telegram credentials not configured"}

    url = TELEGRAM_API_URL.format(token=cfg["token"], method="sendMessage")
    payload = {
        "chat_id": cfg["chat_id"],
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        res = requests.post(url, json=payload, timeout=8)
        data = res.json()
        if res.status_code == 200 and data.get("ok"):
            logger.info("Telegram message dispatched successfully")
            return {"success": True, "message_id": data.get("result", {}).get("message_id")}
        else:
            logger.error(f"Telegram API error: {data}")
            return {"success": False, "error": data.get("description", "Unknown API error")}
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return {"success": False, "error": str(e)}


def test_telegram_connection() -> Dict[str, Any]:
    """Verifies bot connectivity and sends a test ping message."""
    cfg = get_telegram_config()
    if not cfg["token"]:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN missing in .env"}

    # Test getMe
    try:
        me_url = TELEGRAM_API_URL.format(token=cfg["token"], method="getMe")
        me_res = requests.get(me_url, timeout=5).json()
        if not me_res.get("ok"):
            return {"success": False, "error": me_res.get("description", "Invalid bot token")}
        bot_info = me_res.get("result", {})
    except Exception as e:
        return {"success": False, "error": f"Connection error: {e}"}

    # If chat_id is present, send a ping message
    if cfg["chat_id"]:
        now_wib = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S WIB (UTC+7)")
        test_msg = (
            "🔔 <b>Crypto Signal Bot — Telegram Test</b>\n\n"
            "✅ <b>Connection Status:</b> ACTIVE & VERIFIED\n"
            f"🤖 <b>Bot:</b> @{bot_info.get('username', 'N/A')} ({bot_info.get('first_name', 'Bot')})\n"
            f"💬 <b>Target Chat ID:</b> <code>{cfg['chat_id']}</code>\n"
            f"⏱ <b>Timestamp:</b> {now_wib}\n\n"
            "<i>Your paper trading signals and strategy alerts will appear here in real time.</i>"
        )
        send_res = send_telegram_message(test_msg)
        return {
            "success": send_res.get("success", False),
            "bot": bot_info,
            "chat_id": cfg["chat_id"],
            "error": send_res.get("error")
        }

    return {"success": True, "bot": bot_info, "note": "Bot token verified, but TELEGRAM_CHAT_ID is missing"}


def send_signal_alert(
    symbol: str,
    market: str,
    timeframe: str,
    strategy_name: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    initial_risk: float = 0.0,
    atr: Optional[float] = None
) -> Dict[str, Any]:
    """Formats and dispatches an official trade entry signal alert."""
    dir_emoji = "🟢" if direction.upper() == "LONG" else "🔴"
    side_action = "BUY / LONG" if direction.upper() == "LONG" else "SELL / SHORT"
    
    # Calculate Risk:Reward ratio
    risk = abs(entry_price - stop_loss) if initial_risk <= 0 else initial_risk
    reward = abs(take_profit - entry_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0
    risk_pct = round((risk / entry_price) * 100, 2) if entry_price > 0 else 0.0

    now_wib = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M WIB")

    msg = (
        f"{dir_emoji} <b>NEW TRADING SIGNAL: {symbol} ({direction.upper()})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Action:</b> <code>{side_action}</code>\n"
        f"📊 <b>Market:</b> {market.upper()} | <b>TF:</b> {timeframe}\n"
        f"🧠 <b>Strategy:</b> <code>{strategy_name}</code>\n"
        f"⏱ <b>Signal Time:</b> {now_wib}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Entry Price:</b> <code>${entry_price:,.4f}</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>${stop_loss:,.4f}</code> (-{risk_pct}%)\n"
        f"🎯 <b>Take Profit:</b> <code>${take_profit:,.4f}</code>\n"
        f"⚖️ <b>Risk : Reward:</b> <code>1 : {rr_ratio}</code>\n"
    )
    if atr:
        msg += f"📈 <b>ATR Volatility:</b> <code>${atr:,.4f}</code>\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Automated execution active in Paper Trading Engine.</i>"
    )
    return send_telegram_message(msg)


def send_trade_close_alert(
    symbol: str,
    strategy_name: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    net_return_pct: float,
    r_multiple: float,
    holding_bars: int
) -> Dict[str, Any]:
    """Dispatches a trade closure notification with realized PnL."""
    is_win = r_multiple > 0
    outcome_emoji = "🎉 <b>PROFIT TARGET HIT</b>" if exit_reason == "TP" else (
        "🛡️ <b>TRAILING STOP EXITED</b>" if exit_reason == "TRAIL" else (
            "🛑 <b>STOP LOSS HIT</b>" if exit_reason == "SL" else (
                "⏱️ <b>MAX HOLD TIMEOUT</b>" if exit_reason == "TIMEOUT" else "🔒 <b>POSITION CLOSED</b>"
            )
        )
    )
    result_emoji = "🟢" if is_win else "🔴"
    now_wib = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M WIB")

    msg = (
        f"{result_emoji} {outcome_emoji}: <b>{symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>Strategy:</b> <code>{strategy_name}</code> ({direction})\n"
        f"💵 <b>Entry:</b> ${entry_price:,.4f} ➔ <b>Exit:</b> ${exit_price:,.4f}\n"
        f"📌 <b>Reason:</b> <code>{exit_reason}</code>\n"
        f"⏳ <b>Holding Time:</b> {holding_bars} bars\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Net Return:</b> <code>{net_return_pct:+.2f}%</code>\n"
        f"📊 <b>Realized R:</b> <code>{r_multiple:+.2f}R</code>\n"
        f"⏱ <b>Close Time:</b> {now_wib}\n"
    )
    return send_telegram_message(msg)


def send_deployment_alert(
    symbol: str,
    market: str,
    timeframe: str,
    strategy_name: str,
    allocated_capital: float,
    risk_per_trade_pct: float,
    rank: Optional[int] = None,
    rank_score: Optional[float] = None
) -> Dict[str, Any]:
    """Notifies when a top-performing strategy is deployed to live paper trading."""
    rank_badge = f"🥇 Rank #{rank}" if rank == 1 else (f"🥈 Rank #{rank}" if rank == 2 else (f"🥉 Rank #{rank}" if rank == 3 else "⚡ Strategy"))
    now_wib = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M WIB")

    msg = (
        f"🚀 <b>NEW STRATEGY DEPLOYED TO PAPER TRADING!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Status:</b> {rank_badge} (Score: {rank_score if rank_score is not None else 'N/A'})\n"
        f"🪙 <b>Pair:</b> <code>{symbol}</code> ({market.upper()})\n"
        f"⏱ <b>Timeframe:</b> <code>{timeframe}</code>\n"
        f"🧠 <b>Strategy:</b> <code>{strategy_name}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>Allocated Capital:</b> <code>${allocated_capital:,.2f}</code>\n"
        f"🛡️ <b>Risk per Trade:</b> <code>{risk_per_trade_pct:.1f}%</code>\n"
        f"📲 <b>Telegram Alerts:</b> <code>ACTIVE</code>\n"
        f"⏱ <b>Deployed At:</b> {now_wib}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Signals triggered by this strategy will be broadcast directly here.</i>"
    )
    return send_telegram_message(msg)
