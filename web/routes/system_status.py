"""Read-only platform health for every signed-in user (status bar, collector heartbeats).

The admin-only /api/settings/status carries connection details (host, port, database
name, raw error text). This endpoint returns the same health signals without them.
"""
from fastapi import APIRouter

from web.routes.settings import collect_system_status

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
    }
