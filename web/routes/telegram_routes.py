"""
Telegram API Routes for status, testing, and alerting diagnostics.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import logging
from db.db import get_retailbot2_outbox_counts
from web.auth import require_admin
from notifications.telegram_notifier import (
    get_telegram_config,
    test_telegram_connection,
    send_telegram_message,
    is_telegram_delivery_authorized,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramTestRequest(BaseModel):
    custom_message: Optional[str] = None


@router.get("/status")
def get_status(request: Request):
    cfg = get_telegram_config()
    role = str(getattr(request.state, "user", {}).get("role", "")).lower()
    # The target chat identifier is operationally sensitive; only admins may view it.
    result = {
        "configured": cfg["is_configured"],
        "deliveryAuthorized": is_telegram_delivery_authorized(),
        "chatId": cfg["chat_id"] if role == "admin" else None,
        "hasToken": bool(cfg["token"]),
    }
    if role == "admin":
        try:
            result["outbox"] = get_retailbot2_outbox_counts()
        except Exception:
            logging.getLogger(__name__).exception("RetailBot2 outbox health unavailable")
            result["outbox"] = None
    return result


@router.post("/test")
def run_test(req: Optional[TelegramTestRequest] = None, admin: dict = Depends(require_admin)):
    custom_msg = req.custom_message if req else None
    res = test_telegram_connection(custom_message=custom_msg) if custom_msg is not None else test_telegram_connection()
    if not res.get("success") and not res.get("bot"):
        raise HTTPException(status_code=400, detail=res.get("error", "Telegram test failed"))
    return res
