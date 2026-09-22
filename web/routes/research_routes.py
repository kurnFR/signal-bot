"""
API Router for the retailbot2 (Retail Death Trap Bot) and Screen/screening.py
(Smart Money Volume Detector) visibility panels.

Read-only: this exposes what those two standalone processes have written to
their databases so it can be shown in the dashboard. It does not run either
bot or the screener itself -- those remain separate long-running processes
(paper/retailbot2.py and Screen/screening.py).
"""
import logging
from fastapi import APIRouter, HTTPException

from db.db import (
    get_retailbot2_open_trades, get_retailbot2_recent_trades, get_retailbot2_stats,
    get_recent_smart_money_signals, get_smart_money_stats,
)

router = APIRouter(prefix="/api/research", tags=["research-bots"])
logger = logging.getLogger("web.research_routes")


@router.get("/retailbot2/positions")
def retailbot2_positions(limit: int = 100):
    try:
        return {"positions": get_retailbot2_open_trades(limit=limit)}
    except Exception as e:
        logger.exception("retailbot2_positions failed")
        raise HTTPException(status_code=500, detail=f"Failed to load retailbot2 positions: {e}")


@router.get("/retailbot2/trades")
def retailbot2_trades(limit: int = 50):
    try:
        return {"trades": get_retailbot2_recent_trades(limit=limit)}
    except Exception as e:
        logger.exception("retailbot2_trades failed")
        raise HTTPException(status_code=500, detail=f"Failed to load retailbot2 trades: {e}")


@router.get("/retailbot2/stats")
def retailbot2_stats():
    try:
        return get_retailbot2_stats()
    except Exception as e:
        logger.exception("retailbot2_stats failed")
        raise HTTPException(status_code=500, detail=f"Failed to load retailbot2 stats: {e}")


@router.get("/screener/signals")
def screener_signals(limit: int = 50):
    try:
        return {"signals": get_recent_smart_money_signals(limit=limit)}
    except Exception as e:
        logger.exception("screener_signals failed")
        raise HTTPException(status_code=500, detail=f"Failed to load screener signals: {e}")


@router.get("/screener/stats")
def screener_stats():
    try:
        return get_smart_money_stats()
    except Exception as e:
        logger.exception("screener_stats failed")
        raise HTTPException(status_code=500, detail=f"Failed to load screener stats: {e}")
