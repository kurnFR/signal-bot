"""
API Router for the News AI Overlay strategy's visibility panel.

Mostly read-only: this exposes what the Phase 1-3 pollers have collected/evaluated/
acted on (news_events, news_ai_signals) so it can be shown in the dashboard.
It does not trigger any polling, AI calls, or trades itself -- those remain
collectors/news_poller.py, collectors/econ_calendar_poller.py,
ai/news_strategy_engine.py and ai/news_execution.py, run as separate
long-running processes. See docs/NEWS_AI.md.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from db.db import get_recent_news_signals, get_recent_news_events, get_news_overlay_stats
from db.news_ai_control import (
    dismiss_news_signal, get_settings_detail, reset_settings, set_settings,
)
from web.auth import log_audit, require_admin

router = APIRouter(prefix="/api/news", tags=["news-ai-overlay"])
logger = logging.getLogger("web.news_routes")


@router.get("/stats")
def news_stats():
    try:
        return get_news_overlay_stats()
    except Exception as e:
        logger.error("news_stats failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load News AI stats")


@router.get("/signals")
def news_signals(limit: int = 50):
    try:
        return {"signals": get_recent_news_signals(limit=limit)}
    except Exception as e:
        logger.error("news_signals failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load News AI signals")


@router.get("/events")
def news_events(limit: int = 30):
    try:
        return {"events": get_recent_news_events(limit=limit)}
    except Exception as e:
        logger.error("news_events failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load news events")


class NewsAISettingsRequest(BaseModel):
    min_confidence: int
    max_trades_per_day: int


@router.get("/settings")
def news_settings(admin: dict = Depends(require_admin)):
    """Effective News AI guardrails and whether they come from env defaults or an override."""
    try:
        return get_settings_detail()
    except Exception as e:
        logger.error("news_settings failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load News AI settings")


@router.put("/settings")
def update_news_settings(req: NewsAISettingsRequest, request: Request, admin: dict = Depends(require_admin)):
    try:
        set_settings(req.min_confidence, req.max_trades_per_day, admin["username"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("update_news_settings failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to update News AI settings")
    log_audit("NEWS_AI_SETTINGS_UPDATED", admin["username"], admin["id"],
              {"min_confidence": req.min_confidence, "max_trades_per_day": req.max_trades_per_day},
              request.client.host if request.client else "unknown")
    return get_settings_detail()


@router.delete("/settings")
def reset_news_settings(request: Request, admin: dict = Depends(require_admin)):
    """Remove the override; workers fall back to the env/config defaults."""
    try:
        reset_settings()
    except Exception as e:
        logger.error("reset_news_settings failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to reset News AI settings")
    log_audit("NEWS_AI_SETTINGS_RESET", admin["username"], admin["id"], {},
              request.client.host if request.client else "unknown")
    return get_settings_detail()


@router.post("/signals/{signal_id}/dismiss")
def dismiss_signal(request: Request, signal_id: int = Path(..., gt=0)):
    """Finalize a pending signal as dismissed so the execution worker never trades it."""
    try:
        outcome = dismiss_news_signal(signal_id)
    except Exception as e:
        logger.error("dismiss_signal failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to dismiss signal")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Signal not found")
    if outcome == "already_final":
        raise HTTPException(status_code=409, detail="Signal was already acted on or dismissed")
    if outcome == "in_progress":
        raise HTTPException(status_code=409, detail="Signal is being processed right now. Try again shortly.")
    user = getattr(request.state, "user", {}) or {}
    log_audit("NEWS_SIGNAL_DISMISSED", str(user.get("username", "unknown")), user.get("id"),
              {"signal_id": signal_id}, request.client.host if request.client else "unknown")
    return {"status": "dismissed", "id": signal_id}
