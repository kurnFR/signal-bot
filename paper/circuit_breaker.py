"""
Paper Trading Circuit Breaker & Risk Safety Module.

Provides risk guardrails:
1. Max concurrent open positions limit.
2. Max daily realized PnL loss limit (UTC day).
3. Emergency stop / manual circuit breaker trip.

Risk checks fail closed: if the database cannot verify positions or PnL,
new paper entries are blocked rather than assuming risk is zero.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from db.db import get_pool

logger = logging.getLogger("paper.circuit_breaker")

DEFAULT_MAX_CONCURRENT_POSITIONS = int(os.getenv("PAPER_MAX_CONCURRENT_POSITIONS", "5"))
DEFAULT_MAX_DAILY_LOSS_USD = float(os.getenv("PAPER_MAX_DAILY_LOSS_USD", "500.0"))

_emergency_stop_active = False
_emergency_stop_reason = ""


def trip_emergency_stop(reason: str = "Manual emergency stop triggered") -> None:
    """Activate the global emergency stop circuit breaker."""
    global _emergency_stop_active, _emergency_stop_reason
    _emergency_stop_active = True
    _emergency_stop_reason = reason
    logger.warning("CIRCUIT BREAKER TRIPPED: %s", reason)


def reset_emergency_stop() -> None:
    """Clear the in-memory emergency stop circuit breaker."""
    global _emergency_stop_active, _emergency_stop_reason
    _emergency_stop_active = False
    _emergency_stop_reason = ""
    logger.info("Circuit breaker reset: new trade entries re-enabled")


def get_daily_realized_loss_usd() -> Optional[float]:
    """Return today's net realized PnL, or None if it cannot be verified."""
    today_utc_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_ms = int(today_utc_start.timestamp() * 1000)
    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT COALESCE(SUM(net_pnl), 0.0) AS total_net_pnl
            FROM paper_trades
            WHERE exit_time >= %s
            """,
            (start_ms,),
        )
        row = cur.fetchone()
        if row is None or row.get("total_net_pnl") is None:
            raise RuntimeError("daily realized PnL query returned no usable result")
        return float(row["total_net_pnl"])
    except Exception as exc:
        logger.error("Cannot verify daily realized PnL; risk check unavailable: %s", exc)
        return None
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                logger.debug("Failed to close daily PnL cursor", exc_info=True)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close daily PnL connection", exc_info=True)


def check_circuit_breakers(
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_POSITIONS,
    max_daily_loss: float = DEFAULT_MAX_DAILY_LOSS_USD,
) -> Tuple[bool, str]:
    """Return whether a new paper position is allowed; uncertainty blocks entry."""
    if _emergency_stop_active:
        return False, f"Emergency kill-switch is ACTIVE: {_emergency_stop_reason}"

    conn = None
    cur = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS active_count FROM paper_positions WHERE status = 'OPEN'")
        row = cur.fetchone()
        if row is None or row.get("active_count") is None:
            raise RuntimeError("active position query returned no usable result")
        active_count = int(row["active_count"])
    except Exception as exc:
        logger.error("Cannot verify active positions; blocking new entries: %s", exc)
        return False, "Circuit breaker unavailable: cannot verify active positions; new entries blocked"
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                logger.debug("Failed to close active-position cursor", exc_info=True)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close active-position connection", exc_info=True)

    if active_count >= max_concurrent:
        msg = (
            "Circuit breaker tripped: max concurrent positions reached "
            f"({active_count}/{max_concurrent})"
        )
        logger.warning(msg)
        return False, msg

    today_net_pnl = get_daily_realized_loss_usd()
    if today_net_pnl is None:
        return False, "Circuit breaker unavailable: cannot verify daily realized PnL; new entries blocked"
    if today_net_pnl < -abs(max_daily_loss):
        msg = (
            "Circuit breaker tripped: max daily loss exceeded "
            f"(net PnL: ${today_net_pnl:.2f}, limit: -${abs(max_daily_loss):.2f})"
        )
        logger.warning(msg)
        return False, msg

    return True, "All circuit breakers nominal"


def get_circuit_breaker_status() -> Dict[str, Any]:
    """Return diagnostics without reporting unknown database values as zero."""
    today_net_pnl = get_daily_realized_loss_usd()
    conn = None
    cur = None
    active_count = None
    active_error = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS active_count FROM paper_positions WHERE status = 'OPEN'")
        row = cur.fetchone()
        if row is None or row.get("active_count") is None:
            raise RuntimeError("active position query returned no usable result")
        active_count = int(row["active_count"])
    except Exception as exc:
        active_error = str(exc)
        logger.error("Cannot read active-position status: %s", exc)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                logger.debug("Failed to close status cursor", exc_info=True)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close status connection", exc_info=True)

    if active_error:
        allowed, reason = False, "Circuit breaker unavailable: cannot verify active positions; new entries blocked"
    elif today_net_pnl is None:
        allowed, reason = False, "Circuit breaker unavailable: cannot verify daily realized PnL; new entries blocked"
    else:
        allowed, reason = check_circuit_breakers()

    return {
        "status": "NORMAL" if allowed else "TRIPPED",
        "emergency_stop_active": _emergency_stop_active,
        "emergency_stop_reason": _emergency_stop_reason,
        "active_positions": active_count,
        "max_concurrent_positions": DEFAULT_MAX_CONCURRENT_POSITIONS,
        "today_realized_pnl_usd": round(today_net_pnl, 2) if today_net_pnl is not None else None,
        "max_daily_loss_usd": DEFAULT_MAX_DAILY_LOSS_USD,
        "can_open_new_position": allowed,
        "message": reason,
    }
