"""
Telegram API Routes for status, testing, and alerting diagnostics.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from web.auth import require_admin
from notifications.telegram_notifier import (
    get_telegram_config,
    test_telegram_connection,
    send_telegram_message,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramTestRequest(BaseModel):
    custom_message: Optional[str] = None


@router.get("/status")
def get_status(request: Request):
    cfg = get_telegram_config()
    role = str(getattr(request.state, "user", {}).get("role", "")).lower()
    # The target chat identifier is operationally sensitive; only admins may view it.
    return {
        "configured": cfg["is_configured"],
        "chatId": cfg["chat_id"] if role == "admin" else None,
        "hasToken": bool(cfg["token"]),
    }


@router.post("/test")
def run_test(req: Optional[TelegramTestRequest] = None, admin: dict = Depends(require_admin)):
    res = test_telegram_connection()
    if not res.get("success") and not res.get("bot"):
        raise HTTPException(status_code=400, detail=res.get("error", "Telegram test failed"))
    return res
