"""Operator controls for the News AI overlay: runtime guardrail overrides and signal dismissal."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MIN_CONFIDENCE_FLOOR = 50      # below this the LLM output is too noisy to trade
MAX_TRADES_PER_DAY_CEILING = 20
_CACHE_TTL_SECONDS = 15.0
_cache: Optional[tuple] = None


def _pool():
    from db.db import get_pool
    return get_pool()


def _defaults() -> Dict[str, int]:
    import config
    return {"min_confidence": int(config.NEWS_AI_MIN_CONFIDENCE),
            "max_trades_per_day": int(config.NEWS_AI_MAX_TRADES_PER_DAY)}


def validate_settings(min_confidence: int, max_trades_per_day: int) -> None:
    if not isinstance(min_confidence, int) or isinstance(min_confidence, bool) \
            or not MIN_CONFIDENCE_FLOOR <= min_confidence <= 100:
        raise ValueError(f"min_confidence must be an integer between {MIN_CONFIDENCE_FLOOR} and 100")
    if not isinstance(max_trades_per_day, int) or isinstance(max_trades_per_day, bool) \
            or not 0 <= max_trades_per_day <= MAX_TRADES_PER_DAY_CEILING:
        raise ValueError(f"max_trades_per_day must be an integer between 0 and {MAX_TRADES_PER_DAY_CEILING}")


def get_settings_detail() -> Dict[str, Any]:
    """Effective values plus where they come from (for the admin UI)."""
    defaults = _defaults()
    conn = _pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT min_confidence, max_trades_per_day, updated_by, updated_at FROM news_ai_settings WHERE id=1")
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row:
        return {"min_confidence": int(row["min_confidence"]), "max_trades_per_day": int(row["max_trades_per_day"]),
                "source": "override", "defaults": defaults, "updated_by": row["updated_by"],
                "updated_at": str(row["updated_at"]) if row["updated_at"] else None}
    return {**defaults, "source": "default", "defaults": defaults, "updated_by": None, "updated_at": None}


def get_effective_settings(*, use_cache: bool = True) -> Dict[str, int]:
    """Values the workers use. Fail-open to the config defaults on any error."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        d = get_settings_detail()
        values = {"min_confidence": d["min_confidence"], "max_trades_per_day": d["max_trades_per_day"]}
    except Exception as exc:
        logger.warning("news_ai_settings read failed (%s); using config defaults", type(exc).__name__)
        values = _defaults()
    _cache = (now, values)
    return values


def set_settings(min_confidence: int, max_trades_per_day: int, updated_by: Optional[str]) -> None:
    validate_settings(min_confidence, max_trades_per_day)
    conn = _pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO news_ai_settings (id, min_confidence, max_trades_per_day, updated_by)
               VALUES (1, %s, %s, %s)
               ON DUPLICATE KEY UPDATE min_confidence=VALUES(min_confidence),
                   max_trades_per_day=VALUES(max_trades_per_day), updated_by=VALUES(updated_by)""",
            (min_confidence, max_trades_per_day, (updated_by or "")[:100] or None),
        )
        conn.commit()
        cur.close()
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
