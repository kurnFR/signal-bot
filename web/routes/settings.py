import os
import time
import requests
from fastapi import APIRouter
from db.db import get_pool
from config import MYSQL_CONFIG, ENABLE_FUTURES, SPOT_KLINE_URL, FUTURES_KLINE_URL

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/status")
def get_system_status():
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
                "lastBeat": str(r["last_beat_time"]),
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

    return {
        "database": {
            "status": db_status,
            "host": MYSQL_CONFIG["host"],
            "port": MYSQL_CONFIG["port"],
            "database": MYSQL_CONFIG["database"],
            "user": MYSQL_CONFIG["user"],
            "tables": table_counts,
            "error": db_error,
        },
        "heartbeats": heartbeats,
        "binance": {
            "spotApi": binance_spot,
            "futuresApi": binance_futures,
            "enableFuturesConfig": ENABLE_FUTURES,
        },
        "system": {
            "timezone": "Asia/Jakarta (WIB UTC+7)",
        }
    }
