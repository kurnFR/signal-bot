"""Runtime pause/resume switches for background workers.

Workers keep running as processes (``run_all.py`` / ``.env`` still decide which
processes exist); this table lets an admin *pause* the work inside a running
process without a restart. No row means enabled, and any database error is
treated as "not paused" (fail-open) so a control-table outage can never silently
stop position management.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# key -> metadata shown in the dashboard. ``heartbeats`` are collector_heartbeat names.
SERVICES: Dict[str, Dict[str, object]] = {
    "paper_engine": {
        "label": "Paper Trading Engine",
        "description": "Evaluates active strategies, opens paper positions and manages SL/TP/trailing exits.",
        "heartbeats": ["paper_engine"],
        "pause_warning": "While paused, open paper positions are NOT monitored: stop-loss, take-profit and trailing exits will not trigger until it is resumed.",
    },
    "news_ai": {
        "label": "News + Economic Calendar AI",
        "description": "News poller, economic calendar poller, AI strategy engine and news execution.",
        "heartbeats": ["news_poller", "econ_calendar_poller", "news_strategy_engine", "news_execution"],
        "pause_warning": "While paused, no new news or calendar events are ingested and no AI signals are generated.",
    },
    "wallet_tracker": {
        "label": "Wallet Tracker",
        "description": "Polls tracked wallets and sends wallet alerts.",
        "heartbeats": [],
        "pause_warning": "While paused, tracked wallets are not polled and no wallet alerts are sent.",
    },
    "collectors": {
        "label": "Market Data Collectors",
        "description": "Kline WebSocket writer and open-interest poller.",
        "heartbeats": ["ws_kline_spot", "ws_kline_futures", "oi_poller"],
        "pause_warning": "While paused, new candles and open-interest data are not stored; gaps need a backfill after resuming.",
    },
}

_CACHE_TTL_SECONDS = 10.0
_cache: Dict[str, tuple] = {}


def _pool():
    from db.db import get_pool
    return get_pool()


def validate_service(service: str) -> str:
    if service not in SERVICES:
        raise ValueError(f"Unknown service '{service}'")
    return service


def get_service_states() -> Dict[str, Dict[str, object]]:
    """Return {service: {enabled, updated_by, updated_at}} for every known service."""
    states = {k: {"enabled": True, "updated_by": None, "updated_at": None} for k in SERVICES}
    conn = _pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT service, enabled, updated_by, updated_at FROM service_control")
        for row in cur.fetchall():
            if row["service"] in states:
                states[row["service"]] = {
                    "enabled": bool(row["enabled"]),
                    "updated_by": row["updated_by"],
                    "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
                }
        cur.close()
    finally:
        conn.close()
    return states


def set_service_enabled(service: str, enabled: bool, updated_by: Optional[str] = None) -> None:
    validate_service(service)
    conn = _pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO service_control (service, enabled, updated_by)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), updated_by=VALUES(updated_by)""",
            (service, 1 if enabled else 0, (updated_by or "")[:100] or None),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    _cache.pop(service, None)


def is_service_paused(service: str, *, use_cache: bool = True) -> bool:
    """True only when an admin explicitly paused ``service``. Fail-open on any error."""
    now = time.monotonic()
    cached = _cache.get(service)
    if use_cache and cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    paused = False
    try:
        conn = _pool().get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT enabled FROM service_control WHERE service=%s", (service,))
            row = cur.fetchone()
            cur.close()
            paused = bool(row) and not bool(row[0])
        finally:
            conn.close()
    except Exception as exc:  # fail-open: never stop work because the control table is unreadable
        logger.warning("service_control read failed for %s (%s); treating as enabled", service, type(exc).__name__)
        paused = False
    _cache[service] = (now, paused)
    return paused


def pause_gate(service: str, heartbeat_name: Optional[str], sleep_seconds: float) -> bool:
    """Blocking helper for poll loops. Returns True when paused (after sleeping).

    Usage: ``if pause_gate("news_ai", "news_poller", interval): continue``
    """
    if not is_service_paused(service):
        return False
    if heartbeat_name:
        try:
            from db.db import heartbeat
            heartbeat(heartbeat_name, detail="paused by operator")
        except Exception:
            logger.debug("paused heartbeat failed", exc_info=True)
    time.sleep(max(1.0, float(sleep_seconds)))
    return True


def list_services_with_heartbeats() -> List[Dict[str, object]]:
    states = get_service_states()
    beats: Dict[str, Dict[str, object]] = {}
    try:
        conn = _pool().get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT collector_name, status, detail, last_beat_time FROM collector_heartbeat")
            for r in cur.fetchall():
                beats[r["collector_name"]] = {
                    "name": r["collector_name"],
                    "status": r["status"],
                    "detail": r["detail"],
                    "lastBeat": str(r["last_beat_time"]) if r["last_beat_time"] else None,
                }
            cur.close()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("heartbeat read failed (%s)", type(exc).__name__)
    out = []
    for key, meta in SERVICES.items():
        st = states[key]
        out.append({
            "service": key,
            "label": meta["label"],
            "description": meta["description"],
            "pauseWarning": meta["pause_warning"],
            "enabled": st["enabled"],
            "updatedBy": st["updated_by"],
            "updatedAt": st["updated_at"],
            "heartbeats": [beats[h] for h in meta["heartbeats"] if h in beats],
        })
    return out
