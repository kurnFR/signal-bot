"""
Telegram Notification Engine (Phase C Integration).
Delivers real-time trade signals, position updates, and strategy deployment alerts
to Telegram using Bot API.
"""
import os
import base64
import hashlib
import logging
import requests
from html import escape
from typing import Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notifications.telegram")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        from web.auth import SECRET_KEY
        key_src = os.getenv("APP_SECRET_KEY") or SECRET_KEY
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(key_src.encode("utf-8")).digest())
        return Fernet(derived_key)
    except Exception as exc:
        logger.warning("Could not initialize Fernet cipher for Telegram settings: %s", exc)
        return None


def _get_db_telegram_settings() -> Optional[Dict[str, Any]]:
    try:
        from db.db import get_pool, _owned_cursor
        pool = get_pool()
        conn = pool.get_connection()
        with _owned_cursor(conn, dictionary=True) as cur:
            cur.execute(
                "SELECT encrypted_bot_token, chat_id, delivery_authorized "
                "FROM app_telegram_settings ORDER BY id DESC LIMIT 1"
            )
            return cur.fetchone()
    except Exception:
        return None


def set_db_telegram_settings(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    delivery_authorized: Optional[bool] = None,
) -> Dict[str, Any]:
    """Store or update encrypted Telegram settings in database."""
    from db.db import get_pool, _owned_cursor
    current = _get_db_telegram_settings() or {}
    
    enc_token = current.get("encrypted_bot_token")
    if bot_token is not None and bot_token.strip():
        fernet = _get_fernet()
        if fernet:
            enc_token = fernet.encrypt(bot_token.strip().encode("utf-8")).decode("utf-8")
        else:
            enc_token = bot_token.strip()

    new_chat_id = chat_id.strip() if chat_id is not None else current.get("chat_id")
    new_auth = int(delivery_authorized) if delivery_authorized is not None else int(current.get("delivery_authorized", 0))

    pool = get_pool()
    conn = pool.get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO app_telegram_settings (id, encrypted_bot_token, chat_id, delivery_authorized)
            VALUES (1, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                encrypted_bot_token = VALUES(encrypted_bot_token),
                chat_id = VALUES(chat_id),
                delivery_authorized = VALUES(delivery_authorized)
            """,
            (enc_token, new_chat_id, new_auth),
        )
        conn.commit()

    return get_telegram_config()


def _safe_http_status(status: Any) -> str:
    """Validates that status is an exact integer in the range 100-599, or returns 'unknown'."""
    if type(status) is int and 100 <= status <= 599:
        return str(status)
    return "unknown"


def is_telegram_delivery_authorized() -> bool:
    """Returns True only when Telegram delivery is authorized on BOTH layers.

    The environment flag ``TELEGRAM_DELIVERY_ENABLED`` (with deprecated legacy
    alias ``RETAILBOT2_ALLOW_TELEGRAM_DELIVERY``; either set to ``"1"`` opens the
    ceiling) is a hard ceiling: when neither is ``"1"``, delivery is suppressed
    no matter what the database setting says. When the ceiling is open, the
    database setting (Settings UI, ``app_telegram_settings.delivery_authorized``)
    decides if a row exists; with no DB row the env flag alone decides. Every
    delivery consumer (notifier, wallet tracker, report scheduler, outbox
    worker, Screener) must gate on this helper so the layers can never diverge.
    """
    env_authorized = (
        os.getenv("TELEGRAM_DELIVERY_ENABLED", "").strip() == "1"
        or os.getenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "").strip() == "1"
    )
    if not env_authorized:
        return False
    db_settings = _get_db_telegram_settings()
    if db_settings and db_settings.get("delivery_authorized") is not None:
        return bool(db_settings["delivery_authorized"])
    return True


def get_telegram_config() -> Dict[str, Any]:
    """Retrieves current telegram credentials from DB, falling back to environment."""
    db_settings = _get_db_telegram_settings()
    if db_settings:
        raw_enc = db_settings.get("encrypted_bot_token")
        chat_id = (db_settings.get("chat_id") or "").strip()
        token = ""
        if raw_enc:
            fernet = _get_fernet()
            if fernet:
                try:
                    token = fernet.decrypt(raw_enc.encode("utf-8")).decode("utf-8").strip()
                except Exception:
                    token = raw_enc.strip()
            else:
                token = raw_enc.strip()
        if token or chat_id:
            return {
                "token": token,
                "chat_id": chat_id,
                "is_configured": bool(token and chat_id),
                "delivery_authorized": is_telegram_delivery_authorized(),
                "source": "database",
            }

    # Fallback to environment variables
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_TARGET_CHAT_ID") or "").strip()
    return {
        "token": token,
        "chat_id": chat_id,
        "is_configured": bool(token and chat_id),
        "delivery_authorized": is_telegram_delivery_authorized(),
        "source": "environment",
    }


def send_telegram_message(text: str, parse_mode: str = "HTML", max_retries: int = 3) -> Dict[str, Any]:
    """Sends a raw formatted message to the configured Telegram chat with retry logic and exponential backoff."""
    if not is_telegram_delivery_authorized():
        clean_preview = text.replace("\n", " | ")
        logger.info(
            "ℹ️ Telegram message suppressed (delivery unauthorized; "
            "set TELEGRAM_DELIVERY_ENABLED=1): %s",
            clean_preview[:300],
        )
        return {
            "success": False,
            "error": "Telegram delivery is disabled: global authorization missing (set TELEGRAM_DELIVERY_ENABLED=1)",
            "suppressed": True,
        }

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

    last_error = "Unknown error"
    import time
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.post(url, json=payload, timeout=8)
            status_code = getattr(res, "status_code", None)
            safe_status = _safe_http_status(status_code)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                last_error = f"Telegram API error (HTTP {safe_status})"
                logger.warning("Telegram API response attempt %d/%d: %s", attempt, max_retries, last_error)
                # An invalid status is not a trustworthy retry signal. The
                # request may have been accepted, so fail closed immediately.
                break
            try:
                data = res.json()
            except Exception as e:
                last_error = "Invalid Telegram API response"
                logger.warning("Telegram response attempt %d/%d was malformed (%s)", attempt, max_retries, type(e).__name__)
                # A malformed response leaves delivery outcome ambiguous.
                break

            if type(status_code) is int and status_code == 200 and isinstance(data, dict) and data.get("ok") is True:
                logger.info("Telegram message dispatched successfully (attempt %d/%d)", attempt, max_retries)
                return {"success": True, "message_id": data.get("result", {}).get("message_id")}
            else:
                last_error = f"Telegram API error (HTTP {safe_status})"
                logger.warning("Telegram API response attempt %d/%d: %s", attempt, max_retries, last_error)
                # Retry rate limits and server-side failures; permanent client
                # errors (invalid token/chat ID, malformed request, etc.) will
                # not succeed by repeating the same request.
                if type(status_code) is int and status_code < 500 and status_code != 429:
                    break
        except (requests.RequestException, ValueError) as e:
            # Requests exceptions can embed the token-bearing Bot API URL.
            # Keep diagnostics useful without returning or logging credential material.
            last_error = f"{type(e).__name__} while contacting Telegram API"
            logger.warning("Telegram send attempt %d/%d failed (%s)", attempt, max_retries, type(e).__name__)
            # The request may have reached Telegram even if the response was
            # lost or malformed. Do not retry an ambiguous outcome: duplicates
            # are safer to avoid than to assume the message was not accepted.
            break

        if attempt < max_retries:
            time.sleep(1.0 * attempt)

    logger.error("All %d Telegram delivery attempts failed. Last error: %s", max_retries, last_error)
    return {"success": False, "error": last_error}


def test_telegram_connection(custom_message: Optional[str] = None) -> Dict[str, Any]:
    """Verifies bot connectivity and sends at most one test ping message."""
    if custom_message is not None and len(str(custom_message)) > 1000:
        return {"success": False, "error": "custom_message exceeds maximum length of 1000 characters"}

    if not is_telegram_delivery_authorized():
        logger.info(
            "Telegram connectivity test suppressed: global delivery authorization missing "
            "(set TELEGRAM_DELIVERY_ENABLED=1)"
        )
        return {
            "success": False,
            "error": "Telegram delivery is disabled (authorization missing; set TELEGRAM_DELIVERY_ENABLED=1 to enable)",
            "delivery_authorized": False,
        }

    cfg = get_telegram_config()
    if not cfg["token"]:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN missing in .env"}

    # Test getMe
    try:
        me_url = TELEGRAM_API_URL.format(token=cfg["token"], method="getMe")
        res = requests.get(me_url, timeout=5)
        status_code = getattr(res, "status_code", None)
        safe_status = _safe_http_status(status_code)
        try:
            me_res = res.json()
        except Exception:
            me_res = {}

        is_success = (
            type(status_code) is int
            and status_code == 200
            and isinstance(me_res, dict)
            and me_res.get("ok") is True
        )
        if not is_success:
            logger.warning("Telegram getMe check failed (HTTP %s)", safe_status)
            return {"success": False, "error": f"Telegram API error (HTTP {safe_status})"}
        bot_info = me_res.get("result", {})
    except Exception as e:
        # Network exceptions may include the token-bearing request URL.
        logger.warning("Telegram connectivity check failed (%s)", type(e).__name__)
        return {"success": False, "error": "Connection error while contacting Telegram API"}

    # If chat_id is present, send at most one ping message
    if cfg["chat_id"]:
        now_wib = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S WIB (UTC+7)")
        if custom_message and str(custom_message).strip():
            safe_text = escape(str(custom_message).strip()[:1000])
            test_msg = (
                "🔔 <b>Crypto Signal Bot — Telegram Test</b>\n\n"
                f"📝 <b>Message:</b> {safe_text}\n\n"
                "✅ <b>Connection Status:</b> ACTIVE & VERIFIED\n"
                f"🤖 <b>Bot:</b> @{bot_info.get('username', 'N/A')} ({bot_info.get('first_name', 'Bot')})\n"
                f"💬 <b>Target Chat ID:</b> <code>{cfg['chat_id']}</code>\n"
                f"⏱ <b>Timestamp:</b> {now_wib}"
            )
        else:
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
    atr: Optional[float] = None,
    ml_probability: Optional[float] = None,
    ml_threshold: Optional[float] = None,
    ml_model_id: Optional[str] = None,
    ml_generator_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Formats and dispatches an official trade entry signal alert with optional ML gating metadata."""
    safe_symbol = escape(str(symbol))
    safe_market = escape(str(market).upper())
    safe_timeframe = escape(str(timeframe))
    safe_strategy = escape(str(strategy_name))
    safe_direction = escape(str(direction).upper())
    dir_emoji = "🟢" if direction.upper() == "LONG" else "🔴"
    side_action = "BUY / LONG" if direction.upper() == "LONG" else "SELL / SHORT"
    
    # Calculate Risk:Reward ratio
    risk = abs(entry_price - stop_loss) if initial_risk <= 0 else initial_risk
    reward = abs(take_profit - entry_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0
    risk_pct = round((risk / entry_price) * 100, 2) if entry_price > 0 else 0.0

    now_wib = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M WIB")

    msg = (
        f"{dir_emoji} <b>NEW TRADING SIGNAL: {safe_symbol} ({safe_direction})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Action:</b> <code>{side_action}</code>\n"
        f"📊 <b>Market:</b> {safe_market} | <b>TF:</b> {safe_timeframe}\n"
        f"🧠 <b>Strategy:</b> <code>{safe_strategy}</code>\n"
        f"⏱ <b>Signal Time:</b> {now_wib}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Entry Price:</b> <code>${entry_price:,.4f}</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>${stop_loss:,.4f}</code> (-{risk_pct}%)\n"
        f"🎯 <b>Take Profit:</b> <code>${take_profit:,.4f}</code>\n"
        f"⚖️ <b>Risk : Reward:</b> <code>1 : {rr_ratio}</code>\n"
    )
    if atr:
        msg += f"📈 <b>ATR Volatility:</b> <code>${atr:,.4f}</code>\n"

    if ml_probability is not None:
        pass_badge = "✅ PASSED" if (ml_threshold is None or ml_probability >= ml_threshold) else "⚠️ GATED"
        thresh_info = f" (Threshold: {ml_threshold * 100:.1f}%)" if ml_threshold is not None else ""
        msg += f"🤖 <b>AI/ML Confidence:</b> <code>{ml_probability * 100:.1f}%</code>{thresh_info} {pass_badge}\n"
    if ml_model_id:
        msg += f"🧬 <b>Filter Model:</b> <code>{escape(str(ml_model_id))}</code>\n"
    if ml_generator_model_id:
        msg += f"🧠 <b>Signal Generator Model:</b> <code>{escape(str(ml_generator_model_id))}</code>\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Automated execution active in Paper Trading Engine.</i>"
    )
    return send_telegram_message(msg)


def send_news_signal_alert(
    symbol: str,
    market: str,
    timeframe: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    confidence: int,
    reasoning: Optional[str] = None,
    invalidation_condition: Optional[str] = None,
    trigger_headline: Optional[str] = None,
    time_horizon: Optional[str] = None,
) -> Dict[str, Any]:
    """News AI Overlay strategy (Phase 3) entry alert -- same chat/bot as
    send_signal_alert(), deliberately distinct visual template (📰 header,
    'Paper trade' footer) so it's never mistaken for a technical-strategy
    signal. See docs/NEWS_AI.md §3."""
    dir_emoji = "🟢" if direction.upper() == "LONG" else "🔴"
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 0.0
    risk_pct = round((risk / entry_price) * 100, 2) if entry_price > 0 else 0.0
    now_wib = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M WIB")

    safe_symbol = escape(str(symbol))
    safe_market = escape(str(market).upper())
    safe_timeframe = escape(str(timeframe))
    safe_headline = escape(str(trigger_headline)) if trigger_headline else None
    safe_reasoning = escape(str(reasoning)) if reasoning else None
    safe_invalidation = escape(str(invalidation_condition)) if invalidation_condition else None
    safe_horizon = escape(str(time_horizon)) if time_horizon else None

    msg = (
        f"📰 {dir_emoji} <b>NEWS-DRIVEN SIGNAL: {safe_symbol} ({direction.upper()})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Market:</b> {safe_market} | <b>TF:</b> {safe_timeframe}\n"
    )
    if trigger_headline:
        msg += f"🗞 <b>Trigger:</b> {safe_headline}\n"
    msg += (
        f"🤖 <b>AI Confidence:</b> <code>{confidence}%</code>"
        + (f" | <b>Horizon:</b> {safe_horizon}" if safe_horizon else "")
        + "\n"
        f"⏱ <b>Signal Time:</b> {now_wib}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Entry:</b> <code>${entry_price:,.4f}</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>${stop_loss:,.4f}</code> (-{risk_pct}%)\n"
        f"🎯 <b>Take Profit:</b> <code>${take_profit:,.4f}</code>\n"
        f"⚖️ <b>Risk : Reward:</b> <code>1 : {rr_ratio}</code>\n"
    )
    if reasoning:
        msg += f"💭 <b>Reasoning:</b> {safe_reasoning}\n"
    if invalidation_condition:
        msg += f"❌ <b>Invalidation:</b> {safe_invalidation}\n"
    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Paper trade only -- News AI Overlay (Phase 3). No live execution.</i>"
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
    holding_bars: int,
    ml_generator_model_id: Optional[str] = None,
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
    now_wib = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M WIB")

    safe_symbol = escape(str(symbol))
    safe_strategy = escape(str(strategy_name))
    safe_direction = escape(str(direction))
    safe_exit_reason = escape(str(exit_reason))

    msg = (
        f"{result_emoji} {outcome_emoji}: <b>{safe_symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>Strategy:</b> <code>{safe_strategy}</code> ({safe_direction})\n"
    )
    if ml_generator_model_id:
        msg += f"🧬 <b>Signal Generator Model:</b> <code>{escape(str(ml_generator_model_id))}</code>\n"
    msg += (
        f"💵 <b>Entry:</b> ${entry_price:,.4f} ➔ <b>Exit:</b> ${exit_price:,.4f}\n"
        f"📌 <b>Reason:</b> <code>{safe_exit_reason}</code>\n"
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
    rank_score: Optional[float] = None,
    max_retries: int = 1,
) -> Dict[str, Any]:
    """Notifies when a top-performing strategy is deployed to live paper trading.

    Deployment notifications default to a single delivery attempt (max_retries=1)
    to prevent duplicate alerts during interactive operator deployments.
    """
    rank_badge = f"🥇 Rank #{rank}" if rank == 1 else (f"🥈 Rank #{rank}" if rank == 2 else (f"🥉 Rank #{rank}" if rank == 3 else "⚡ Strategy"))
    now_wib = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M WIB")

    safe_symbol = escape(str(symbol))
    safe_market = escape(str(market).upper())
    safe_timeframe = escape(str(timeframe))
    safe_strategy = escape(str(strategy_name))

    msg = (
        f"🚀 <b>NEW STRATEGY DEPLOYED TO PAPER TRADING!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Status:</b> {rank_badge} (Score: {rank_score if rank_score is not None else 'N/A'})\n"
        f"🪙 <b>Pair:</b> <code>{safe_symbol}</code> ({safe_market})\n"
        f"⏱ <b>Timeframe:</b> <code>{safe_timeframe}</code>\n"
        f"🧠 <b>Strategy:</b> <code>{safe_strategy}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>Allocated Capital:</b> <code>${allocated_capital:,.2f}</code>\n"
        f"🛡️ <b>Risk per Trade:</b> <code>{risk_per_trade_pct:.1f}%</code>\n"
        f"📲 <b>Telegram Alerts:</b> <code>ACTIVE</code>\n"
        f"⏱ <b>Deployed At:</b> {now_wib}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Signals triggered by this strategy will be broadcast directly here.</i>"
    )
    return send_telegram_message(msg, max_retries=max_retries)
