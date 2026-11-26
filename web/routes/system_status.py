"""Read-only platform health for every signed-in user (status bar, collector heartbeats).

The admin-only /api/settings/status carries connection details (host, port, database
name, raw error text). This endpoint returns the same health signals without them.
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional, List, Dict, Any
import json

from config import ENABLE_FUTURES
from web.routes.settings import collect_system_status
from db.db import get_pool, _owned_cursor
from web.auth import require_admin
from web.licensing import get_branding_config, verify_license_key

router = APIRouter(prefix="/api/system", tags=["system"])


def _display_collector_status(heartbeat: dict) -> str:
    name = heartbeat.get("collector")
    if name in {"ws_funding", "ws_liquidations"}:
        return "retired"
    if not ENABLE_FUTURES and name in {"ws_kline_futures", "oi_poller"}:
        return "disabled"
    return heartbeat.get("status", "unknown")


@router.get("/status")
def get_public_system_status():
    full = collect_system_status()
    db = full.get("database", {})
    limiter = full.get("loginRateLimiter", {})
    return {
        "database": {
            "status": db.get("status", "error"),
            "tables": db.get("tables", {}),
            "error": "Database unavailable" if db.get("status") != "ok" else None,
        },
        "heartbeats": [
            {
                "collector": heartbeat.get("collector"),
                "status": _display_collector_status(heartbeat),
                "lastBeat": heartbeat.get("lastBeat"),
            }
            for heartbeat in full.get("heartbeats", [])
            if isinstance(heartbeat, dict)
        ],
        "binance": full.get("binance", {}),
        "loginRateLimiter": {
            "mode": limiter.get("mode", "unknown"),
            "degraded": bool(limiter.get("degraded", True)),
        },
        "branding": get_branding_config(),
        "license": verify_license_key(),
    }
