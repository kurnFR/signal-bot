"""
Paper Trading Circuit Breaker & Risk Safety Module.

Provides institutional-grade risk guardrails:
1. Max concurrent open positions limit.
2. Max daily loss limit (UTC day).
3. Emergency stop / manual circuit breaker trip.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from db.db import get_pool

logger = logging.getLogger("paper.circuit_breaker")

# Configurable risk thresholds (can be set via environment variables)
DEFAULT_MAX_CONCURRENT_POSITIONS = int(os.getenv("PAPER_MAX_CONCURRENT_POSITIONS", "5"))
DEFAULT_MAX_DAILY_LOSS_USD = float(os.getenv("PAPER_MAX_DAILY_LOSS_USD", "500.0"))

# In-memory emergency kill switch state
_emergency_stop_active = False
_emergency_stop_reason = ""


def trip_emergency_stop(reason: str = "Manual emergency stop triggered") -> None:
    """Activates the global emergency stop circuit breaker."""
    global _emergency_stop_active, _emergency_stop_reason
    _emergency_stop_active = True
    _emergency_stop_reason = reason
    logger.warning("CIRCUIT BREAKER TRIPPED: %s", reason)


def reset_emergency_stop() -> None:
    """Clears the global emergency stop circuit breaker."""
    global _emergency_stop_active, _emergency_stop_reason
    _emergency_stop_active = False
    _emergency_stop_reason = ""
    logger.info("Circuit breaker reset: new trade entries re-enabled")


def get_daily_realized_loss_usd() -> float:
    """Computes today's net realized loss across all closed paper trades (UTC day)."""
    today_utc_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(today_utc_start.timestamp() * 1000)

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT COALESCE(SUM(net_pnl), 0.0) AS total_net_pnl
            FROM paper_trades
            WHERE exit_time >= %s
            """,
            (start_ms,)
        )
        row = cur.fetchone()
        cur.close()
        total_net_pnl = float(row["total_net_pnl"]) if row else 0.0
        return total_net_pnl
    except Exception as exc:
        logger.error("Error querying daily realized PnL: %s", exc)
        return 0.0
    finally:
        conn.close()


def check_circuit_breakers(
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_POSITIONS,
    max_daily_loss: float = DEFAULT_MAX_DAILY_LOSS_USD,
) -> Tuple[bool, str]:
    """Check all circuit breakers before opening a new paper position.

    Returns:
        (allowed: bool, reason: str)
        allowed is True if safe to open position, False if tripped.
    """
    global _emergency_stop_active, _emergency_stop_reason
    if _emergency_stop_active:
        return False, f"Emergency kill-switch is ACTIVE: {_emergency_stop_reason}"

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS active_count FROM paper_positions WHERE status = 'OPEN'")
        row = cur.fetchone()
        cur.close()
        active_count = int(row["active_count"]) if row else 0
    except Exception as exc:
        logger.error("Error querying active position count: %s", exc)
        active_count = 0
    finally:
        conn.close()

    # 1. Concurrent positions gate
    if active_count >= max_concurrent:
        msg = f"Circuit breaker tripped: max concurrent positions reached ({active_count}/{max_concurrent})"
        logger.warning(msg)
        return False, msg

    # 2. Max daily loss gate
    today_net_pnl = get_daily_realized_loss_usd()
    if today_net_pnl < -abs(max_daily_loss):
        msg = f"Circuit breaker tripped: max daily loss exceeded (net PnL: ${today_net_pnl:.2f}, limit: -${abs(max_daily_loss):.2f})"
        logger.warning(msg)
        return False, msg

    return True, "All circuit breakers nominal"


def get_circuit_breaker_status() -> Dict[str, Any]:
    """Returns current diagnostic status of all circuit breakers."""
    today_net_pnl = get_daily_realized_loss_usd()
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS active_count FROM paper_positions WHERE status = 'OPEN'")
        row = cur.fetchone()
        cur.close()
        active_count = int(row["active_count"]) if row else 0
    finally:
        conn.close()

    allowed, reason = check_circuit_breakers()
    return {
        "status": "NORMAL" if allowed else "TRIPPED",
        "emergency_stop_active": _emergency_stop_active,
        "emergency_stop_reason": _emergency_stop_reason,
        "active_positions": active_count,
        "max_concurrent_positions": DEFAULT_MAX_CONCURRENT_POSITIONS,
        "today_realized_pnl_usd": round(today_net_pnl, 2),
        "max_daily_loss_usd": DEFAULT_MAX_DAILY_LOSS_USD,
        "can_open_new_position": allowed,
        "message": reason,
    }
