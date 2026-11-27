"""
Telegram API Routes for status, testing, and alerting diagnostics.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
import logging
from config import MYSQL_CONFIG
from paper.outbox_store import get_outbox_counts
from web.auth import require_admin
from notifications.telegram_notifier import (
    get_telegram_config,
    get_telegram_outbox_authorization,
    test_telegram_connection,
    send_telegram_message,
    is_telegram_delivery_authorized,
    set_db_telegram_settings,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramTestRequest(BaseModel):
    custom_message: Optional[str] = Field(None, max_length=1000)


class TelegramConfigUpdateRequest(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=256)
    chat_id: Optional[str] = Field(None, max_length=128)
    delivery_authorized: Optional[bool] = None


@router.put("/config")
def update_config(req: TelegramConfigUpdateRequest, request: Request, admin: dict = Depends(require_admin)):
    """Admin-only: securely persist Telegram bot credentials and delivery permission."""
    from web.auth import log_audit
    set_db_telegram_settings(
        bot_token=req.bot_token,
        chat_id=req.chat_id,
        delivery_authorized=req.delivery_authorized,
    )
    client_ip = request.client.host if request.client else "unknown"
    log_audit(
        "TELEGRAM_CONFIG_UPDATE",
        admin["username"],
        admin["id"],
        {"has_token": bool(req.bot_token), "chat_id": req.chat_id, "delivery_authorized": req.delivery_authorized},
        client_ip,
    )
    return get_status(request)


@router.get("/status")
def get_status(request: Request):
    cfg = get_telegram_config()
    role = str(getattr(request.state, "user", {}).get("role", "")).lower()
    # The target chat identifier is operationally sensitive; only admins may view it.
    authorization, blocked_reason = get_telegram_outbox_authorization(MYSQL_CONFIG["database"])
    result = {
        "delivery_authorized": authorization,
        "outbox_blocked_reason": blocked_reason,
        "configured": cfg["is_configured"],
        "deliveryAuthorized": is_telegram_delivery_authorized(),
        "chatId": cfg["chat_id"] if role == "admin" else None,
        "hasToken": bool(cfg["token"]),
    }
    if role == "admin":
        try:
            result["outbox"] = get_outbox_counts()
        except Exception as exc:
            logging.getLogger(__name__).warning("Telegram outbox health unavailable (%s)", type(exc).__name__)
            result["outbox"] = None
    return result


@router.post("/test")
def run_test(req: Optional[TelegramTestRequest] = None, admin: dict = Depends(require_admin)):
    custom_msg = req.custom_message if req else None
    res = test_telegram_connection(custom_message=custom_msg) if custom_msg is not None else test_telegram_connection()
    if not res.get("success") and not res.get("bot"):
        raise HTTPException(status_code=400, detail=res.get("error", "Telegram test failed"))
    return res
