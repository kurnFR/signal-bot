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
    return {
        "database": {
            "status": db.get("status", "error"),
            "tables": db.get("tables", {}),
            "error": "Database unavailable" if db.get("status") != "ok" else None,
        },
        "heartbeats": full.get("heartbeats", []),
        "binance": full.get("binance", {}),
    }
