"""
████████████████████████████████████████████████████████████████████
██  RETAIL DEATH TRAP BOT v2.1 - PRODUCTION FIXES               ██
██  Fix: Mixed signals | Fix: All 7 strategies | Fix: Inverse   ██
██  Fix: Confluence count | Fix: Separate equity per mode       ██
████████████████████████████████████████████████████████████████████

FIXES IN v2.1:
  [F1] SR_TRAP threshold tightened — was triggering on everything
  [F2] DBL_PAT tolerance tightened — same issue
  [F3] RSI strategy fixed — uses range not exact cross
  [F4] BB strategy fixed — percentage proximity not exact touch
  [F5] MACD divergence fixed — robust swing detection
  [F6] Spike fade fixed — correct candle indexing
  [F7] MA crossover fixed — handles flat EMA edge case
  [F8] Confluence score correctly reflects triggering strategies
  [F9] Inverse trades use SEPARATE slot counter from shadow
  [F10] max_open_trades applies per-mode not globally
  [F11] Signals can be SHORT — trend context added to SR/DBL
  [F12] Strategy signal logging shows actual reasons
"""

import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from enum import Enum
import warnings
import ccxt
import mysql.connector
from mysql.connector import pooling
import logging
import time
import requests
import traceback
import random
import json
import os
import uuid
import signal as sys_signal
import argparse
from html import escape
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings('ignore')


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

def _lifecycle_flag(value: object) -> bool:
    """Interpret persisted lifecycle flags explicitly; malformed values fail closed."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    return False


def is_observation_only(config: Optional[Any] = None) -> bool:
    """Return True if RetailBot2 is explicitly configured in read-only observation mode."""
    if config is not None and getattr(config, "observation_only", False):
        return True
    return os.getenv("RETAILBOT2_OBSERVATION_ONLY", "").strip() == "1"


ALLOWED_OUTBOX_ENQUEUE_DATABASES: Tuple[str, ...] = ("signal_bot_test", "crypto_signals")


def get_connection_database(conn: Any) -> Optional[str]:
    """Execute SELECT DATABASE() on the active MariaDB/MySQL connection.

    Returns the normalized database name string if successfully retrieved.
    Fails closed (returns None) on missing connection, NULL result, query error,
    or unexpected return structure.
    """
    if conn is None:
        return None
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, (tuple, list)):
            db = row[0] if len(row) > 0 else None
        elif isinstance(row, dict):
            db = next(iter(row.values()), None)
        else:
            db = str(row)
        if db is None:
            return None
        db_str = str(db).strip()
        if not db_str or db_str.upper() in ("NULL", "NONE"):
            return None
        return db_str
    except Exception as exc:
        logging.getLogger("retailbot2.auth").warning(
            "Failed to determine active database identity via SELECT DATABASE(): %s", exc
        )
        return None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def _resolve_configured_database(config: Optional[Any] = None) -> Optional[str]:
    """Resolve expected target database name strictly from configuration object.

    Fails closed (returns None) if configuration is absent or does not specify
    a non-empty database string. Never substitutes BotConfig() defaults or
    unrelated environment defaults.
    """
    if config is None:
        return None
    if isinstance(config, str) and config.strip():
        return config.strip()
    if isinstance(config, dict):
        raw_db = config.get("db_name") or config.get("database")
        if isinstance(raw_db, str) and raw_db.strip():
            return raw_db.strip()
        return None
    raw_db = getattr(config, "db_name", None) or getattr(config, "database", None)
    if isinstance(raw_db, str) and raw_db.strip():
        return raw_db.strip()
    if hasattr(config, "config"):
        inner = getattr(config, "config", None)
        if inner is not None and inner is not config:
            raw_inner_db = getattr(inner, "db_name", None) or getattr(inner, "database", None)
            if isinstance(raw_inner_db, str) and raw_inner_db.strip():
                return raw_inner_db.strip()
    return None


def _resolve_database_name(config: Optional[Any] = None, database: Optional[str] = None) -> Optional[str]:
    """Legacy helper preserved for backward compatibility.
    Resolves database name from config or explicit database parameter."""
    if database is not None and isinstance(database, str) and database.strip():
        return database.strip()
    return _resolve_configured_database(config)


def is_outbox_enqueue_authorized(
    config: Optional[Any] = None,
    conn: Optional[Any] = None,
    database: Optional[str] = None,
) -> bool:
    """Validate outbox enqueue authorization against actual connection identity and configuration.

    Production Enqueue Policy (Work Orders 71760 & 71764):
    1. For 'signal_bot_test': requires RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1.
    2. For 'crypto_signals': requires BOTH:
       - RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1
       - RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1
       Missing, malformed, or unset production flag fails closed.
    3. Active connection identity is verified on the active MariaDB connection via SELECT DATABASE().
    4. Configured expected database and actual connection database must strictly match.
    5. Observation-only mode always suppresses enqueue.
    6. Independent of Telegram delivery authorization (RETAILBOT2_ALLOW_TELEGRAM_DELIVERY).
    7. Independent of trading authorization (RETAILBOT2_ALLOW_TRADING).
    8. Independent of worker startup (RETAILBOT2_OUTBOX_WORKER_ENABLED).
    9. Fails closed without BotConfig() fallback if configuration or connection is absent.
    """
    if is_observation_only(config):
        return False
    if os.getenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "").strip() != "1":
        return False
    if config is None:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: missing configuration (fails closed)"
        )
        return False
    expected_db = _resolve_configured_database(config)
    if not expected_db or expected_db not in ALLOWED_OUTBOX_ENQUEUE_DATABASES:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: configured database %r is not in allowlist %r",
            expected_db,
            ALLOWED_OUTBOX_ENQUEUE_DATABASES,
        )
        return False
    if database is not None and isinstance(database, str) and database.strip():
        if database.strip() != expected_db:
            logging.getLogger("retailbot2.auth").warning(
                "Outbox enqueue rejected: caller-supplied database %r does not match configured database %r",
                database.strip(),
                expected_db,
            )
            return False
    if conn is None:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: missing active database connection (fails closed)"
        )
        return False
    actual_db = get_connection_database(conn)
    if not actual_db or actual_db not in ALLOWED_OUTBOX_ENQUEUE_DATABASES:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: active connection database %r is not in allowlist %r",
            actual_db,
            ALLOWED_OUTBOX_ENQUEUE_DATABASES,
        )
        return False
    if expected_db != actual_db:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: configured database %r does not match active connection database %r",
            expected_db,
            actual_db,
        )
        return False
    if actual_db == "crypto_signals" or expected_db == "crypto_signals":
        if os.getenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", "").strip() != "1":
            logging.getLogger("retailbot2.auth").warning(
                "Production outbox enqueue rejected: RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1 is required for database 'crypto_signals'"
            )
            return False
    return True


def is_shadow_outbox_enqueue_authorized(
    config: Optional[Any] = None,
    conn: Optional[Any] = None,
    database: Optional[str] = None,
) -> bool:
    """Validate shadow outbox enqueue authorization against actual connection identity and configuration.

    Strict Shadow Enqueue Policy (Work Order 71778):
    1. Independent test-only authorization via explicit RETAILBOT2_SHADOW_ENQUEUE_ENABLED=1.
       Missing, empty, or any value other than '1' fails closed.
    2. Configured database must be EXACTLY 'signal_bot_test'.
       Production database 'crypto_signals' is STRICTLY PROHIBITED.
    3. Active database connection's SELECT DATABASE() must report EXACTLY 'signal_bot_test'.
    4. Configured expected database and actual connection database must strictly match.
    5. Observation-only mode always suppresses enqueue.
    6. Does NOT require RETAILBOT2_ALLOW_TRADING=1.
    7. Does NOT authorize production enqueue (RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED).
    8. Fails closed without BotConfig() fallback if configuration or connection is absent.
    """
    logger = logging.getLogger("retailbot2.auth")
    if is_observation_only(config):
        return False
    if os.getenv("RETAILBOT2_SHADOW_ENQUEUE_ENABLED", "").strip() != "1":
        return False
    if config is None:
        logger.warning("Shadow enqueue rejected: missing configuration (fails closed)")
        return False
    expected_db = _resolve_configured_database(config)
    if expected_db != "signal_bot_test":
        logger.warning(
            "Shadow enqueue rejected: database %r is not permitted for shadow enqueue (strictly 'signal_bot_test' only)",
            expected_db,
        )
        return False
    if database is not None and isinstance(database, str) and database.strip():
        if database.strip() != "signal_bot_test":
            logger.warning(
                "Shadow enqueue rejected: caller-supplied database %r does not match 'signal_bot_test'",
                database.strip(),
            )
            return False
    if conn is None:
        logger.warning("Shadow enqueue rejected: missing active database connection (fails closed)")
        return False
    actual_db = get_connection_database(conn)
    if actual_db != "signal_bot_test":
        logger.warning(
            "Shadow enqueue rejected: active connection database %r is not 'signal_bot_test'",
            actual_db,
        )
        return False
    if expected_db != actual_db:
        logger.warning(
            "Shadow enqueue rejected: configured database %r does not match active connection database %r",
            expected_db,
            actual_db,
        )
        return False
    return True


def is_trading_authorized(config: Optional[Any] = None) -> bool:
    """Return True if RetailBot2 trading is explicitly authorized.
    Trading requires independent, explicit authorization via RETAILBOT2_ALLOW_TRADING=1,
    the --allow-trading CLI flag, or BotConfig(allow_trading=True).
    Observation-only mode always suppresses trading."""
    if is_observation_only(config):
        return False
    if config is not None and getattr(config, "allow_trading", None) is not None:
        return bool(config.allow_trading)
    return os.getenv("RETAILBOT2_ALLOW_TRADING", "").strip() == "1"


class SignalMode(Enum):
    SHADOW = "shadow"
    INVERSE = "inverse"
    BOTH = "both"


class StrategyID(Enum):
    RSI_COUNTER_TREND = "RSI_CTR"
    MA_CROSSOVER_WHIPSAW = "MA_WHIP"
    SR_BOUNCE_TRAP = "SR_TRAP"
    MACD_DIVERGENCE = "MACD_DIV"
    BBAND_REVERT = "BB_REV"
    DOUBLE_PATTERN = "DBL_PAT"
    SPIKE_FADE = "SPIKE_FADE"


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BotConfig:
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT"
    ])

    signal_mode: str = "both"

    # Timeframe
    primary_timeframe: str = "15m"

    # Strategy 1: RSI — classic retail levels
    rsi_period: int = 14
    rsi_oversold: int = 35      # [F3] Wider than 30 = more triggers
    rsi_overbought: int = 65    # [F3] Wider than 70 = more triggers
    rsi_zone_width: int = 5     # RSI must be within this of threshold

    # Strategy 2: MA Crossover
    ma_fast: int = 9
    ma_slow: int = 21
    ma_trend: int = 200         # For context only (retail ignores it)

    # Strategy 3: S/R Bounce
    sr_lookback: int = 50
    # [F1] Tightened from 0.002 to 0.0015 — still triggers but less spam
    sr_touch_threshold: float = 0.0015
    sr_min_touches: int = 2

    # Strategy 4: MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal_period: int = 9
    divergence_lookback: int = 30

    # Strategy 5: Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    # [F4] Band proximity — within 0.3% of band counts
    bb_proximity_pct: float = 0.003

    # Strategy 6: Double Pattern
    pattern_lookback: int = 60
    # [F2] Tightened from 0.003 to 0.002
    pattern_tolerance: float = 0.002
    pattern_min_bars_apart: int = 10

    # Strategy 7: Spike Fade
    spike_atr_multiplier: float = 2.5  # Raised — less noise
    # [F6] Fixed candle index — confirmed candles only

    # Risk
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    rr_ratio: float = 2.0
    risk_per_trade: float = 0.01
    initial_capital: float = 10000.0

    # [F10] Separate limits per mode
    max_open_trades_per_mode: int = 8   # 4 shadow + 4 inverse = 8 total
    max_trades_per_symbol: int = 1
    min_trade_interval_hours: int = 2

    # Infrastructure
    # Use the shared signal-bot MySQL connection; no per-RetailBot DB override.
    db_host: str = field(default_factory=lambda: os.getenv('MYSQL_HOST', os.getenv('DB_HOST', '192.168.1.30')))
    db_port: int = int(os.getenv('MYSQL_PORT', os.getenv('DB_PORT', '3306')))
    db_name: str = field(default_factory=lambda: os.getenv('MYSQL_DATABASE', 'crypto_signals'))
    db_user: str = field(default_factory=lambda: os.getenv('MYSQL_USER', os.getenv('DB_USER', 'cms')))
    db_password: str = field(default_factory=lambda: os.getenv('MYSQL_PASSWORD', os.getenv('DB_PASS', '')))
    telegram_token: str = field(default_factory=lambda: os.getenv('TELEGRAM_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', '')))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv('TELEGRAM_CHAT_ID', ''))


    exit_check_interval: int = 5
    api_retries: int = 3
    api_retry_delay: float = 1.0
    heartbeat_interval: int = 60
    observation_only: bool = False
    allow_trading: bool = field(
        default_factory=lambda: os.getenv("RETAILBOT2_ALLOW_TRADING", "").strip() == "1"
    )


# ═══════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════

OUTBOX_LOCK_NAME = "rdt_outbox_worker_lock"


def format_entry_message(payload: Dict, trade_id: int) -> str:
    signal = payload.get("signal") or {}
    side_emoji = "🟢 LONG" if signal.get("side") == "LONG" else "🔴 SHORT"
    mode = str(payload.get("mode", "shadow"))
    mode_tag = "🔄 INVERSE" if mode == "inverse" else "👁️ SHADOW"
    strategies = signal.get("strategies_triggered", [signal.get("strategy", "?")])
    confluence = signal.get("confluence_score", len(strategies))

    strat_lines = []
    for s in strategies:
        strat_lines.append(f"  ├ {escape(str(s))}")
    strat_text = "\n".join(strat_lines) if strat_lines else "  ├ N/A"

    reason = str(signal.get("reason", "N/A"))[:200]
    units = float(payload.get("units", 0.0))
    risk_amt = float(payload.get("risk_amt", 0.0))
    risk_pct = float(payload.get("risk_pct", 0.0))
    entry_val = float(signal.get("entry", 0.0))
    stop_val = float(signal.get("stop", 0.0))
    target_val = float(signal.get("target", 0.0))
    candle_time = escape(str(signal.get("candle_close_time", "N/A")))

    return (
        f"{side_emoji} <b>{mode_tag} #{trade_id}</b>\n"
        f"\n"
        f"<b>Symbol:</b> {escape(str(signal.get('symbol', 'N/A')))}\n"
        f"<b>Time:</b> {candle_time}\n"
        f"\n"
        f"<b>📊 PLAN:</b>\n"
        f"  ├ Entry:  <code>${entry_val:.6f}</code>\n"
        f"  ├ Stop:   <code>${stop_val:.6f}</code>\n"
        f"  └ Target: <code>${target_val:.6f}</code>\n"
        f"\n"
        f"<b>💰 RISK:</b>\n"
        f"  ├ Units: {units:.6f}\n"
        f"  ├ Risk:  ${risk_amt:.2f} ({risk_pct:.1f}%)\n"
        f"  └ R:R:   1:{signal.get('rr', 1.0):.1f}\n"
        f"\n"
        f"<b>🎯 Strategies ({confluence}/7):</b>\n"
        f"{strat_text}\n"
        f"\n"
        f"<b>Reason:</b> <i>{escape(reason)}</i>"
    )


def format_exit_message(payload: Dict) -> str:
    trade = payload.get("trade") or {}
    exit_price = float(payload.get("exit_price", 0.0))
    reason = str(payload.get("reason", "UNKNOWN"))
    pnl = float(payload.get("pnl", 0.0))
    pnl_pct = float(payload.get("pnl_pct", 0.0))
    emoji_map = {
        "TAKE_PROFIT": "🎯✅",
        "STOP_LOSS": "🛑❌",
    }
    emoji = emoji_map.get(reason, "⏹️")
    color = "🟢" if pnl >= 0 else "🔴"
    mode = str(trade.get("mode", "shadow")).upper()

    return (
        f"{emoji} <b>EXIT [{mode}] #{trade.get('id', '?')}</b>\n"
        f"\n"
        f"<b>Symbol:</b> {escape(str(trade.get('symbol', '?')))} "
        f"<b>Side:</b> {escape(str(trade.get('side', '?')))}\n"
        f"<b>Strategy:</b> {escape(str(trade.get('strategy', '?')))}\n"
        f"<b>Reason:</b> {escape(str(reason).replace('_', ' '))}\n"
        f"\n"
        f"{color} <b>P&L:</b> ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
        f"{color} <b>Exit:</b> <code>${exit_price:.6f}</code>\n"
        f"\n"
        f"⏰ {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def build_entry_alert_payload(
    signal: Dict[str, Any],
    units: float,
    risk_amt: float,
    risk_pct: float,
    mode: str = "shadow",
) -> Dict[str, Any]:
    """Pure, deterministic payload builder for outbox entry notifications.

    Validates required fields and returns a JSON-serializable dictionary.
    Does not mutate input parameters, access databases, or make network calls.
    """
    if not isinstance(signal, dict):
        raise ValueError("signal must be a dictionary")

    required_signal_fields = ("symbol", "side", "entry", "stop", "target")
    missing = [f for f in required_signal_fields if f not in signal or signal[f] is None]
    if missing:
        raise ValueError(f"Missing required signal fields: {', '.join(missing)}")

    side = str(signal["side"]).strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid signal side: {side!r}; must be 'LONG' or 'SHORT'")

    try:
        entry_val = float(signal["entry"])
        stop_val = float(signal["stop"])
        target_val = float(signal["target"])
        units_val = float(units)
        risk_amt_val = float(risk_amt)
        risk_pct_val = float(risk_pct)
        rr_val = float(signal.get("rr", 1.0))
        confluence_val = int(signal.get("confluence_score", 1))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Numeric validation failed for entry payload: {exc}") from exc

    if entry_val <= 0 or stop_val <= 0 or target_val <= 0:
        raise ValueError(f"Prices must be positive: entry={entry_val}, stop={stop_val}, target={target_val}")
    if units_val <= 0:
        raise ValueError(f"Units must be positive: {units_val}")
    if risk_amt_val < 0 or risk_pct_val < 0:
        raise ValueError(f"Risk amounts must be non-negative: amt={risk_amt_val}, pct={risk_pct_val}")

    mode_str = str(mode).strip().lower()
    if mode_str not in ("shadow", "inverse"):
        mode_str = "shadow"

    candle_time = signal.get("candle_close_time")
    candle_time_str = str(candle_time) if candle_time is not None else ""

    strategies = signal.get("strategies_triggered")
    if not strategies:
        strat = signal.get("strategy", "combined")
        strategies = [strat] if strat else ["combined"]
    strategies_clean = [str(s) for s in strategies]

    reason_clean = str(signal.get("reason", ""))[:500]

    return {
        "signal": {
            "symbol": str(signal["symbol"]).strip().upper(),
            "side": side,
            "candle_close_time": candle_time_str,
            "entry": entry_val,
            "stop": stop_val,
            "target": target_val,
            "rr": rr_val,
            "strategies_triggered": strategies_clean,
            "strategy": str(signal.get("strategy", strategies_clean[0])),
            "confluence_score": confluence_val,
            "reason": reason_clean,
        },
        "units": units_val,
        "risk_amt": risk_amt_val,
        "risk_pct": risk_pct_val,
        "mode": mode_str,
    }


def build_exit_alert_payload(
    trade: Dict[str, Any],
    exit_price: float,
    reason: str,
    pnl: float,
    pnl_pct: float,
) -> Dict[str, Any]:
    """Pure, deterministic payload builder for outbox exit notifications.

    Validates required fields and returns a JSON-serializable dictionary.
    Does not mutate input parameters, access databases, or make network calls.
    """
    if not isinstance(trade, dict):
        raise ValueError("trade must be a dictionary")

    required_trade_fields = ("symbol", "side")
    missing = [f for f in required_trade_fields if f not in trade or trade[f] is None]
    if missing:
        raise ValueError(f"Missing required trade fields: {', '.join(missing)}")

    side = str(trade["side"]).strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid trade side: {side!r}; must be 'LONG' or 'SHORT'")

    try:
        exit_val = float(exit_price)
        pnl_val = float(pnl)
        pnl_pct_val = float(pnl_pct)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Numeric validation failed for exit payload: {exc}") from exc

    if exit_val <= 0:
        raise ValueError(f"Exit price must be positive: {exit_val}")

    reason_str = str(reason).strip()
    if not reason_str:
        raise ValueError("Exit reason must be a non-empty string")

    trade_id = trade.get("id", 0)
    try:
        trade_id_int = int(trade_id)
    except (ValueError, TypeError):
        trade_id_int = 0

    mode_str = str(trade.get("mode", "shadow")).strip().lower()

    return {
        "trade": {
            "id": trade_id_int,
            "symbol": str(trade["symbol"]).strip().upper(),
            "side": side,
            "strategy": str(trade.get("strategy", "combined")),
            "mode": mode_str,
            "entry": float(trade.get("entry", 0.0)),
            "stop": float(trade.get("stop", 0.0)),
            "target": float(trade.get("target", 0.0)),
            "units": float(trade.get("units", 0.0)),
        },
        "exit_price": exit_val,
        "reason": reason_str,
        "pnl": pnl_val,
        "pnl_pct": pnl_pct_val,
    }


class TelegramAlert:
    """
    Fixed version with:
    - Proper token validation
    - Detailed error logging
    - Automatic retry on network error
    - Response body logging on failure
    - Rate limit handling (30 msg/sec Telegram limit)
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        logger: logging.Logger = None,
        allow_direct: Optional[bool] = None,
        allow_global: Optional[bool] = None,
    ):
        self.token = str(token).strip()
        self.chat_id = str(chat_id).strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.logger = logger or logging.getLogger("Telegram")
        self._last_send_time = 0.0
        self._min_interval = 0.05  # Max 20 msg/sec (safe under 30 limit)

        # Global delivery authorization (required by outbox worker and direct delivery)
        if allow_global is not None:
            self.global_authorized = bool(allow_global)
        else:
            self.global_authorized = (
                os.getenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "").strip() == "1"
            )

        # Direct delivery authorization (prohibited by default; requires BOTH flags)
        direct_flag = (
            bool(allow_direct)
            if allow_direct is not None
            else (os.getenv("RETAILBOT2_ALLOW_DIRECT_TELEGRAM", "").strip() == "1")
        )
        self.direct_authorized = self.global_authorized and direct_flag

        # Validate on init — tell user exactly what's wrong
        self.enabled = self._validate()

    def _validate(self) -> bool:
        """Strict validation with clear error messages."""
        if not self.direct_authorized:
            if not self.global_authorized:
                self.logger.info(
                    "ℹ️ Direct Telegram delivery is disabled (global delivery authorization missing; "
                    "set RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1 and RETAILBOT2_ALLOW_DIRECT_TELEGRAM=1 to enable direct delivery)."
                )
            else:
                self.logger.info(
                    "ℹ️ Direct Telegram delivery is disabled (direct delivery authorization missing; "
                    "set RETAILBOT2_ALLOW_DIRECT_TELEGRAM=1 to enable direct delivery)."
                )
            return False

        if not self.token or self.token in ("", "YOUR_TOKEN", "YOUR_BOT_TOKEN_HERE"):
            self.logger.warning("⚠️  Telegram DISABLED: token is empty/placeholder")
            return False

        if ':' not in self.token:
            self.logger.error(
                f"❌ Telegram DISABLED: token missing colon separator\n"
                f"   Got:    '{self.token}'\n"
                f"   Expect: '1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'"
            )
            return False

        parts = self.token.split(':', 1)
        if not parts[0].isdigit():
            self.logger.error(
                f"❌ Telegram DISABLED: token prefix must be numeric bot ID\n"
                f"   Got prefix: '{parts[0]}'"
            )
            return False

        if len(parts[1]) < 30:
            self.logger.error(
                f"❌ Telegram DISABLED: token hash too short "
                f"({len(parts[1])} chars, expected 35+)"
            )
            return False

        if not self.chat_id or self.chat_id in ("", "YOUR_CHAT_ID", "YOUR_CHAT_ID_HERE"):
            self.logger.warning("⚠️  Telegram DISABLED: chat_id is empty/placeholder")
            return False

        # chat_id must be numeric (positive for users, negative for groups)
        try:
            int(self.chat_id)
        except ValueError:
            self.logger.error(
                f"❌ Telegram DISABLED: chat_id must be numeric\n"
                f"   Got: '{self.chat_id}'\n"
                f"   User chat_id looks like: 123456789\n"
                f"   Group chat_id looks like: -1001234567890"
            )
            return False

        # Live connectivity test
        try:
            resp = requests.get(
                f"{self.base_url}/getMe",
                timeout=8
            )
            data = resp.json()
            if resp.status_code == 200 and data.get('ok'):
                bot_name = data['result'].get('first_name', 'Unknown')
                bot_user = data['result'].get('username', 'N/A')
                self.logger.info(
                    f"✅ Telegram connected: {bot_name} (@{bot_user})"
                )
                return True
            else:
                desc = data.get('description', 'No description')
                self.logger.error(
                    f"❌ Telegram DISABLED: getMe failed — {desc}\n"
                    f"   HTTP {resp.status_code}"
                )
                return False
        except requests.exceptions.ConnectionError:
            self.logger.error(
                "❌ Telegram DISABLED: Cannot reach api.telegram.org\n"
                "   Check internet connection"
            )
            return False
        except requests.exceptions.Timeout:
            self.logger.error(
                "❌ Telegram DISABLED: Connection timed out\n"
                "   Firewall may be blocking api.telegram.org"
            )
            return False
        except Exception as e:
            self.logger.error(f"❌ Telegram validation error: {e}")
            return False

    def send(self, message: str, parse_mode: str = "HTML",
             retries: int = 2) -> bool:
        """Send with retry, rate limiting, and full error reporting."""
        if not self.enabled or not getattr(self, "direct_authorized", True):
            # Console fallback — clean up HTML tags
            clean = (message
                     .replace('<b>', '').replace('</b>', '')
                     .replace('<i>', '').replace('</i>', '')
                     .replace('\n', ' | '))
            print(f"📝 [NO-TG] {clean[:160]}")
            return False

        # Rate limit protection
        now = time.time()
        gap = now - self._last_send_time
        if gap < self._min_interval:
            time.sleep(self._min_interval - gap)

        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        'chat_id': self.chat_id,
                        'text': message,
                        'parse_mode': parse_mode,
                        'disable_web_page_preview': True,
                    },
                    timeout=10,
                )
                self._last_send_time = time.time()

                if resp.status_code == 200:
                    try:
                        response_data = resp.json()
                    except Exception:
                        response_data = {}
                    if response_data.get('ok') is True:
                        return True
                    err_desc = response_data.get('description', 'Telegram response did not confirm success')
                    self.logger.warning("Telegram returned HTTP 200 without ok=true: %s", err_desc)
                    if attempt < retries:
                        time.sleep(2 ** attempt)
                        continue
                    return False

                # Parse error
                try:
                    err_data = resp.json()
                    err_desc = err_data.get('description', 'No description')
                    err_code = err_data.get('error_code', resp.status_code)
                except Exception:
                    err_desc = resp.text[:200]
                    err_code = resp.status_code

                # Handle specific errors
                if resp.status_code == 429:
                    # Too many requests
                    retry_after = err_data.get('parameters', {}).get('retry_after', 5)
                    self.logger.warning(
                        f"⏳ Telegram rate limited — waiting {retry_after}s"
                    )
                    time.sleep(retry_after)
                    continue

                elif resp.status_code == 400:
                    # Bad request — don't retry, log full details
                    self.logger.error(
                        f"❌ Telegram 400 Bad Request: {err_desc}\n"
                        f"   Message preview: {message[:100]}"
                    )
                    
                    # Bad parse mode — try plain text
                    if 'parse' in err_desc.lower() and parse_mode != 'plain':
                        self.logger.info("   Retrying without parse_mode...")
                        plain_resp = requests.post(
                            f"{self.base_url}/sendMessage",
                            json={
                                'chat_id': self.chat_id,
                                'text': message.replace('<b>', '').replace('</b>', ''),
                            },
                            timeout=10,
                        )
                        try:
                            plain_data = plain_resp.json()
                        except Exception:
                            plain_data = {}
                        return plain_resp.status_code == 200 and plain_data.get('ok') is True
                    return False

                elif resp.status_code == 401:
                    self.logger.error(
                        f"❌ Telegram 401 Unauthorized: Invalid token\n"
                        f"   Disabling Telegram alerts"
                    )
                    self.enabled = False
                    return False

                elif resp.status_code == 403:
                    self.logger.error(
                        f"❌ Telegram 403 Forbidden: {err_desc}\n"
                        f"   Bot may be blocked by user or not started\n"
                        f"   → Open Telegram → find your bot → send /start"
                    )
                    return False

                else:
                    self.logger.warning(
                        f"⚠️  Telegram HTTP {err_code}: {err_desc} "
                        f"(attempt {attempt + 1}/{retries + 1})"
                    )
                    if attempt < retries:
                        time.sleep(2 ** attempt)

            except requests.exceptions.Timeout:
                self.logger.warning(
                    f"⏳ Telegram timeout (attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    time.sleep(2)

            except requests.exceptions.ConnectionError as e:
                self.logger.warning(f"🌐 Telegram connection error: {e}")
                if attempt < retries:
                    time.sleep(3)

            except Exception as e:
                self.logger.error(f"❌ Telegram unexpected error: {e}")
                break

        self.logger.error(f"❌ Telegram send failed after {retries + 1} attempts")
        return False

    # Keep your existing send_entry / send_exit / send_stats methods
    # Just change __init__ calls to pass logger:

    def send_entry(self, signal: Dict, trade_id: int, units: float,
                   risk_amt: float, risk_pct: float, mode: str) -> bool:
        payload = {
            "signal": signal,
            "units": units,
            "risk_amt": risk_amt,
            "risk_pct": risk_pct,
            "mode": mode,
        }
        msg = format_entry_message(payload, trade_id)
        return self.send(msg)

    def send_exit(self, trade: Dict, exit_price: float,
                  reason: str, pnl: float, pnl_pct: float) -> bool:
        payload = {
            "trade": trade,
            "exit_price": exit_price,
            "reason": reason,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        }
        msg = format_exit_message(payload)
        return self.send(msg)

    @staticmethod
    def _paper_performance_lines() -> list[str]:
        """Read the same realized paper ledger totals shown on the dashboard."""
        lines = ["📝 <b>Paper Trading Engine · Realized P&amp;L</b>"]
        try:
            try:
                from paper.engine import get_paper_metrics
            except ModuleNotFoundError:
                from engine import get_paper_metrics

            metrics = get_paper_metrics()
        except Exception as exc:
            logging.getLogger(__name__).warning("Paper performance report unavailable (%s: %s)", type(exc).__name__, exc)
            return [*lines, "Paper trade totals unavailable"]

        if not metrics["totalTrades"]:
            lines.append("No closed trades included in reporting")

        wr = float(metrics.get("winRatePct", 0.0))
        lines.append(
            f"Net P&amp;L: <code>${metrics['netPnl']:+,.2f}</code> · "
            f"Closed: {metrics['totalTrades']} · "
            f"WR: <code>{wr:.0f}%</code> · "
            f"Wins: {metrics['wins']} · Losses: {metrics['losses']}"
        )
        by_pair: dict[str, dict[str, list]] = {}
        try:
            try:
                from paper.engine import get_paper_configs
            except ModuleNotFoundError:
                from engine import get_paper_configs

            for cfg in get_paper_configs():
                if cfg.get("report_enabled", True):
                    by_pair.setdefault(cfg["symbol"], {}).setdefault(cfg["strategy_name"], [0, 0, 0, 0.0])
        except Exception as exc:
            logging.getLogger(__name__).debug("Paper configs lookup failed (%s: %s)", type(exc).__name__, exc)

        for row in metrics["byStrategy"]:
            strategies = by_pair.setdefault(row["symbol"], {})
            stats = strategies.setdefault(row["strategy"], [0, 0, 0, 0.0])
            stats[0] += row["totalTrades"]
            stats[1] += row["wins"]
            stats[2] += row["losses"]
            stats[3] += row["netPnl"]

        omitted = 0
        remaining = 1750
        for symbol, strategies in sorted(by_pair.items()):
            heading = f"<b>{escape(symbol)}</b> · Net=${sum(row[3] for row in strategies.values()):+,.2f}"
            pair_lines = [heading]
            for name, (trades, wins, losses, pnl) in sorted(strategies.items()):
                wr = f"{wins / trades:.0%}" if trades else "0%"
                color = "⚪" if trades == 0 else ("🟢" if pnl >= 0 else "🔴")
                pair_lines.append(
                    f"{color} <b>{escape(name)}</b>: T={trades} W={wins} L={losses} WR={wr} Net=${pnl:+,.2f}"
                )
            # Keep each pair heading with at least one strategy. Totals above cover every enabled row.
            if remaining < len(heading) + len(pair_lines[1]) + 2:
                omitted += len(strategies)
                continue
            lines.append(heading)
            remaining -= len(heading) + 1
            for line in pair_lines[1:]:
                if remaining < len(line) + 1:
                    omitted += 1
                    continue
                lines.append(line)
                remaining -= len(line) + 1
        if omitted:
            lines.append(f"Total includes {omitted} more pair/strategy rows. View all in Paper Trading.")
        return lines

    @staticmethod
    def format_stats(stats: Dict, equity_shadow: float, equity_inverse: float) -> str:
        lines = [
            "📊 <b>PERFORMANCE REPORT</b>",
            "─" * 30,
            *TelegramAlert._paper_performance_lines(),
            "",
            "🔬 <b>RetailBot2 Research · Shadow / Inverse</b>",
            f"💰 Shadow Equity:  <code>${equity_shadow:,.2f}</code>",
            f"🔄 Inverse Equity: <code>${equity_inverse:,.2f}</code>",
            "",
        ]
        for key, data in sorted(stats.items()):
            total = data.get('total', 0)
            if total == 0:
                continue
            wins = data.get('wins', 0)
            losses = data.get('losses', 0)
            wr = wins / total * 100 if total > 0 else 0
            net = float(data.get('net_pnl', 0))
            color = "🟢" if net >= 0 else "🔴"
            lines.append(
                f"{color} <b>{escape(key)}</b>: "
                f"T={total} W={wins} L={losses} "
                f"WR={wr:.0f}% Net=${net:+.0f}"
            )
        return "\n".join(lines)

    def send_stats(self, stats: Dict,
                   equity_shadow: float, equity_inverse: float) -> bool:
        return self.send(self.format_stats(stats, equity_shadow, equity_inverse))


# ═══════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════

class Indicators:
    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)  # Avoid division by zero
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl = df['high'] - df['low']
        hc = (df['high'] - df['close'].shift()).abs()
        lc = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def macd(series: pd.Series, fast: int = 12,
             slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_f = series.ewm(span=fast, adjust=False).mean()
        ema_s = series.ewm(span=slow, adjust=False).mean()
        line = ema_f - ema_s
        sig = line.ewm(span=signal, adjust=False).mean()
        hist = line - sig
        return line, sig, hist

    @staticmethod
    def bollinger(series: pd.Series,
                  period: int = 20, std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        mid = series.rolling(window=period).mean()
        s = series.rolling(window=period).std()
        return mid + std * s, mid, mid - std * s

    @staticmethod
    def find_sr_levels(df: pd.DataFrame, lookback: int,
                       threshold: float, min_touches: int) -> Tuple[List[float], List[float]]:
        """
        Find S/R by clustering local pivot highs and lows.
        [F1] More precise — uses actual pivot points not all candle data
        """
        if len(df) < lookback + 5:
            return [], []

        recent = df.iloc[-lookback:].copy()
        current_price = df['close'].iloc[-2]  # Use completed candle

        # Find local pivot highs and lows (5-bar pivot)
        pivot_highs = []
        pivot_lows = []
        closes = recent['high'].values
        lows_arr = recent['low'].values

        for i in range(2, len(recent) - 2):
            # Pivot high: higher than 2 bars each side
            if (closes[i] > closes[i-1] and closes[i] > closes[i-2] and
                    closes[i] > closes[i+1] and closes[i] > closes[i+2]):
                pivot_highs.append(closes[i])
            # Pivot low
            if (lows_arr[i] < lows_arr[i-1] and lows_arr[i] < lows_arr[i-2] and
                    lows_arr[i] < lows_arr[i+1] and lows_arr[i] < lows_arr[i+2]):
                pivot_lows.append(lows_arr[i])

        def cluster_levels(levels: List[float]) -> List[float]:
            if not levels:
                return []
            levels_sorted = sorted(levels)
            clusters = []
            used = set()
            for i, lv in enumerate(levels_sorted):
                if i in used:
                    continue
                group = [lv]
                for j in range(i + 1, len(levels_sorted)):
                    if j in used:
                        continue
                    if abs(levels_sorted[j] - lv) / (lv + 1e-10) < threshold:
                        group.append(levels_sorted[j])
                        used.add(j)
                used.add(i)
                if len(group) >= min_touches:
                    clusters.append(float(np.mean(group)))
            return clusters

        all_supports = cluster_levels(
            [lv for lv in pivot_lows if lv < current_price]
        )
        all_resistances = cluster_levels(
            [lv for lv in pivot_highs if lv > current_price]
        )

        return sorted(all_supports, reverse=True), sorted(all_resistances)


# ═══════════════════════════════════════════════════════════════════
# STRATEGY ENGINE — ALL 7 FIXED
# ═══════════════════════════════════════════════════════════════════

class RetailStrategyEngine:
    def __init__(self, config: BotConfig):
        self.config = config

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = self.config

        df['rsi'] = Indicators.rsi(df['close'], c.rsi_period)
        df['ema_fast'] = Indicators.ema(df['close'], c.ma_fast)
        df['ema_slow'] = Indicators.ema(df['close'], c.ma_slow)
        df['ema_trend'] = Indicators.ema(df['close'], c.ma_trend)
        df['atr'] = Indicators.atr(df, c.atr_period)
        df['macd_line'], df['macd_sig'], df['macd_hist'] = Indicators.macd(
            df['close'], c.macd_fast, c.macd_slow, c.macd_signal_period
        )
        df['bb_upper'], df['bb_mid'], df['bb_lower'] = Indicators.bollinger(
            df['close'], c.bb_period, c.bb_std
        )
        df['body'] = (df['close'] - df['open']).abs()
        df['range'] = df['high'] - df['low']
        return df

    def _base_signal(self, df: pd.DataFrame, symbol: str,
                     side: str, strategy: StrategyID, reason: str) -> Dict:
        """Build a standard signal dict from the completed candle."""
        curr = df.iloc[-2]
        entry = float(curr['close'])
        atr_val = float(curr['atr'])
        c = self.config
        risk = atr_val * c.atr_sl_multiplier

        if side == 'LONG':
            stop = entry - risk
            target = entry + risk * c.rr_ratio
        else:
            stop = entry + risk
            target = entry - risk * c.rr_ratio

        return {
            'symbol': symbol,
            'side': side,
            'entry': entry,
            'stop': stop,
            'target': target,
            'atr': atr_val,
            'rr': c.rr_ratio,
            'strategy': strategy.value,
            'reason': reason,
            'candle_close_time': df.index[-2],
            'strategies_triggered': [strategy.value],
            'confluence_score': 1,
        }

    # ──────────────────────────────────────────────────────────────
    # [F3] STRATEGY 1: RSI Counter-Trend — FIXED
    # Zone-based: RSI within 5 points of threshold, not exact cross
    # Also checks whether RSI was BELOW threshold recently (more signals)
    # ──────────────────────────────────────────────────────────────
    def strat_rsi_counter_trend(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 50:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['rsi']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        c = self.config

        # LONG: RSI was below oversold and now recovering
        # [F3] Was: exact cross. Now: curr in zone AND prev was below threshold
        rsi_was_oversold = prev['rsi'] < c.rsi_oversold
        rsi_recovering = curr['rsi'] > prev['rsi']
        rsi_in_bounce_zone = c.rsi_oversold <= curr['rsi'] <= (c.rsi_oversold + c.rsi_zone_width)

        if rsi_was_oversold and rsi_recovering and rsi_in_bounce_zone:
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.RSI_COUNTER_TREND,
                f"RSI bounce {prev['rsi']:.1f}→{curr['rsi']:.1f} "
                f"(oversold={c.rsi_oversold}, no trend filter)"
            )

        # SHORT: RSI was above overbought and now rejecting
        rsi_was_overbought = prev['rsi'] > c.rsi_overbought
        rsi_rejecting = curr['rsi'] < prev['rsi']
        rsi_in_reject_zone = (c.rsi_overbought - c.rsi_zone_width) <= curr['rsi'] <= c.rsi_overbought

        if rsi_was_overbought and rsi_rejecting and rsi_in_reject_zone:
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.RSI_COUNTER_TREND,
                f"RSI rejection {prev['rsi']:.1f}→{curr['rsi']:.1f} "
                f"(overbought={c.rsi_overbought}, no trend filter)"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F7] STRATEGY 2: MA Crossover — FIXED
    # Handles flat EMA, adds minimum separation check
    # ──────────────────────────────────────────────────────────────
    def strat_ma_crossover(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 50:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['ema_fast']) or pd.isna(curr['ema_slow']) or curr['atr'] <= 0:
            return None

        # [F7] Minimum separation to avoid flat EMA false cross
        min_sep = curr['atr'] * 0.05  # Must differ by at least 5% of ATR

        curr_fast = float(curr['ema_fast'])
        curr_slow = float(curr['ema_slow'])
        prev_fast = float(prev['ema_fast'])
        prev_slow = float(prev['ema_slow'])

        # Golden cross
        crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
        separation_ok = abs(curr_fast - curr_slow) > min_sep

        if crossed_up and separation_ok:
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.MA_CROSSOVER_WHIPSAW,
                f"EMA{self.config.ma_fast}/{self.config.ma_slow} golden cross "
                f"({prev_fast:.2f}→{curr_fast:.2f})"
            )

        # Death cross
        crossed_down = prev_fast >= prev_slow and curr_fast < curr_slow

        if crossed_down and separation_ok:
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.MA_CROSSOVER_WHIPSAW,
                f"EMA{self.config.ma_fast}/{self.config.ma_slow} death cross "
                f"({prev_fast:.2f}→{curr_fast:.2f})"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F1] [F11] STRATEGY 3: S/R Bounce — FIXED
    # Uses actual pivot-based S/R, adds trend context for direction
    # ──────────────────────────────────────────────────────────────
    def strat_sr_bounce(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < self.config.sr_lookback + 10:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        supports, resistances = Indicators.find_sr_levels(
            df.iloc[:-1],
            self.config.sr_lookback,
            self.config.sr_touch_threshold,
            self.config.sr_min_touches
        )

        price = float(curr['close'])
        threshold = self.config.sr_touch_threshold

        # [F11] Use EMA trend to decide direction
        is_uptrend = curr['close'] > curr['ema_trend']

        # Buy at support — worse if in downtrend (retail buys dip in downtrend)
        for support in supports[:2]:
            dist = abs(price - support) / (support + 1e-10)
            if dist < threshold:
                direction = 'LONG'
                trend_note = "IN DOWNTREND (catching knife)" if not is_uptrend else "at support"
                return self._base_signal(
                    df, symbol, direction,
                    StrategyID.SR_BOUNCE_TRAP,
                    f"Bounce at support ${support:.4f} {trend_note}"
                )

        # Sell at resistance — worse if in uptrend
        for resistance in resistances[:2]:
            dist = abs(price - resistance) / (resistance + 1e-10)
            if dist < threshold:
                direction = 'SHORT'
                trend_note = "IN UPTREND (selling rip)" if is_uptrend else "at resistance"
                return self._base_signal(
                    df, symbol, direction,
                    StrategyID.SR_BOUNCE_TRAP,
                    f"Rejection at resistance ${resistance:.4f} {trend_note}"
                )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F5] STRATEGY 4: MACD Divergence — FIXED
    # Robust swing detection using argrelextrema
    # ──────────────────────────────────────────────────────────────
    def strat_macd_divergence(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        from scipy.signal import argrelextrema

        if len(df) < self.config.divergence_lookback + 40:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['macd_hist']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        lb = self.config.divergence_lookback
        recent = df.iloc[-(lb + 2):-1].copy()

        if len(recent) < 20:
            return None

        prices_low = recent['low'].values
        prices_high = recent['high'].values
        hist = recent['macd_hist'].values

        # Find local minima (bullish divergence)
        try:
            min_idx = argrelextrema(prices_low, np.less, order=3)[0]
            if len(min_idx) >= 2:
                i1, i2 = min_idx[-2], min_idx[-1]
                if i2 - i1 >= 5:
                    # Price: lower low | MACD: higher low = bullish divergence
                    if prices_low[i2] < prices_low[i1] and hist[i2] > hist[i1]:
                        return self._base_signal(
                            df, symbol, 'LONG',
                            StrategyID.MACD_DIVERGENCE,
                            f"Bullish MACD div: price {prices_low[i1]:.4f}→{prices_low[i2]:.4f} "
                            f"hist {hist[i1]:.4f}→{hist[i2]:.4f}"
                        )

            # Find local maxima (bearish divergence)
            max_idx = argrelextrema(prices_high, np.greater, order=3)[0]
            if len(max_idx) >= 2:
                i1, i2 = max_idx[-2], max_idx[-1]
                if i2 - i1 >= 5:
                    # Price: higher high | MACD: lower high = bearish divergence
                    if prices_high[i2] > prices_high[i1] and hist[i2] < hist[i1]:
                        return self._base_signal(
                            df, symbol, 'SHORT',
                            StrategyID.MACD_DIVERGENCE,
                            f"Bearish MACD div: price {prices_high[i1]:.4f}→{prices_high[i2]:.4f} "
                            f"hist {hist[i1]:.4f}→{hist[i2]:.4f}"
                        )
        except Exception:
            return None

        return None

    # ──────────────────────────────────────────────────────────────
    # [F4] STRATEGY 5: Bollinger Band — FIXED
    # Uses proximity percentage not exact touch (more realistic)
    # ──────────────────────────────────────────────────────────────
    def strat_bband_reversion(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < self.config.bb_period + 10:
            return None

        curr = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(curr['bb_lower']) or pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        price = float(curr['close'])
        lower = float(curr['bb_lower'])
        upper = float(curr['bb_upper'])
        prox = self.config.bb_proximity_pct

        # [F4] Within proximity % of lower band (not exact cross)
        near_lower = abs(price - lower) / (lower + 1e-10) < prox
        prev_price = float(prev['close'])
        was_near_or_below = prev_price <= lower * (1 + prox)

        if near_lower or (prev_price < lower and price > lower):
            return self._base_signal(
                df, symbol, 'LONG',
                StrategyID.BBAND_REVERT,
                f"Near lower BB ${lower:.4f} (price ${price:.4f}, "
                f"gap {abs(price - lower) / lower * 100:.2f}%)"
            )

        near_upper = abs(price - upper) / (upper + 1e-10) < prox

        if near_upper or (prev_price > upper and price < upper):
            return self._base_signal(
                df, symbol, 'SHORT',
                StrategyID.BBAND_REVERT,
                f"Near upper BB ${upper:.4f} (price ${price:.4f}, "
                f"gap {abs(price - upper) / upper * 100:.2f}%)"
            )

        return None

    # ──────────────────────────────────────────────────────────────
    # [F2] STRATEGY 6: Double Pattern — FIXED
    # Tighter tolerance, proper min separation, clearer direction
    # ──────────────────────────────────────────────────────────────
    def strat_double_pattern(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        from scipy.signal import argrelextrema

        if len(df) < self.config.pattern_lookback + 10:
            return None

        curr = df.iloc[-2]
        if pd.isna(curr['atr']) or curr['atr'] <= 0:
            return None

        lb = self.config.pattern_lookback
        recent = df.iloc[-(lb + 2):-1].copy()
        tol = self.config.pattern_tolerance
        min_sep = self.config.pattern_min_bars_apart

        highs = recent['high'].values
        lows = recent['low'].values

        try:
            # Double top: two highs at similar level
            peak_idx = argrelextrema(highs, np.greater, order=4)[0]
            if len(peak_idx) >= 2:
                p1, p2 = peak_idx[-2], peak_idx[-1]
                if p2 - p1 >= min_sep:
                    similarity = abs(highs[p1] - highs[p2]) / (highs[p1] + 1e-10)
                    if similarity < tol:
                        # Check price is below both peaks (early short entry)
                        pattern_high = max(highs[p1], highs[p2])
                        if float(curr['close']) < pattern_high * (1 - tol / 2):
                            return self._base_signal(
                                df, symbol, 'SHORT',
                                StrategyID.DOUBLE_PATTERN,
                                f"Double top at ${pattern_high:.4f} "
                                f"(sep={p2-p1} bars, sim={similarity*100:.2f}%)"
                            )

            # Double bottom: two lows at similar level
            trough_idx = argrelextrema(lows, np.less, order=4)[0]
            if len(trough_idx) >= 2:
                t1, t2 = trough_idx[-2], trough_idx[-1]
                if t2 - t1 >= min_sep:
                    similarity = abs(lows[t1] - lows[t2]) / (lows[t1] + 1e-10)
                    if similarity < tol:
                        pattern_low = min(lows[t1], lows[t2])
                        if float(curr['close']) > pattern_low * (1 + tol / 2):
                            return self._base_signal(
                                df, symbol, 'LONG',
                                StrategyID.DOUBLE_PATTERN,
                                f"Double bottom at ${pattern_low:.4f} "
                                f"(sep={t2-t1} bars, sim={similarity*100:.2f}%)"
                            )
        except Exception:
            return None

        return None

    # ──────────────────────────────────────────────────────────────
    # [F6] STRATEGY 7: Spike Fade — FIXED
    # Correct candle indexing: spike at [-3], confirm at [-2]
    # ──────────────────────────────────────────────────────────────
    def strat_spike_fade(self, df: pd.DataFrame, symbol: str) -> Optional[Dict]:
        if len(df) < 30:
            return None

        # [F6] Both must be COMPLETED candles
        confirm = df.iloc[-2]   # Most recent completed candle
        spike = df.iloc[-3]     # Candle before that

        if pd.isna(spike['atr']) or spike['atr'] <= 0:
            return None
        if pd.isna(confirm['atr']) or confirm['atr'] <= 0:
            return None

        threshold = spike['atr'] * self.config.spike_atr_multiplier

        # Bull spike: big green candle at [-3], fade with short
        bull_spike = (
            spike['body'] > threshold
            and spike['close'] > spike['open']  # Green candle
        )
        if bull_spike:
            # Confirm: next candle shows reversal attempt
            if confirm['close'] < confirm['open']:  # Red confirmation
                return self._base_signal(
                    df, symbol, 'SHORT',
                    StrategyID.SPIKE_FADE,
                    f"Fading bull spike: body={spike['body']:.4f} "
                    f"vs ATR={spike['atr']:.4f} ({spike['body']/spike['atr']:.1f}x)"
                )

        # Bear spike: big red candle at [-3], fade with long
        bear_spike = (
            spike['body'] > threshold
            and spike['close'] < spike['open']  # Red candle
        )
        if bear_spike:
            if confirm['close'] > confirm['open']:  # Green confirmation
                return self._base_signal(
                    df, symbol, 'LONG',
                    StrategyID.SPIKE_FADE,
                    f"Fading bear spike: body={spike['body']:.4f} "
                    f"vs ATR={spike['atr']:.4f} ({spike['body']/spike['atr']:.1f}x)"
                )

        return None

    # ──────────────────────────────────────────────────────────────
    # COMPOSITE SIGNAL BUILDER
    # ──────────────────────────────────────────────────────────────
    def run_all(
        self, df: pd.DataFrame, symbol: str,
        enabled_strategies: Optional[set] = None,
    ) -> List[Dict]:
        """Run enabled strategy functions and return the signals that fire.

        None preserves the all-strategies behavior for existing callers.
        An explicit empty set intentionally evaluates no strategy functions.
        """
        runners = [
            (StrategyID.RSI_COUNTER_TREND, self.strat_rsi_counter_trend),
            (StrategyID.MA_CROSSOVER_WHIPSAW, self.strat_ma_crossover),
            (StrategyID.SR_BOUNCE_TRAP, self.strat_sr_bounce),
            (StrategyID.MACD_DIVERGENCE, self.strat_macd_divergence),
            (StrategyID.BBAND_REVERT, self.strat_bband_reversion),
            (StrategyID.DOUBLE_PATTERN, self.strat_double_pattern),
            (StrategyID.SPIKE_FADE, self.strat_spike_fade),
        ]
        results = []
        for strategy_id, fn in runners:
            if enabled_strategies is not None and strategy_id.value not in enabled_strategies:
                continue
            try:
                sig = fn(df, symbol)
                if sig is not None:
                    results.append(sig)
            except Exception:
                # Do not silently turn implementation/data errors into a
                # normal "no signal" result. Include the strategy and symbol
                # so operators can identify a broken strategy from worker logs.
                logging.getLogger(__name__).exception(
                    "Strategy evaluation failed: strategy=%s symbol=%s",
                    strategy_id.value,
                    symbol,
                )
        return results

    def build_composite(self, signals: List[Dict]) -> Optional[Dict]:
        """
        [F8] Fixed confluence count.
        Group by direction, majority wins.
        """
        if not signals:
            return None

        longs = [s for s in signals if s['side'] == 'LONG']
        shorts = [s for s in signals if s['side'] == 'SHORT']

        chosen = longs if len(longs) >= len(shorts) else shorts
        if not chosen:
            return None

        direction = chosen[0]['side']
        base = chosen[0].copy()

        # This trade is the result of a multi-strategy composite, not an
        # independently executed trade for whichever contributor happened
        # to appear first. Keep contributors for diagnostics, but attribute
        # realized P&L to the explicit composite experiment.
        base['strategy'] = 'COMPOSITE'

        # [F8] Correct confluence count = number of strategies agreeing
        base['confluence_score'] = len(chosen)      # FIXED
        base['strategies_triggered'] = [s['strategy'] for s in chosen]  # FIXED
        base['reason'] = " ⊕ ".join(
            set(s.get('reason', '') for s in chosen)
        )

        # Use average entry/stop across agreeing strategies
        avg_entry = np.mean([s['entry'] for s in chosen])
        if direction == 'LONG':
            # Tightest stop = closest to entry = most likely to be hit
            tightest_stop = max(s['stop'] for s in chosen)
            risk = avg_entry - tightest_stop
            base['entry'] = avg_entry
            base['stop'] = tightest_stop
            base['target'] = avg_entry + risk * self.config.rr_ratio
        else:
            tightest_stop = min(s['stop'] for s in chosen)
            risk = tightest_stop - avg_entry
            base['entry'] = avg_entry
            base['stop'] = tightest_stop
            base['target'] = avg_entry - risk * self.config.rr_ratio

        base['atr'] = np.mean([s['atr'] for s in chosen])
        return base


# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════

class DatabaseManager:
    def __init__(self, config: BotConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.pool: Optional[pooling.MySQLConnectionPool] = None
        self._init_pool()

    def _init_pool(self):
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name="rdt_bot_pool",
                pool_size=3,
                pool_reset_session=True,
                host=self.config.db_host,
                port=self.config.db_port,
                database=self.config.db_name,
                user=self.config.db_user,
                password=self.config.db_password,
            )
            self._verify_schema()
            self.logger.info("✅ Database pool ready")
        except Exception as e:
            self.logger.error(f"❌ DB init error: {e}")
            self.pool = None

    def _verify_schema(self):
        """Verify migration-managed RetailBot2 tables without issuing DDL/DML."""
        conn = self.get_conn()
        if not conn:
            raise RuntimeError("RetailBot2 database connection is unavailable")
        try:
            cur = conn.cursor(dictionary=True)
            required_tables = ("rdt_trades", "rdt_strategy_stats", "bot_control", "rdt_strategy_lifecycle", "rdt_telegram_outbox")
            placeholders = ", ".join(["%s"] * len(required_tables))
            cur.execute(
                f"""SELECT TABLE_NAME AS table_name
                    FROM information_schema.tables
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME IN ({placeholders})""",
                required_tables,
            )
            existing = {str(row["table_name"]) for row in cur.fetchall()}
            missing = sorted(set(required_tables) - existing)
            if missing:
                raise RuntimeError(
                    "RetailBot2 schema is missing tables "
                    f"({', '.join(missing)}); run 'python -m db.migrate' before starting the worker."
                )
            cur.execute(
                """SELECT COLUMN_NAME AS column_name
                   FROM information_schema.columns
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
                ("rdt_trades", "telegram_enabled"),
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "RetailBot2 schema is missing rdt_trades.telegram_enabled; "
                    "run 'python -m db.migrate' before starting the worker."
                )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                ("rdt_telegram_outbox", "claim_token"),
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "RetailBot2 schema is missing rdt_telegram_outbox.claim_token; "
                    "run python -m db.migrate before starting the worker."
                )
            cur.close()
        finally:
            conn.close()

    def get_strategy_lifecycle_settings(self) -> List[Dict]:
        """Return explicit user lifecycle settings. Missing rows use safe
        defaults in the caller: background paper/report on, Telegram off."""
        conn = self.get_conn()
        if not conn:
            raise RuntimeError("RetailBot2 database is unavailable")
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("""
                SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
                       telegram_enabled, report_enabled, auto_promote_enabled
                FROM rdt_strategy_lifecycle
            """)
            rows = cur.fetchall()
            cur.close()
            return rows
        finally:
            conn.close()

    def get_bot_control(self) -> Dict:
        """Web-dashboard-controlled enable flag + BotConfig field overrides.
        Polled periodically by the run loop (see RetailDeathTrapBot._reload_control)
        rather than only read once at startup, so changes made from the
        dashboard take effect on a running process without a restart.
        Fails closed (enabled=False, available=False) on connection error, query failure,
        missing control record, or malformed overrides payload."""
        conn = self.get_conn()
        if not conn:
            self.logger.error("❌ get_bot_control: database connection unavailable; failing closed with trading disabled")
            return {'enabled': False, 'overrides': {}, 'available': False}
        c = None
        try:
            c = conn.cursor(dictionary=True)
            c.execute("SELECT enabled, overrides_json FROM bot_control WHERE id=1")
            row = c.fetchone()
            if not row:
                self.logger.error("❌ get_bot_control: bot_control record (id=1) missing; failing closed with trading disabled")
                return {'enabled': False, 'overrides': {}, 'available': False}
            raw_overrides = row.get('overrides_json')
            overrides = {}
            if raw_overrides:
                try:
                    parsed = json.loads(raw_overrides)
                    if isinstance(parsed, dict):
                        overrides = parsed
                    else:
                        self.logger.error("❌ get_bot_control: overrides_json is not a JSON object; failing closed with trading disabled")
                        return {'enabled': False, 'overrides': {}, 'available': False}
                except (json.JSONDecodeError, TypeError) as exc:
                    self.logger.error("❌ get_bot_control: malformed overrides_json (%s); failing closed with trading disabled", exc)
                    return {'enabled': False, 'overrides': {}, 'available': False}
            raw_enabled = bool(row.get('enabled'))
            # Legacy safety transition: if overrides_json is NULL, this row was seeded
            # by legacy migration 022 without explicit operator configuration.
            # Never treat legacy unconfigured rows as trading enabled.
            if raw_enabled and raw_overrides is None:
                self.logger.warning(
                    "⚠️ get_bot_control: legacy unconfigured bot_control row (overrides_json IS NULL) "
                    "detected; failing closed with trading disabled until explicitly saved"
                )
                return {'enabled': False, 'overrides': {}, 'available': True, 'legacy_unconfigured': True}

            return {'enabled': raw_enabled, 'overrides': overrides, 'available': True}
        except Exception as e:
            self.logger.error(f"❌ get_bot_control failed: {e}; failing closed with trading disabled")
            return {'enabled': False, 'overrides': {}, 'available': False}
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception as close_error:
                    self.logger.warning("get_bot_control: cursor close failed: %s", close_error)
            conn.close()

    def set_bot_control(self, enabled: Optional[bool] = None, overrides: Optional[Dict] = None):
        """Partial update -- pass only what changed. `overrides` REPLACES the
        whole overrides dict (the web route is expected to read-modify-write,
        not send a partial patch), matching how the dashboard form works:
        it always submits the full current parameter set."""
        conn = self.get_conn()
        if not conn:
            return False
        try:
            c = conn.cursor()
            if enabled is not None and overrides is not None:
                c.execute(
                    "UPDATE bot_control SET enabled=%s, overrides_json=%s WHERE id=1",
                    (enabled, json.dumps(overrides))
                )
            elif enabled is not None:
                c.execute("UPDATE bot_control SET enabled=%s WHERE id=1", (enabled,))
            elif overrides is not None:
                c.execute(
                    "UPDATE bot_control SET overrides_json=%s WHERE id=1",
                    (json.dumps(overrides),)
                )
            conn.commit()
            c.close()
            return True
        except Exception as e:
            self.logger.error(f"❌ set_bot_control: {e}")
            return False
        finally:
            conn.close()

    def get_conn(self):
        if not self.pool:
            return None
        try:
            conn = self.pool.get_connection()
            conn.ping(reconnect=True)
            return conn
        except Exception as e:
            self.logger.error(f"❌ DB conn error: {e}")
            return None

    def save_trade(self, signal: Dict, units: float, mode: str, entry_alert: Optional[Dict] = None) -> Optional[int]:
        if is_observation_only(getattr(self, "config", None)):
            self.logger.error("❌ DatabaseManager.save_trade blocked in observation-only mode; failing closed")
            return None
        if not is_trading_authorized(getattr(self, "config", None)):
            self.logger.error("❌ DatabaseManager.save_trade blocked: trading is not authorized; failing closed")
            return None
        conn = self.get_conn()
        if not conn:
            return None
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO rdt_trades
                (symbol, side, mode, entry_price, stop_price, target_price,
                 atr, rr, units, strategy, strategies_json, telegram_enabled,
                 confluence_score, reason, entry_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                signal['symbol'], signal['side'], mode,
                signal['entry'], signal['stop'], signal['target'],
                signal['atr'], signal['rr'], units,
                signal.get('strategy', 'combined'),
                json.dumps(signal.get('strategies_triggered', [])),
                bool(signal.get('telegram_enabled', False)),
                signal.get('confluence_score', 1),
                str(signal.get('reason', ''))[:500],
                signal.get('candle_close_time', datetime.now(timezone.utc).replace(tzinfo=None)),
            ))
            tid = c.lastrowid
            enqueue_cfg = getattr(self, "config", None)
            if (
                bool(signal.get('telegram_enabled', False))
                and entry_alert
                and is_outbox_enqueue_authorized(enqueue_cfg, conn=conn)
            ):
                c.execute(
                    """INSERT INTO rdt_telegram_outbox
                       (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
                       VALUES (%s, %s, 'entry', %s, 'pending', 0, UTC_TIMESTAMP())
                       ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)""",
                    (f"retailbot2:entry:{tid}", tid, json.dumps(entry_alert, default=str)),
                )
            conn.commit()
            return tid
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.logger.error(f"❌ save_trade: {e}")
            return None
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def close_trade(self, trade_id: int, exit_price: float,
                    reason: str, pnl: float, pnl_pct: float):
        """Close a trade in its own transaction.

        Kept for compatibility with existing callers. New trade-closing code
        should use close_trade_and_update_stats() so the trade row and strategy
        statistics commit atomically.
        """
        if is_observation_only(getattr(self, "config", None)):
            self.logger.error("❌ DatabaseManager.close_trade blocked in observation-only mode; failing closed")
            return False
        conn = self.get_conn()
        if not conn:
            return False
        c = None
        try:
            c = conn.cursor()
            c.execute("""
                UPDATE rdt_trades SET status='CLOSED',
                exit_price=%s, exit_reason=%s, net_pnl=%s,
                pnl_percent=%s, exit_time=%s WHERE id=%s
            """, (exit_price, reason, pnl, pnl_pct, datetime.now(timezone.utc).replace(tzinfo=None), trade_id))
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.logger.error(f"❌ close_trade: {e}")
            return False
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def close_trade_and_update_stats(
            self, trade_id: int, exit_price: float, reason: str,
            pnl: float, pnl_pct: float, strategy: str, mode: str,
            exit_alert: Optional[Dict] = None) -> bool:
        """Atomically close a trade and update its strategy statistics.

        Both writes use the same DB connection and transaction. No caller
        state must be mutated until this method returns True. If either write
        fails, the transaction is rolled back and the trade remains OPEN.
        """
        if is_observation_only(getattr(self, "config", None)):
            self.logger.error("❌ DatabaseManager.close_trade_and_update_stats blocked in observation-only mode; failing closed")
            return False
        conn = self.get_conn()
        if not conn:
            return False

        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE rdt_trades SET status='CLOSED',
                exit_price=%s, exit_reason=%s, net_pnl=%s,
                pnl_percent=%s, exit_time=%s
                WHERE id=%s AND mode=%s AND status='OPEN'
            """, (exit_price, reason, pnl, pnl_pct, datetime.now(timezone.utc).replace(tzinfo=None), trade_id, mode))

            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"trade close affected {cursor.rowcount} rows for id={trade_id}"
                )

            cursor.execute("""
                INSERT INTO rdt_strategy_stats
                    (strategy, mode, total_trades, wins, losses, total_pnl)
                VALUES (%s, %s, 1, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_trades = total_trades + 1,
                    wins = wins + %s,
                    losses = losses + %s,
                    total_pnl = total_pnl + %s
            """, (
                strategy, mode,
                1 if pnl > 0 else 0, int(pnl < 0), pnl,
                1 if pnl > 0 else 0, int(pnl < 0), pnl,
            ))

            enqueue_cfg = getattr(self, "config", None)
            if (
                exit_alert
                and bool(exit_alert.get('telegram_enabled', False))
                and is_outbox_enqueue_authorized(enqueue_cfg, conn=conn)
            ):
                payload = dict(exit_alert)
                payload.pop('telegram_enabled', None)
                cursor.execute(
                    """INSERT INTO rdt_telegram_outbox
                       (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
                       VALUES (%s, %s, 'exit', %s, 'pending', 0, UTC_TIMESTAMP())
                       ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)""",
                    (f"retailbot2:exit:{trade_id}", trade_id, json.dumps(payload, default=str)),
                )
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.logger.error(f"❌ close_trade_and_update_stats: {e}")
            return False
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            conn.close()

    def update_stats(self, strategy: str, mode: str,
                     won: bool, pnl: float):
        if is_observation_only(getattr(self, "config", None)):
            self.logger.error("❌ DatabaseManager.update_stats blocked in observation-only mode; failing closed")
            return
        conn = self.get_conn()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO rdt_strategy_stats
                    (strategy, mode, total_trades, wins, losses, total_pnl)
                VALUES (%s, %s, 1, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_trades = total_trades + 1,
                    wins = wins + %s,
                    losses = losses + %s,
                    total_pnl = total_pnl + %s
            """, (
                strategy, mode,
                int(won), int(not won and pnl < 0), pnl,
                int(won), int(not won and pnl < 0), pnl,
            ))
            conn.commit()
        except Exception as e:
            self.logger.error(f"❌ update_stats: {e}")
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def load_open_trades(self) -> List[Dict]:
        conn = self.get_conn()
        if not conn:
            return []
        c = None
        try:
            c = conn.cursor(dictionary=True)
            c.execute("SELECT * FROM rdt_trades WHERE status='OPEN'")
            rows = c.fetchall()
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, Decimal):
                        r[k] = float(v)
            return rows
        except Exception as e:
            self.logger.error(f"❌ load_open_trades: {e}")
            return []
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def count_open(self, mode: str = None, symbol: str = None, strategy: str = None) -> int:
        conn = self.get_conn()
        if not conn:
            return 0
        c = None
        try:
            c = conn.cursor()
            sql = "SELECT COUNT(*) FROM rdt_trades WHERE status='OPEN'"
            params = []
            if mode:
                sql += " AND mode=%s"
                params.append(mode)
            if symbol:
                sql += " AND symbol=%s"
                params.append(symbol)
            if strategy:
                sql += " AND strategy=%s"
                params.append(strategy)
            c.execute(sql, params)
            count = c.fetchone()[0]
            return count
        except Exception as e:
            self.logger.error(f"❌ count_open: {e}")
            return 0
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def get_stats(self) -> Dict:
        """Build performance statistics from the closed trade ledger.

        rdt_trades is the single source of truth. rdt_strategy_stats remains
        a derived/cache table for compatibility, but reports must never read
        it because historical incremental summaries can drift from the
        authoritative trade ledger.
        """
        conn = self.get_conn()
        if not conn:
            return {}
        c = None
        try:
            c = conn.cursor(dictionary=True)
            c.execute("""
                SELECT
                    t.strategy,
                    t.mode,
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM(net_pnl), 0) AS total_pnl
                FROM rdt_trades t
                LEFT JOIN rdt_strategy_lifecycle l
                  ON l.strategy_id = t.strategy AND l.mode = t.mode
                WHERE t.status='CLOSED'
                  AND COALESCE(l.report_enabled, TRUE) = TRUE
                GROUP BY t.strategy, t.mode
                ORDER BY t.strategy, t.mode
            """)
            rows = c.fetchall()
            result = {}
            for r in rows:
                key = f"{r['strategy']}_{r['mode']}"
                result[key] = {
                    'total': int(r['total_trades']),
                    'wins': int(r['wins']),
                    'losses': int(r['losses']),
                    'net_pnl': float(r['total_pnl'])
                    if isinstance(r['total_pnl'], Decimal)
                    else float(r.get('total_pnl', 0)),
                }
            return result
        except Exception as e:
            self.logger.error(f"❌ get_stats: {e}")
            return {}
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def get_realized_pnl_by_mode(self) -> Dict[str, float]:
        """Sum of net_pnl for CLOSED trades, grouped by mode. Used to
        rebuild equity_shadow/equity_inverse on restart -- without this,
        _load_state() resets both back to initial_capital on every
        restart, silently discarding all realized gains/losses."""
        conn = self.get_conn()
        if not conn:
            return {}
        c = None
        try:
            c = conn.cursor(dictionary=True)
            c.execute("""
                SELECT mode, COALESCE(SUM(net_pnl), 0) AS total_pnl
                FROM rdt_trades WHERE status='CLOSED' GROUP BY mode
            """)
            rows = c.fetchall()
            return {r['mode']: float(r['total_pnl']) for r in rows}
        except Exception as e:
            self.logger.error(f"❌ get_realized_pnl_by_mode: {e}")
            return {}
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()

    def get_last_trade_times(self) -> Dict[str, Dict[str, Dict[str, datetime]]]:
        """Most recent entry per (symbol, mode, strategy), so strategies
        maintain independent cooldowns across process restarts."""
        conn = self.get_conn()
        if not conn:
            return {}
        c = None
        try:
            c = conn.cursor(dictionary=True)
            c.execute("""
                SELECT symbol, mode, strategy, MAX(entry_time) AS last_entry
                FROM rdt_trades GROUP BY symbol, mode, strategy
            """)
            rows = c.fetchall()
            result: Dict[str, Dict[str, Dict[str, datetime]]] = {}
            for r in rows:
                result.setdefault(r['symbol'], {}).setdefault(r['mode'], {})[r['strategy']] = r['last_entry']
            return result
        except Exception as e:
            self.logger.error(f"❌ get_last_trade_times: {e}")
            return {}
        finally:
            try:
                if c is not None:
                    c.close()
            finally:
                conn.close()


    def acquire_outbox_lock(self, conn, timeout_seconds: int = 0) -> bool:
        """Attempt to acquire MariaDB advisory lock for outbox delivery.
        Returns True if acquired (1), False otherwise (0 or error)."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT GET_LOCK(%s, %s)", (OUTBOX_LOCK_NAME, int(timeout_seconds)))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.error("Failed to acquire advisory lock %s: %s", OUTBOX_LOCK_NAME, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def is_outbox_lock_owned(self, conn) -> bool:
        """Check if this connection owns the MariaDB advisory lock."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID()", (OUTBOX_LOCK_NAME,))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.warning("Lock ownership check failed: %s", exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def release_outbox_lock(self, conn) -> bool:
        """Release the advisory lock on this connection."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.error("Failed to release advisory lock %s: %s", OUTBOX_LOCK_NAME, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def claim_telegram_outbox_event(self, lease_seconds: int = 90, conn=None) -> Optional[Dict]:
        """Atomically claim one due event; expired leases are recoverable after restart.
        If conn is provided (e.g. dedicated S_lock), it is kept open and committed before return."""
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return None
        cursor = None
        try:
            cursor = active_conn.cursor(dictionary=True)
            cursor.execute("START TRANSACTION")
            # An expired final-attempt lease must not be sent again after a crash.
            cursor.execute(
                """UPDATE rdt_telegram_outbox
                   SET status='dead', locked_until=NULL, claim_token=NULL,
                       last_error=COALESCE(last_error, 'DELIVERY_UNKNOWN: delivery lease expired at max attempts')
                   WHERE status='processing' AND locked_until <= UTC_TIMESTAMP()
                     AND attempts >= 8"""
            )
            cursor.execute(
                """SELECT id, trade_id, event_type, payload_json, attempts
                   FROM rdt_telegram_outbox
                   WHERE attempts < 8 AND (
                       (status='pending' AND next_attempt_at <= UTC_TIMESTAMP())
                       OR (status='processing' AND locked_until <= UTC_TIMESTAMP())
                   )
                   ORDER BY next_attempt_at, id LIMIT 1 FOR UPDATE"""
            )
            event = cursor.fetchone()
            if not event:
                active_conn.commit()
                return None
            try:
                payload = json.loads(event.get('payload_json') or '{}')
            except (TypeError, ValueError) as exc:
                cursor.execute(
                    """UPDATE rdt_telegram_outbox
                       SET status='dead', locked_until=NULL, claim_token=NULL, last_error=%s
                       WHERE id=%s""",
                    (f"invalid payload JSON: {exc}"[:1000], event['id']),
                )
                active_conn.commit()
                return None
            claim_token = uuid.uuid4().hex
            cursor.execute(
                """UPDATE rdt_telegram_outbox
                   SET status='processing', attempts=attempts+1,
                       locked_until=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
                       claim_token=%s, last_error=NULL WHERE id=%s""",
                (max(1, int(lease_seconds)), claim_token, event['id']),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("outbox claim lost before update")
            active_conn.commit()
            event['attempts'] = int(event.get('attempts') or 0) + 1
            event['claim_token'] = claim_token
            event.pop('payload_json', None)
            event['payload'] = payload
            return event
        except Exception as exc:
            try:
                active_conn.rollback()
            except Exception:
                pass
            self.logger.error("Telegram outbox claim failed: %s", exc)
            return None
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def mark_telegram_outbox_sent(self, event_id: int, claim_token: str, conn=None) -> bool:
        """Acknowledge only the still-current, unexpired claim owner."""
        return self._update_telegram_outbox(
            """UPDATE rdt_telegram_outbox
               SET status='sent', sent_at=UTC_TIMESTAMP(), locked_until=NULL,
                   claim_token=NULL, last_error=NULL
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            (event_id, claim_token),
            conn=conn,
        )

    def retry_telegram_outbox_event(self, event_id: int, claim_token: str,
                                    attempts: int, error: str,
                                    max_attempts: int = 8,
                                    conn=None,
                                    delay: Optional[int] = None) -> bool:
        """Release/retry only if this worker still owns an unexpired claim."""
        attempts = max(1, int(attempts))
        dead = attempts >= max_attempts
        if delay is None:
            delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
        else:
            delay = max(1, int(delay))
        return self._update_telegram_outbox(
            """UPDATE rdt_telegram_outbox
               SET status=%s, next_attempt_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
                   locked_until=NULL, claim_token=NULL, last_error=%s
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            ('dead' if dead else 'pending', delay, str(error)[:1000], event_id, claim_token),
            conn=conn,
        )

    def dead_letter_telegram_outbox_event(self, event_id: int, claim_token: str,
                                          error: str, conn=None) -> bool:
        """Permanently transition to dead state if this worker owns unexpired claim."""
        return self._update_telegram_outbox(
            """UPDATE rdt_telegram_outbox
               SET status='dead', locked_until=NULL,
                   claim_token=NULL, last_error=%s
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            (str(error)[:1000], event_id, claim_token),
            conn=conn,
        )

    def _update_telegram_outbox(self, sql: str, params: tuple, conn=None) -> bool:
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False
        cursor = None
        try:
            cursor = active_conn.cursor()
            cursor.execute(sql, params)
            if cursor.rowcount != 1:
                active_conn.rollback()
                return False
            active_conn.commit()
            return True
        except Exception as exc:
            try:
                active_conn.rollback()
            except Exception:
                pass
            self.logger.error("Telegram outbox update failed: %s", exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def enqueue_telegram_outbox_event(
        self,
        event_key: str,
        event_type: str,
        payload: Dict[str, Any],
        trade_id: int = 0,
        conn=None,
    ) -> bool:
        """Enqueue an event into rdt_telegram_outbox for asynchronous delivery by outbox_worker.
        Blocked fail-closed if observation-only mode is active, configuration is missing,
        or outbox enqueuing is unauthorized on the active database connection."""
        enqueue_cfg = getattr(self, "config", None)
        if enqueue_cfg is None or is_observation_only(enqueue_cfg):
            return False
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False
        if not is_outbox_enqueue_authorized(enqueue_cfg, conn=active_conn):
            if own_conn:
                active_conn.close()
            return False
        cursor = None
        try:
            cursor = active_conn.cursor()
            cursor.execute(
                """INSERT INTO rdt_telegram_outbox
                   (trade_id, event_key, event_type, payload_json, status, next_attempt_at)
                   VALUES (%s, %s, %s, %s, 'pending', UTC_TIMESTAMP())""",
                (int(trade_id), str(event_key)[:128], str(event_type)[:16], json.dumps(payload)),
            )
            if own_conn:
                active_conn.commit()
            return True
        except Exception as exc:
            if own_conn and active_conn:
                try:
                    active_conn.rollback()
                except Exception:
                    pass
            self.logger.warning("Failed to enqueue outbox event %s: %s", event_key, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def enqueue_trade_signal_event(
        self,
        event_key: str,
        event_type: str,
        payload: Dict[str, Any],
        conn=None,
        trade_id: int = 0,
    ) -> bool:
        """Enqueue a trade signal event (e.g. shadow entry/exit) into rdt_telegram_outbox.

        Strictly notification-only (Work Order 71778):
        - Never inserts into rdt_trades.
        - Never updates rdt_trades or rdt_strategy_stats.
        - Never requires RETAILBOT2_ALLOW_TRADING=1.
        - Commits only its own outbox insertion.
        - Requires is_shadow_outbox_enqueue_authorized() on the active connection.
        - Uses ON DUPLICATE KEY UPDATE for idempotent delivery.
        """
        if not event_key or not isinstance(event_key, str):
            self.logger.warning("enqueue_trade_signal_event rejected: invalid or empty event_key")
            return False
        if event_type not in ("entry", "exit", "shadow_entry", "shadow_exit"):
            self.logger.warning("enqueue_trade_signal_event rejected: unsupported event_type %r", event_type)
            return False
        if not isinstance(payload, dict):
            self.logger.warning("enqueue_trade_signal_event rejected: payload must be a dict")
            return False

        enqueue_cfg = getattr(self, "config", None)
        if enqueue_cfg is None or is_observation_only(enqueue_cfg):
            return False

        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False

        if not is_shadow_outbox_enqueue_authorized(enqueue_cfg, conn=active_conn):
            if own_conn:
                active_conn.close()
            return False

        cursor = None
        try:
            cursor = active_conn.cursor()
            normalized_event_type = "entry" if "entry" in event_type else "exit"
            cursor.execute(
                """INSERT INTO rdt_telegram_outbox
                   (trade_id, event_key, event_type, payload_json, status, attempts, next_attempt_at)
                   VALUES (%s, %s, %s, %s, 'pending', 0, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)""",
                (
                    int(trade_id),
                    str(event_key)[:128],
                    normalized_event_type,
                    json.dumps(payload, default=str),
                ),
            )
            if own_conn:
                active_conn.commit()
            return True
        except Exception as exc:
            if own_conn and active_conn:
                try:
                    active_conn.rollback()
                except Exception:
                    pass
            self.logger.warning("Failed to enqueue trade signal event %s: %s", event_key, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()


# ═══════════════════════════════════════════════════════════════════
# DATA FETCHER
# ═══════════════════════════════════════════════════════════════════

class DataFetcher:
    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'timeout': 30000,
        })

    def validate_symbols(self) -> List[str]:
        try:
            markets = self.exchange.load_markets()
            valid = [s for s in self.config.symbols if s in markets]
            bad = set(self.config.symbols) - set(valid)
            if bad:
                print(f"⚠️  Removed invalid: {bad}")
            return valid
        except Exception as e:
            print(f"❌ Market load error: {e}")
            return self.config.symbols

    def fetch(self, symbol: str, limit: int = 500) -> Optional[pd.DataFrame]:
        c = self.config
        for attempt in range(c.api_retries):
            try:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol, c.primary_timeframe, limit=limit
                )
                if not ohlcv or len(ohlcv) < 10:
                    return None
                df = pd.DataFrame(
                    ohlcv,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                return df
            except ccxt.RateLimitExceeded:
                wait = c.api_retry_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"⏳ Rate limit {symbol}, wait {wait:.1f}s")
                time.sleep(wait)
            except ccxt.ExchangeError as e:
                print(f"❌ Exchange error {symbol}: {e}")
                return None
            except ccxt.NetworkError as e:
                print(f"🌐 Network error {symbol}: {e}")
                if attempt < c.api_retries - 1:
                    time.sleep(c.api_retry_delay * (attempt + 1))
            except Exception as e:
                print(f"❌ Fetch error {symbol}: {e}")
                if attempt < c.api_retries - 1:
                    time.sleep(c.api_retry_delay)
        return None

    def get_price(self, symbol: str) -> Optional[float]:
        for _ in range(2):
            try:
                t = self.exchange.fetch_ticker(symbol)
                p = float(t.get('last', 0))
                return p if p > 0 else None
            except ccxt.RateLimitExceeded:
                time.sleep(1.5)
            except Exception:
                return None
        return None


# ═══════════════════════════════════════════════════════════════════
# MAIN BOT
# ═══════════════════════════════════════════════════════════════════

class RetailDeathTrapBot:
    def __init__(self, config: BotConfig):
        # Logger
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s │ %(levelname)-7s │ %(message)s',
            handlers=[
                logging.StreamHandler(),
                RotatingFileHandler(
                    'rdt_bot.log',
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5
                )
            ]
        )
        self.logger = logging.getLogger("RDT_Bot")
        self.config = config
        self.mode = SignalMode(config.signal_mode)
        self.observation_only = bool(getattr(config, "observation_only", False) or is_observation_only(config))
        self._shutdown_requested = False

        # Components
        self.fetcher = DataFetcher(config)
        self.engine = RetailStrategyEngine(config)
        self.db = DatabaseManager(config, self.logger)
        self.tg = TelegramAlert(config.telegram_token, config.telegram_chat_id)

        # Validate symbols
        self.symbols = self.fetcher.validate_symbols()

        # State — separate per mode
        # [F10] Track open trades PER MODE separately
        self.active_shadow: Dict[int, Dict] = {}
        self.active_inverse: Dict[int, Dict] = {}

        # Equities
        self.equity_shadow = config.initial_capital
        self.equity_inverse = config.initial_capital

        # Timing
        self.last_scan: Dict[str, float] = {s: 0.0 for s in self.symbols}
        self.last_trade_time: Dict[str, Dict[str, Dict[str, datetime]]] = {
            s: {'shadow': {}, 'inverse': {}}
            for s in self.symbols
        }
        self.strategy_lifecycle: Dict[Tuple[str, str], Dict[str, bool]] = {}
        self._strategy_lifecycle_reload_at = 0.0
        self._strategy_lifecycle_unavailable = False

        self._load_state()
        self._startup_msg()

        # Bot control: trading_enabled gates new-signal scanning only --
        # check_exits() always runs regardless, so pausing never abandons
        # an open position. Reloaded periodically from the DB (see
        # _reload_control()), not just read once at startup, so a toggle
        # from the web dashboard takes effect on this running process
        # without needing a restart.
        self.trading_enabled = False
        self._control_reload_interval = 30  # seconds
        self._last_control_reload = 0.0
        # Explicit safe-list, NOT "every dataclass field" -- BotConfig also
        # holds db_password/telegram_token/db_host etc., which must never
        # be settable via a web API override regardless of what a request
        # sends. Deliberately excludes ema/lookback "shape" params that
        # would corrupt already-accumulated indicator state if changed on
        # a running process (symbols, primary_timeframe, *_period/*_lookback
        # lengths) -- those still require a restart, same as before this
        # feature existed.
        self._tunable_fields = {
            "rsi_oversold", "rsi_overbought", "rsi_zone_width",
            "sr_touch_threshold", "sr_min_touches",
            "bb_std", "bb_proximity_pct",
            "pattern_tolerance", "pattern_min_bars_apart",
            "spike_atr_multiplier",
            "atr_sl_multiplier", "rr_ratio", "risk_per_trade",
            "max_open_trades_per_mode", "max_trades_per_symbol",
            "min_trade_interval_hours",
        }
        self._reload_control(force=True)

    def _reload_control(self, force: bool = False):
        now = time.time()
        if not force and (now - self._last_control_reload) < self._control_reload_interval:
            return
        self._last_control_reload = now

        was_enabled = getattr(self, "trading_enabled", False)
        if getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None)):
            self.trading_enabled = False
            return

        trading_authorized = is_trading_authorized(getattr(self, "config", None))
        if not trading_authorized:
            self.trading_enabled = False

        control = self.db.get_bot_control()
        if not control.get('available', True):
            self.trading_enabled = False
            if was_enabled:
                self.logger.warning("⚠️  bot_control state unavailable; failing closed with trading PAUSED")
                self.notify_operational("⚠️ <b>Trading PAUSED</b> (bot_control state unavailable)", event_key=f"control_err:{int(time.time()*1000)}")
            return

        db_enabled = bool(control.get('enabled', False))
        if not trading_authorized:
            self.trading_enabled = False
            if db_enabled:
                self.logger.warning(
                    "⚠️ RetailBot2 trading is disabled (bot_control has enabled=True in database, "
                    "but independent explicit authorization RETAILBOT2_ALLOW_TRADING=1 is missing)"
                )
            if was_enabled:
                self.logger.warning("⚠️ Trading PAUSED: independent trading authorization was revoked")
                self.notify_operational("⚠️ <b>Trading PAUSED</b> (trading authorization missing)", event_key=f"control_auth:{int(time.time()*1000)}")
            return

        self.trading_enabled = db_enabled
        if was_enabled != self.trading_enabled:
            state_str = "ENABLED" if self.trading_enabled else "PAUSED (existing positions still managed)"
            self.logger.info(f"🎛️  Trading {state_str} via dashboard control")
            self.notify_operational(f"🎛️ <b>Trading {state_str}</b> (via web dashboard)", event_key=f"control:{int(time.time()*1000)}")

        applied = []
        for key, value in (control.get('overrides') or {}).items():
            if key not in self._tunable_fields:
                self.logger.warning(f"⚠️  Ignoring unknown bot_control override key: {key}")
                continue
            current = getattr(self.config, key, None)
            # Coerce to the existing field's type so a value submitted as a
            # JSON string/number from the web form lands as the right
            # Python type (int/float/bool) rather than silently breaking
            # downstream numeric comparisons in the strategy engine.
            try:
                if isinstance(current, bool):
                    new_value = bool(value)
                elif isinstance(current, int) and not isinstance(current, bool):
                    new_value = int(value)
                elif isinstance(current, float):
                    new_value = float(value)
                else:
                    new_value = value
            except (TypeError, ValueError):
                self.logger.warning(f"⚠️  Could not coerce override {key}={value!r} to match existing type, skipping")
                continue
            if current != new_value:
                setattr(self.config, key, new_value)
                applied.append(f"{key}={new_value}")
        if applied:
            self.logger.info(f"🎛️  Applied config overrides: {', '.join(applied)}")

    def _load_state(self):
        rows = self.db.load_open_trades()
        for t in rows:
            record = {
                'id': t['id'],
                'symbol': t['symbol'],
                'side': t['side'],
                'mode': t.get('mode', 'shadow'),
                'entry': t['entry_price'],
                'stop': t['stop_price'],
                'target': t['target_price'],
                'units': t['units'],
                'strategy': t.get('strategy', 'unknown'),
                'telegram_enabled': bool(t.get('telegram_enabled', False)),
            }
            if record['mode'] == 'inverse':
                self.active_inverse[t['id']] = record
            else:
                self.active_shadow[t['id']] = record
            self.logger.info(
                f"📂 Loaded #{t['id']} {t['symbol']} "
                f"{t['side']} [{record['mode']}]"
            )

        # Rebuild equity from realized P&L -- without this, a restart
        # silently resets both equities back to initial_capital, discarding
        # every closed trade's gain/loss (see get_realized_pnl_by_mode()).
        realized = self.db.get_realized_pnl_by_mode()
        if realized:
            self.equity_shadow = self.config.initial_capital + realized.get('shadow', 0.0)
            self.equity_inverse = self.config.initial_capital + realized.get('inverse', 0.0)
            self.logger.info(
                f"💰 Restored equity from DB: shadow=${self.equity_shadow:,.2f} "
                f"inverse=${self.equity_inverse:,.2f}"
            )

        # Rebuild per-symbol cooldown -- without this, a restart resets
        # last_trade_time to datetime.min for every symbol, bypassing
        # min_trade_interval_hours immediately after restart (see
        # get_last_trade_times()).
        last_times = self.db.get_last_trade_times()
        restored = 0
        for symbol, by_mode in last_times.items():
            if symbol not in self.last_trade_time:
                continue
            for mode, by_strategy in by_mode.items():
                for strategy_id, last_entry in by_strategy.items():
                    if last_entry is not None:
                        self.last_trade_time[symbol][mode][strategy_id] = last_entry
                        restored += 1
        if restored:
            self.logger.info(f"⏱️  Restored cooldown state for {restored} symbol/mode pairs")

    def notify_operational(self, message: str, event_key: Optional[str] = None) -> bool:
        """Deliver or queue an operational notification respecting delivery authorization policy.

        1. Suppressed fail-closed in observation mode.
        2. Routes through durable outbox queue (rdt_telegram_outbox) only if outbox enqueue is authorized.
        3. Falls back to direct delivery ONLY if direct delivery is explicitly authorized.
        4. Fails closed (logs locally without network send or DB row insertion) if delivery is not authorized.
        """
        if not message:
            return False

        if getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None)):
            clean = (
                message.replace('<b>', '').replace('</b>', '')
                .replace('<i>', '').replace('</i>', '')
                .replace('<code>', '').replace('</code>', '')
                .replace('\n', ' | ')
            )
            self.logger.info("Operational notification suppressed (observation mode): %s", clean[:160])
            return False

        if event_key is None:
            event_key = f"op:{uuid.uuid4().hex}"

        # 1. Prefer routing through existing outbox architecture if enqueue is authorized
        queued = False
        try:
            if hasattr(self, "db") and self.db:
                queued = self.db.enqueue_telegram_outbox_event(
                    event_key=event_key,
                    event_type="operational",
                    payload={"text": message},
                    trade_id=0,
                )
        except Exception as exc:
            self.logger.warning("Could not enqueue operational notification to outbox: %s", exc)
            queued = False

        if queued:
            self.logger.info("Queued operational notification to outbox [%s]", event_key)
            return True

        # 2. Direct delivery ONLY if explicitly authorized
        if getattr(self.tg, "direct_authorized", False) and getattr(self.tg, "enabled", False):
            return self.tg.send(message)

        # 3. Fail closed: log locally
        clean = (
            message.replace('<b>', '').replace('</b>', '')
            .replace('<i>', '').replace('</i>', '')
            .replace('<code>', '').replace('</code>', '')
            .replace('\n', ' | ')
        )
        self.logger.info("Operational notification not sent (delivery unauthorized): %s", clean[:160])
        return False

    def enqueue_shadow_entry_signal(self, signal: Dict[str, Any], mode: str = "shadow") -> bool:
        """Enqueue an entry notification for an eligible paper/shadow signal without executing a trade.

        Conditions (Work Order 71778):
        - Observation-only mode is not active.
        - Strategy lifecycle setting has telegram_enabled=True.
        - Strategy has paper_enabled=True or evaluation_enabled=True.
        - Stable signal identity is constructed.
        - Active connection is signal_bot_test with shadow enqueue authorized.
        """
        if getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None)):
            return False

        strategy_id = signal.get("strategy", "combined")
        lifecycle = self._get_strategy_lifecycle(strategy_id, mode)
        if not lifecycle.get("telegram_enabled", False):
            return False
        if not lifecycle.get("paper_enabled", False) and not lifecycle.get("evaluation_enabled", False):
            return False

        symbol = str(signal.get("symbol", "")).strip().upper()
        candle_close = str(signal.get("candle_close_time", "")).replace(" ", "_").replace(":", "-")
        side = str(signal.get("side", "")).strip().upper()
        if not symbol or not side:
            return False

        # Stable deterministic event key in shadow namespace
        event_key = f"retailbot2:shadow:entry:{symbol}_{strategy_id}_{mode}_{side}_{candle_close}"[:128]

        equity = self.equity_inverse if mode == "inverse" else self.equity_shadow
        risk_pct = self.config.risk_per_trade * 100
        risk_amt = equity * self.config.risk_per_trade
        risk_pu = abs(float(signal.get("entry", 0.0)) - float(signal.get("stop", 0.0)))
        units = (risk_amt / risk_pu) if risk_pu > 0 else 0.0

        try:
            payload = build_entry_alert_payload(
                signal=signal,
                units=units,
                risk_amt=risk_amt,
                risk_pct=risk_pct,
                mode=mode,
            )
        except Exception as exc:
            self.logger.warning("Failed to build shadow entry alert payload: %s", exc)
            return False

        return self.db.enqueue_trade_signal_event(
            event_key=event_key,
            event_type="entry",
            payload=payload,
            trade_id=0,
        )

    def _startup_msg(self):
        is_obs = getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None))
        obs_badge = "\n👀 <b>OBSERVATION-ONLY MODE</b> (no trades, no outbox enqueues)" if is_obs else ""
        msg = (
            f"🤖 <b>RETAIL DEATH TRAP BOT v2.1</b>{obs_badge}\n"
            f"✅ All 7 Strategies Fixed\n"
            f"✅ Mixed Long/Short Signals\n"
            f"✅ Separate Shadow/Inverse Slots\n\n"
            f"📊 Mode: <b>{self.mode.value.upper()}</b>\n"
            f"📈 Symbols: {len(self.symbols)}\n"
            f"💰 Shadow Capital:  ${self.config.initial_capital:,.2f}\n"
            f"💰 Inverse Capital: ${self.config.initial_capital:,.2f}\n"
            f"🎯 Max trades/mode: {self.config.max_open_trades_per_mode}"
        )
        self.notify_operational(msg, event_key=f"startup:{int(time.time())}")
        self.logger.info("=" * 60)
        mode_label = "OBSERVATION-ONLY" if is_obs else "STARTED"
        self.logger.info(f"RETAIL DEATH TRAP BOT v2.1 — {mode_label}")
        self.logger.info(f"Symbols: {self.symbols}")
        self.logger.info("=" * 60)

    # ──────────────────────────────────────────────────────────────
    # TRADE CONTROL
    # ──────────────────────────────────────────────────────────────

    def _get_strategy_lifecycle(self, strategy_id: str, mode: str) -> Dict[str, bool]:
        # Never allow an unknown strategy/mode pair to inherit the permissive
        # defaults used for registered research strategies. This is a worker-side
        # guard in addition to the API registry, because signals may originate
        # from legacy state or future strategy code.
        registered = {item.value for item in StrategyID}
        supported = (
            (mode == "shadow" and strategy_id in registered)
            or (mode == "inverse" and strategy_id.endswith("_INV") and strategy_id[:-4] in registered)
            or (mode == "shadow" and strategy_id == "COMPOSITE")
            or (mode == "inverse" and strategy_id == "COMPOSITE_INV")
        )
        if not supported:
            return {
                'evaluation_enabled': False,
                'paper_enabled': False,
                'telegram_enabled': False,
                'report_enabled': True,
                'auto_promote_enabled': False,
            }

        now = time.monotonic()
        # Always attempt the first read, even when the OS monotonic clock is
        # less than 15 seconds after boot and the initial reload timestamp is 0.
        # Otherwise a fast-starting worker can temporarily ignore saved Stop &
        # Hide settings and use permissive defaults until the first refresh.
        if (
            not getattr(self, "_strategy_lifecycle_initialized", False)
            or now - self._strategy_lifecycle_reload_at >= 15
        ):
            try:
                rows = self.db.get_strategy_lifecycle_settings()
                self.strategy_lifecycle = {
                    (row['strategy_id'], row['mode']): {
                        key: (
                            False if key == 'auto_promote_enabled'
                            else _lifecycle_flag(row.get(key))
                        ) for key in (
                            'evaluation_enabled', 'paper_enabled', 'telegram_enabled',
                            'report_enabled', 'auto_promote_enabled'
                        )
                    } for row in rows
                }
                self._strategy_lifecycle_unavailable = False
                self._strategy_lifecycle_initialized = True
                self._strategy_lifecycle_reload_at = now
            except Exception as e:
                # A failed refresh must not reuse stale promotion decisions:
                # an operator may have just disabled paper trading or alerts.
                # Retry after the normal refresh interval, but fail closed until
                # a fresh database read succeeds. Exit management still runs.
                self._strategy_lifecycle_unavailable = True
                self._strategy_lifecycle_initialized = True
                self._strategy_lifecycle_reload_at = now
                self.logger.error("Strategy lifecycle reload failed: %s", e)
        # Composite identities exist only for historical reporting. Enforce
        # this in the worker too, rather than relying only on API validation:
        # legacy/manual DB rows must never make them executable.
        if strategy_id in {"COMPOSITE", "COMPOSITE_INV"}:
            saved = self.strategy_lifecycle.get((strategy_id, mode), {})
            return {
                'evaluation_enabled': False,
                'paper_enabled': False,
                'telegram_enabled': False,
                'report_enabled': saved.get('report_enabled', True),
                'auto_promote_enabled': False,
            }
        if getattr(self, "_strategy_lifecycle_unavailable", False):
            return {
                'evaluation_enabled': False, 'paper_enabled': False,
                'telegram_enabled': False, 'report_enabled': True,
                'auto_promote_enabled': False,
            }
        return self.strategy_lifecycle.get((strategy_id, mode), {
            'evaluation_enabled': True, 'paper_enabled': True,
            'telegram_enabled': False, 'report_enabled': True,
            'auto_promote_enabled': False,
        })

    def _can_open(self, symbol: str, mode: str, strategy: str) -> Tuple[bool, str]:
        """Apply mode-wide capacity and strategy-scoped cooldowns."""
        active = self.active_inverse if mode == 'inverse' else self.active_shadow

        # Mode-level cap
        if len(active) >= self.config.max_open_trades_per_mode:
            return False, f"Max {mode} trades ({self.config.max_open_trades_per_mode}) reached"

        # Per-symbol caps are scoped to each strategy and mode so one
        # strategy's research position does not suppress another's.
        sym_count = sum(
            1 for t in active.values()
            if t['symbol'] == symbol and t.get('strategy') == strategy
        )
        if sym_count >= self.config.max_trades_per_symbol:
            return False, f"{symbol} {strategy} already has an open {mode} trade"

        last = self.last_trade_time.get(symbol, {}).get(mode, {}).get(strategy, datetime.min)
        elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds()
        cooldown = self.config.min_trade_interval_hours * 3600
        if elapsed < cooldown:
            remaining = (cooldown - elapsed) / 60
            return False, f"{symbol} {mode} cooldown: {remaining:.0f}m left"

        # DB check
        db_count = self.db.count_open(mode=mode, symbol=symbol, strategy=strategy)
        if db_count >= self.config.max_trades_per_symbol:
            return False, f"{symbol} {strategy} {mode} already open in DB"

        return True, "OK"

    def _open_trade(self, signal: Dict, mode: str) -> Optional[int]:
        if getattr(self, "_shutdown_requested", False):
            self.logger.info("Trade open suppressed because shutdown was requested")
            return None
        if getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None)):
            self.logger.error("❌ RetailDeathTrapBot._open_trade called in observation-only mode; failing closed")
            return None
        if not getattr(self, "trading_enabled", False) or not is_trading_authorized(getattr(self, "config", None)):
            self.logger.error("❌ RetailDeathTrapBot._open_trade called while trading is disabled or unauthorized; failing closed")
            return None
        strategy_id = signal.get('strategy', 'combined')
        lifecycle = self._get_strategy_lifecycle(strategy_id, mode)
        if not lifecycle['paper_enabled']:
            return None
        signal = signal.copy()
        signal['telegram_enabled'] = lifecycle['telegram_enabled']
        can, reason = self._can_open(signal['symbol'], mode, strategy_id)
        if not can:
            self.logger.debug(f"⏸️  [{mode}] Skip {signal['symbol']}: {reason}")
            return None

        equity = self.equity_inverse if mode == 'inverse' else self.equity_shadow
        risk_amt = equity * self.config.risk_per_trade
        risk_pu = abs(signal['entry'] - signal['stop'])
        if risk_pu <= 0:
            return None
        units = risk_amt / risk_pu
        if units <= 0:
            return None

        risk_pct = self.config.risk_per_trade * 100
        try:
            entry_alert = build_entry_alert_payload(
                signal, units, risk_amt, risk_pct, mode=mode
            ) if lifecycle['telegram_enabled'] else None
        except Exception as exc:
            self.logger.warning("Failed to build entry alert payload: %s", exc)
            entry_alert = None
        # Last boundary check: never begin a new persistence operation after
        # SIGTERM/SIGINT has been observed.
        if getattr(self, "_shutdown_requested", False):
            self.logger.info("Trade persistence suppressed because shutdown was requested")
            return None
        tid = self.db.save_trade(signal, units, mode, entry_alert=entry_alert)
        if tid is None:
            return None
        if getattr(self, "_shutdown_requested", False):
            self.logger.warning(
                "Trade #%s persisted before shutdown was observed; skipping in-memory "
                "activation so the committed row can be reloaded on next startup",
                tid,
            )
            return tid

        record = {
            'id': tid,
            'symbol': signal['symbol'],
            'side': signal['side'],
            'mode': mode,
            'entry': signal['entry'],
            'stop': signal['stop'],
            'target': signal['target'],
            'units': units,
            'strategy': strategy_id,
            'strategies_triggered': signal.get('strategies_triggered', []),
            'telegram_enabled': lifecycle['telegram_enabled'],
        }

        if mode == 'inverse':
            self.active_inverse[tid] = record
        else:
            self.active_shadow[tid] = record

        # Update cooldown
        if signal['symbol'] not in self.last_trade_time:
            self.last_trade_time[signal['symbol']] = {'shadow': {}, 'inverse': {}}
        self.last_trade_time[signal['symbol']][mode][strategy_id] = datetime.now(timezone.utc).replace(tzinfo=None)

        self.logger.info(
            f"✅ [{mode.upper()}] #{tid} {signal['symbol']} {signal['side']} "
            f"| Entry: {signal['entry']:.4f} "
            f"| Stop: {signal['stop']:.4f} "
            f"| Confluence: {signal.get('confluence_score', 1)}/7 "
            f"| Strategies: {signal.get('strategies_triggered', [])}"
        )
        return tid

    def _close_trade(self, tid: int, exit_price: float,
                     reason: str, mode: str):
        if getattr(self, "_shutdown_requested", False):
            self.logger.info("Trade close suppressed because shutdown was requested")
            return
        if getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None)):
            self.logger.error(f"❌ RetailDeathTrapBot._close_trade called in observation-only mode for trade #{tid}; failing closed")
            return
        active = self.active_inverse if mode == 'inverse' else self.active_shadow
        trade = active.get(tid)
        if not trade:
            return

        units = trade['units']
        side = trade['side']
        entry_price = trade['entry']

        if getattr(getattr(self, "config", None), "deduct_fees_slippage", False):
            from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT
            from backtest.accounting import calculate_trade_accounting
            accounting = calculate_trade_accounting(
                direction=side,
                quantity=float(units),
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                fee_pct=BACKTEST_FEE_PCT,
                entry_slippage_pct=0.0,
                exit_slippage_pct=BACKTEST_SLIPPAGE_PCT,
                stop_price=float(trade.get('stop')) if trade.get('stop') is not None else None,
                funding_cost=0.0,
            )
            pnl = float(accounting['net_pnl'])
            pnl_pct = float(accounting['return_pct_on_entry_notional'] or 0.0)
            exit_price = float(accounting['exit_exec_price'])
        else:
            # RetailBot2 paper shadow/inverse equity metrics explicitly track gross PnL (zero fees/slippage).
            if side == 'LONG':
                gross_pnl = (exit_price - entry_price) * units
            else:
                gross_pnl = (entry_price - exit_price) * units
            base_value = entry_price * units
            pnl = gross_pnl
            pnl_pct = (gross_pnl / base_value * 100) if base_value > 0 else 0.0

        # Persistence is the commit boundary. Do not mutate in-memory
        # equity or remove the active trade until both DB writes commit.
        try:
            exit_alert = build_exit_alert_payload(
                trade, exit_price, reason, pnl, pnl_pct
            ) if trade.get('telegram_enabled', False) else None
            if exit_alert:
                exit_alert['telegram_enabled'] = True
        except Exception as exc:
            self.logger.warning("Failed to build exit alert payload: %s", exc)
            exit_alert = None
        if getattr(self, "_shutdown_requested", False):
            self.logger.info("Trade close persistence suppressed because shutdown was requested")
            return
        persisted = self.db.close_trade_and_update_stats(
            tid, exit_price, reason, pnl, pnl_pct,
            trade['strategy'], mode, exit_alert=exit_alert
        )
        if not persisted:
            self.logger.error(
                f"❌ [{mode.upper()}] #{tid} close persistence failed; "
                "keeping active trade and equity unchanged"
            )
            return
        if getattr(self, "_shutdown_requested", False):
            self.logger.warning(
                "Trade #%s close committed before shutdown was observed; leaving in-memory "
                "state untouched because the process is terminating",
                tid,
            )
            return

        # Update equity only after the atomic DB transaction commits.
        if mode == 'inverse':
            self.equity_inverse += pnl
        else:
            self.equity_shadow += pnl

        del active[tid]

        result_icon = "✅" if pnl > 0 else "❌"
        pnl_label = "Net P&L" if getattr(getattr(self, "config", None), "deduct_fees_slippage", False) else "Gross P&L"
        self.logger.info(
            f"🛑 [{mode.upper()}] #{tid} {trade['symbol']} closed: "
            f"{reason} {result_icon} | "
            f"{pnl_label}: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
        )

    # ──────────────────────────────────────────────────────────────
    # EXIT MONITOR
    # ──────────────────────────────────────────────────────────────

    def check_exits(self):
        if getattr(self, "_shutdown_requested", False):
            return
        all_active = {**self.active_shadow, **self.active_inverse}
        if not all_active:
            return

        # Batch fetch prices
        symbols_needed = list(set(t['symbol'] for t in all_active.values()))
        prices: Dict[str, float] = {}
        for sym in symbols_needed:
            if getattr(self, "_shutdown_requested", False):
                return
            p = self.fetcher.get_price(sym)
            if getattr(self, "_shutdown_requested", False):
                return
            if p:
                prices[sym] = p
            time.sleep(0.08)

        is_obs = getattr(self, "observation_only", False) or is_observation_only(getattr(self, "config", None))

        # Check shadow trades
        for tid, trade in list(self.active_shadow.items()):
            if getattr(self, "_shutdown_requested", False):
                return
            price = prices.get(trade['symbol'])
            if not price:
                continue
            exit_p, reason = self._check_exit_condition(trade, price)
            if reason:
                if is_obs:
                    self.logger.info(
                        f"🔍 [OBSERVE] Trade #{tid} {trade['symbol']} [SHADOW] reached {reason} "
                        f"at {exit_p:.4f} (simulated, no action taken)"
                    )
                else:
                    if getattr(self, "_shutdown_requested", False):
                        return
                    self._close_trade(tid, exit_p, reason, 'shadow')

        # Check inverse trades
        for tid, trade in list(self.active_inverse.items()):
            if getattr(self, "_shutdown_requested", False):
                return
            price = prices.get(trade['symbol'])
            if not price:
                continue
            exit_p, reason = self._check_exit_condition(trade, price)
            if reason:
                if is_obs:
                    self.logger.info(
                        f"🔍 [OBSERVE] Trade #{tid} {trade['symbol']} [INVERSE] reached {reason} "
                        f"at {exit_p:.4f} (simulated, no action taken)"
                    )
                else:
                    if getattr(self, "_shutdown_requested", False):
                        return
                    self._close_trade(tid, exit_p, reason, 'inverse')

    def _check_exit_condition(self, trade: Dict,
                               price: float) -> Tuple[float, str]:
        if trade['side'] == 'LONG':
            if price <= trade['stop']:
                return trade['stop'], 'STOP_LOSS'
            if price >= trade['target']:
                return trade['target'], 'TAKE_PROFIT'
        else:
            if price >= trade['stop']:
                return trade['stop'], 'STOP_LOSS'
            if price <= trade['target']:
                return trade['target'], 'TAKE_PROFIT'
        return price, ''

    # ──────────────────────────────────────────────────────────────
    # SIGNAL SCANNING
    # ──────────────────────────────────────────────────────────────

    def scan(self):
        for symbol in self.symbols:
            if getattr(self, "_shutdown_requested", False):
                return
            try:
                # Quick candle check
                df_check = self.fetcher.fetch(symbol, limit=15)
                if getattr(self, "_shutdown_requested", False):
                    return
                if df_check is None or len(df_check) < 3:
                    continue

                last_candle_ts = df_check.index[-2].timestamp()
                if last_candle_ts <= self.last_scan.get(symbol, 0):
                    continue

                self.last_scan[symbol] = last_candle_ts
                self.logger.info(
                    f"🕯️  New candle {symbol} @ "
                    f"{df_check.index[-2].strftime('%H:%M')}"
                )

                # Full fetch + indicators
                df = self.fetcher.fetch(symbol, limit=500)
                if getattr(self, "_shutdown_requested", False):
                    return
                if df is None or len(df) < 100:
                    continue

                df = self.engine.calculate_indicators(df)
                if getattr(self, "_shutdown_requested", False):
                    return

                # Only evaluate a strategy function when at least one of
                # its mode-specific lifecycle settings allows evaluation.
                enabled_strategies = set()
                for strategy_id in StrategyID:
                    shadow_settings = self._get_strategy_lifecycle(strategy_id.value, 'shadow')
                    inverse_settings = self._get_strategy_lifecycle(f"{strategy_id.value}_INV", 'inverse')
                    if shadow_settings['evaluation_enabled'] or inverse_settings['evaluation_enabled']:
                        enabled_strategies.add(strategy_id.value)

                # Track each enabled strategy independently. Composite signals
                # are intentionally not booked as a contributor's trade.
                if getattr(self, "_shutdown_requested", False):
                    return
                raw = self.engine.run_all(df, symbol, enabled_strategies=enabled_strategies)
                if getattr(self, "_shutdown_requested", False):
                    return
                if not raw:
                    continue

                fired = [s['strategy'] for s in raw]
                directions = [s['side'] for s in raw]
                self.logger.info(
                    f"📡 {symbol}: {len(raw)} independent signals → "
                    f"{list(zip(fired, directions))}"
                )

                for signal in raw:
                    if getattr(self, "_shutdown_requested", False):
                        return
                    if self.mode in (SignalMode.SHADOW, SignalMode.BOTH):
                        settings = self._get_strategy_lifecycle(signal.get('strategy', ''), 'shadow')
                        if settings['evaluation_enabled']:
                            if getattr(self, "_shutdown_requested", False):
                                return
                            if getattr(self, "trading_enabled", False) and is_trading_authorized(getattr(self, "config", None)):
                                self._open_trade(signal, 'shadow')
                            elif os.getenv("RETAILBOT2_SHADOW_ENQUEUE_ENABLED", "").strip() == "1":
                                self.enqueue_shadow_entry_signal(signal, 'shadow')

                    if getattr(self, "_shutdown_requested", False):
                        return
                    if self.mode in (SignalMode.INVERSE, SignalMode.BOTH):
                        inverse_signal = self._invert(signal)
                        settings = self._get_strategy_lifecycle(inverse_signal.get('strategy', ''), 'inverse')
                        if settings['evaluation_enabled']:
                            if getattr(self, "_shutdown_requested", False):
                                return
                            if getattr(self, "trading_enabled", False) and is_trading_authorized(getattr(self, "config", None)):
                                self._open_trade(inverse_signal, 'inverse')
                            elif os.getenv("RETAILBOT2_SHADOW_ENQUEUE_ENABLED", "").strip() == "1":
                                self.enqueue_shadow_entry_signal(inverse_signal, 'inverse')

                time.sleep(0.3)

            except Exception as e:
                self.logger.error(
                    f"❌ Scan error {symbol}: {e}\n"
                    f"{traceback.format_exc()}"
                )

    @staticmethod
    def _invert(signal: Dict) -> Dict:
        """Flip direction, recompute stop/target."""
        inv = signal.copy()
        risk = abs(signal['entry'] - signal['stop'])
        if signal['side'] == 'LONG':
            inv['side'] = 'SHORT'
            inv['stop'] = signal['entry'] + risk
            inv['target'] = signal['entry'] - risk * signal['rr']
        else:
            inv['side'] = 'LONG'
            inv['stop'] = signal['entry'] - risk
            inv['target'] = signal['entry'] + risk * signal['rr']
        inv['strategy'] = signal.get('strategy', '') + '_INV'
        inv['strategies_triggered'] = [
            s + '_INV' for s in signal.get('strategies_triggered', [])
        ]
        inv['reason'] = f"INVERSE: {signal.get('reason', '')}"
        return inv

    # ──────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────

    def _dispatch_telegram_outbox(self, limit: int = 2):
        """Deprecated compatibility stub; delivery belongs exclusively to outbox_worker."""
        logging.getLogger(__name__).warning(
            "In-process Telegram outbox delivery is disabled; run paper.outbox_worker instead"
        )
        return 0

    def run(self):
        self.logger.info("🚀 Bot running — CTRL+C or SIGTERM to stop")
        loop_count = 0
        stats_every = 720  # Every hour at 5s interval

        def _handle_signal(signum, frame):
            sig_name = "SIGTERM" if signum == sys_signal.SIGTERM else "SIGINT"
            self.logger.info(f"🛑 Received {sig_name}; initiating graceful shutdown")
            self._shutdown_requested = True

        old_term = None
        old_int = None
        try:
            old_term = sys_signal.signal(sys_signal.SIGTERM, _handle_signal)
            old_int = sys_signal.signal(sys_signal.SIGINT, _handle_signal)
        except (ValueError, AttributeError):
            pass

        try:
            while not self._shutdown_requested:
                try:
                    loop_count += 1

                    self._reload_control()
                    if self._shutdown_requested:
                        break

                    self.check_exits()
                    if self._shutdown_requested:
                        break

                    if self.trading_enabled and not (getattr(self, "observation_only", False) or is_observation_only(self.config)):
                        self.scan()
                    if self._shutdown_requested:
                        break

                    # Heartbeat every 60s
                    hb_loops = self.config.heartbeat_interval // self.config.exit_check_interval
                    if loop_count % max(hb_loops, 1) == 0:
                        s_open = len(self.active_shadow)
                        i_open = len(self.active_inverse)
                        self.logger.info(
                            f"💓 Shadow: {s_open} open ${self.equity_shadow:,.2f} | "
                            f"Inverse: {i_open} open ${self.equity_inverse:,.2f}"
                        )

                    # Stats report every hour
                    if loop_count % stats_every == 0:
                        stats = self.db.get_stats()
                        stats_msg = self.tg.format_stats(
                            stats, self.equity_shadow, self.equity_inverse
                        )
                        self.notify_operational(stats_msg, event_key=f"stats:{int(time.time())}")

                    # Sliced sleep to respond promptly to shutdown signals
                    sleep_remaining = float(self.config.exit_check_interval)
                    while sleep_remaining > 0 and not self._shutdown_requested:
                        step = min(sleep_remaining, 0.5)
                        time.sleep(step)
                        sleep_remaining -= step

                except KeyboardInterrupt:
                    self.logger.info("🛑 Stopped by user")
                    self._shutdown_requested = True
                    break
                except Exception as e:
                    self.logger.error(f"🚨 Loop error: {e}\n{traceback.format_exc()}")
                    self.notify_operational(f"🚨 Error: {str(e)[:150]}", event_key=f"error:{int(time.time()*1000)}")
                    time.sleep(min(30, self.config.exit_check_interval))
        finally:
            try:
                if old_term is not None:
                    sys_signal.signal(sys_signal.SIGTERM, old_term)
                if old_int is not None:
                    sys_signal.signal(sys_signal.SIGINT, old_int)
            except (ValueError, AttributeError):
                pass

        self.logger.info("🛑 Bot shutdown complete")
        self.notify_operational("🛑 <b>Bot stopped</b>", event_key=f"shutdown:{int(time.time())}")


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("RETAIL DEATH TRAP BOT v2.1")
    print("Fixes: Mixed signals | All 7 strategies | Inverse slots")
    print("=" * 60)

    parser = argparse.ArgumentParser(description="Retail Death Trap Bot v2.1")
    parser.add_argument(
        "--observation-only",
        action="store_true",
        default=False,
        help="Run in read-only observation mode (no trades, no outbox enqueues, memory-only exit monitoring)"
    )
    parser.add_argument(
        "--allow-trading",
        action="store_true",
        default=False,
        help="Explicitly authorize RetailBot2 trade execution (requires bot_control.enabled=1 in DB)"
    )
    args, _ = parser.parse_known_args()
    obs_mode = args.observation_only or os.getenv("RETAILBOT2_OBSERVATION_ONLY", "").strip() == "1"
    allow_trading = args.allow_trading or os.getenv("RETAILBOT2_ALLOW_TRADING", "").strip() == "1"

    config = BotConfig(
        symbols=[
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
            "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
        ],
        signal_mode="both",
        primary_timeframe="15m",
        observation_only=obs_mode,
        allow_trading=allow_trading,
        # RSI wider zones = more triggers
        rsi_oversold=35,
        rsi_overbought=65,
        rsi_zone_width=5,
        # Risk
        atr_sl_multiplier=1.5,
        rr_ratio=2.0,
        risk_per_trade=0.01,
        initial_capital=10000.0,
        # [F10] Separate limits
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=2,
        # ── Infrastructure ──
        db_host=os.getenv('MYSQL_HOST', os.getenv('DB_HOST', '192.168.1.30')),
        db_port=int(os.getenv('MYSQL_PORT', os.getenv('DB_PORT', '3306'))),
        db_user=os.getenv('MYSQL_USER', os.getenv('DB_USER', 'cms')),
        db_password=os.getenv('MYSQL_PASSWORD', os.getenv('DB_PASS', '')),
        db_name=os.getenv('MYSQL_DATABASE', 'crypto_signals'),

        # ── Telegram ──
        telegram_token=os.getenv('TELEGRAM_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', '')),
        telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID', ''),
    )


    bot = RetailDeathTrapBot(config)
    bot.run()


if __name__ == "__main__":
    main()