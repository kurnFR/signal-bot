"""
API Router for the News AI Overlay strategy's visibility panel.

Read-only: this exposes what the Phase 1-3 pollers have collected/evaluated/
acted on (news_events, news_ai_signals) so it can be shown in the dashboard.
It does not trigger any polling, AI calls, or trades itself -- those remain
collectors/news_poller.py, collectors/econ_calendar_poller.py,
ai/news_strategy_engine.py and ai/news_execution.py, run as separate
long-running processes. See docs/NEWS_AI.md.
"""
import logging
from fastapi import APIRouter, HTTPException

from db.db import get_recent_news_signals, get_recent_news_events, get_news_overlay_stats

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
