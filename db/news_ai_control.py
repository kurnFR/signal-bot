"""Operator controls for the News AI overlay: runtime guardrail overrides and signal dismissal."""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MIN_CONFIDENCE_FLOOR = 50      # below this the LLM output is too noisy to trade
MAX_TRADES_PER_DAY_CEILING = 20
RUNTIME_BOUNDS = {
    "min_confidence": (MIN_CONFIDENCE_FLOOR, 100, int),
    "max_trades_per_day": (0, MAX_TRADES_PER_DAY_CEILING, int),
    "stop_atr_mult": (0.5, 5, float),
    "tp_r_scalp": (0.5, 10, float),
    "tp_r_intraday": (0.5, 10, float),
    "tp_r_swing": (0.5, 10, float),
    "poll_interval_seconds": (30, 3600, int),
    "execution_poll_interval_seconds": (30, 3600, int),
}
_UNSET = object()
_CACHE_TTL_SECONDS = 15.0
_cache: Optional[tuple] = None


def _pool():
    from db.db import get_pool
    return get_pool()


def _defaults() -> Dict[str, int | float]:
    import config

    return {
        "min_confidence": config.NEWS_AI_MIN_CONFIDENCE,
        "max_trades_per_day": config.NEWS_AI_MAX_TRADES_PER_DAY,
        "stop_atr_mult": config.NEWS_AI_STOP_ATR_MULT,
        **{f"tp_r_{horizon}": config.NEWS_AI_TP_R_MULTIPLE[horizon]
           for horizon in ("scalp", "intraday", "swing")},
        "poll_interval_seconds": config.NEWS_AI_POLL_INTERVAL_SECONDS,
        "execution_poll_interval_seconds": config.NEWS_AI_EXECUTION_POLL_INTERVAL_SECONDS,
    }


def validate_settings(min_confidence=_UNSET, max_trades_per_day=_UNSET, **overrides) -> None:
    values = {"min_confidence": min_confidence, "max_trades_per_day": max_trades_per_day, **overrides}
    for key, value in values.items():
        if key not in RUNTIME_BOUNDS:
            raise ValueError(f"Unknown News AI setting: {key}")
        if value is _UNSET or value is None:
            continue
        minimum, maximum, kind = RUNTIME_BOUNDS[key]
        valid_type = type(value) is int if kind is int else type(value) in (int, float)
        if not valid_type or not minimum <= value <= maximum or not math.isfinite(value):
            unit = "an integer" if kind is int else "a number"
            raise ValueError(f"{key} must be {unit} between {minimum} and {maximum}")


def get_settings_detail() -> Dict[str, Any]:
    """Effective values plus the explicit overrides for each field."""
    defaults = _defaults()
    conn = _pool().get_connection()
    try:
        columns = ", ".join(RUNTIME_BOUNDS)
        with conn.cursor(dictionary=True) as cur:
            cur.execute(f"SELECT {columns}, updated_by, updated_at FROM news_ai_settings WHERE id=1")
            row = cur.fetchone() or {}
    finally:
        conn.close()
    overrides = {
        key: kind(row[key])
        for key, (_, _, kind) in RUNTIME_BOUNDS.items() if row.get(key) is not None
    }
    return {**defaults, **overrides, "overrides": overrides,
            "source": "override" if overrides else "default", "defaults": defaults,
            "updated_by": row.get("updated_by"),
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None}


def get_effective_settings(*, use_cache: bool = True) -> Dict[str, int | float]:
    """Values the workers use. The existing config defaults apply on read failure."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        detail = get_settings_detail()
        values = {key: detail[key] for key in RUNTIME_BOUNDS}
    except Exception as exc:
        logger.warning("news_ai_settings read failed (%s); using config defaults", type(exc).__name__)
        values = _defaults()
    _cache = (now, values)
    return values


def set_settings(min_confidence=_UNSET, max_trades_per_day=_UNSET, updated_by: Optional[str] = None, **overrides) -> None:
    validate_settings(min_confidence, max_trades_per_day, **overrides)
    values = {key: value for key, value in {
        "min_confidence": min_confidence, "max_trades_per_day": max_trades_per_day, **overrides,
    }.items() if value is not _UNSET}
    if not values:
        raise ValueError("At least one News AI setting is required")
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    updates = ", ".join(f"{key}=VALUES({key})" for key in values)
    conn = _pool().get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO news_ai_settings (id, {columns}, updated_by)
                    VALUES (1, {placeholders}, %s)
                    ON DUPLICATE KEY UPDATE {updates}, updated_by=VALUES(updated_by)""",
                (*values.values(), (updated_by or "")[:100] or None),
            )
            conn.commit()
    finally:
        conn.close()
    _invalidate()


def reset_settings() -> bool:
    conn = _pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM news_ai_settings WHERE id=1")
        conn.commit()
        removed = cur.rowcount > 0
        cur.close()
    finally:
        conn.close()
    _invalidate()
    return removed


def _invalidate() -> None:
    global _cache
    _cache = None


def dismiss_news_signal(signal_id: int) -> str:
    """Finalize an un-acted signal as dismissed so execution never trades it.

    Returns 'dismissed', 'not_found', 'already_final' or 'in_progress'. Atomic with the
    execution worker's claim: both only act on ``acted_on = FALSE`` with no live claim.
    """
    conn = _pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE news_ai_signals
               SET acted_on = TRUE, processing_at = NULL, skip_reason = 'dismissed_by_operator'
               WHERE id = %s AND acted_on = FALSE
                 AND (processing_at IS NULL OR processing_at < UTC_TIMESTAMP() - INTERVAL 10 MINUTE)""",
            (signal_id,),
        )
        conn.commit()
        if cur.rowcount == 1:
            cur.close()
            return "dismissed"
        cur.execute("SELECT acted_on FROM news_ai_signals WHERE id = %s", (signal_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row is None:
        return "not_found"
    return "already_final" if row[0] else "in_progress"
