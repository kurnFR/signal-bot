import logging
import os
import time
import requests
from fastapi import APIRouter
from db.db import get_pool
from web.auth import get_rate_limiter_health
from config import MYSQL_CONFIG, ENABLE_FUTURES, SPOT_KLINE_URL, FUTURES_KLINE_URL

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger(__name__)


def collect_system_status() -> dict:
    """Full status (includes connection details). Callers decide what to expose."""
    # MariaDB check
    db_status = "ok"
    db_error = None
    table_counts = {}
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT table_name, table_rows 
            FROM information_schema.tables 
            WHERE table_schema = %s
        """, (MYSQL_CONFIG["database"],))
        for row in cur.fetchall():
            table_counts[row["table_name"]] = row["table_rows"]

        cur.execute("SELECT collector_name, status, detail, last_beat_time FROM collector_heartbeat")
        heartbeats = [
            {
                "collector": r["collector_name"],
                "status": r["status"],
                "detail": r["detail"],
                "lastBeat": str(r["last_beat_time"]) if r["last_beat_time"] is not None else None,
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
    except Exception as e:
        db_status = "error"
        db_error = str(e)
        heartbeats = []

    # Binance connectivity check
    binance_spot = "unknown"
    binance_futures = "unknown"
    try:
        r = requests.get(SPOT_KLINE_URL, params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1}, timeout=3)
        binance_spot = "online" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception:
        binance_spot = "offline"

    try:
        r = requests.get(FUTURES_KLINE_URL, params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1}, timeout=3)
        binance_futures = "online" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception:
        binance_futures = "offline"

    limiter_health = get_rate_limiter_health()
    return {
        "database": {
            "status": db_status,
            "host": MYSQL_CONFIG["host"],
            "port": MYSQL_CONFIG["port"],
            "database": MYSQL_CONFIG["database"],
            "tables": table_counts,
            "error": db_error,
        },
        "heartbeats": heartbeats,
        "binance": {
            "spotApi": binance_spot,
            "futuresApi": binance_futures,
            "enableFuturesConfig": ENABLE_FUTURES,
        },
        "loginRateLimiter": {
            "mode": limiter_health.get("mode", "unknown"),
            "degraded": bool(limiter_health.get("degraded", True)),
        },
        "system": {
            "timezone": "Asia/Jakarta (WIB UTC+7)",
        }
    }


@router.get("/status")
def get_system_status():
    """Admin-only (see ADMIN_ONLY_PREFIXES): full status including connection details."""
    return collect_system_status()


# ---------------------------------------------------------------------------
# Runtime service control (admin only via ADMIN_ONLY_PREFIXES)
# ---------------------------------------------------------------------------
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from db.service_control import (
    SERVICES,
    list_services_with_heartbeats,
    set_service_enabled,
)
from web.auth import log_audit, require_admin


class ServiceToggleRequest(BaseModel):
    enabled: bool


@router.get("/services")
def list_services():
    """Pause/resume state and heartbeats of every controllable background worker."""
    try:
        return {"services": list_services_with_heartbeats()}
    except Exception as exc:
        logger.error("list_services failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to load service control state")


@router.put("/services/{service}")
def toggle_service(service: str, req: ServiceToggleRequest, request: Request,
                   admin: dict = Depends(require_admin)):
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail="Unknown service")
    try:
        set_service_enabled(service, req.enabled, admin["username"])
    except Exception as exc:
        logger.error("toggle_service failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to update service control")
    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        "SERVICE_ENABLED" if req.enabled else "SERVICE_PAUSED",
        admin["username"], admin["id"], {"service": service, "enabled": req.enabled}, client_ip,
    )
    return {
        "status": "ok",
        "service": service,
        "enabled": req.enabled,
        "note": "Takes effect on the worker's next cycle (typically within one poll interval); no restart needed.",
    }
