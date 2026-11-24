"""Read-only platform health for every signed-in user (status bar, collector heartbeats).

The admin-only /api/settings/status carries connection details (host, port, database
name, raw error text). This endpoint returns the same health signals without them.
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional, List, Dict, Any
import json

from web.routes.settings import collect_system_status
from db.db import get_pool, _owned_cursor
from web.auth import require_admin
from web.licensing import get_branding_config, verify_license_key

router = APIRouter(prefix="/api/system", tags=["system"])


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
                "status": heartbeat.get("status", "unknown"),
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
