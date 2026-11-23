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


@router.get("/audit-logs")
def get_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    admin: dict = Depends(require_admin),
):
    """Admin-only: query immutable operator security and trading audit logs."""
    pool = get_pool()
    conn = pool.get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        conditions = []
        params = []
        if action:
            conditions.append("action = %s")
            params.append(action)
        if username:
            conditions.append("username = %s")
            params.append(username)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cur.execute(f"SELECT COUNT(*) as total FROM audit_logs {where_clause}", tuple(params))
        total = cur.fetchone()["total"]

        cur.execute(
            f"SELECT id, user_id, username, action, details, ip_address, created_at "
            f"FROM audit_logs {where_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
            tuple(params + [limit, offset]),
        )
        rows = cur.fetchall()
        logs = []
        for r in rows:
            details = None
            if r.get("details"):
                try:
                    details = json.loads(r["details"])
                except Exception:
                    details = r["details"]
            logs.append({
                "id": r["id"],
                "userId": r["user_id"],
                "username": r["username"],
                "action": r["action"],
                "details": details,
                "ipAddress": r["ip_address"],
                "createdAt": str(r["created_at"]) if r.get("created_at") else None,
            })

        return {"logs": logs, "total": total, "limit": limit, "offset": offset}
