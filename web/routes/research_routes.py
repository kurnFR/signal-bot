"""
API Router for the retailbot2 (Retail Death Trap Bot) and Screen/screening.py
(Smart Money Volume Detector) visibility panels.

Read-only endpoints don't run either bot or the screener themselves --
those remain separate long-running processes (paper/retailbot2.py and
Screen/screening.py). The control endpoints (enable/disable, parameter
overrides) are a "soft" remote control: they write to a small DB row that
each bot polls on its own (~30-60s interval) and applies to itself. This
repo does NOT give the web server permission to start/stop/kill the OS
processes themselves -- that's a real security/reliability line (process
control from a web request needs its own privilege boundary and failure
handling, and is a different problem from "pause the trading logic"). The
process itself is still your responsibility to keep running (tmux/systemd/
supervisor) -- these toggles only pause/resume what it does once it's up.
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Optional

from db.db import (
    get_retailbot2_open_trades, get_retailbot2_recent_trades, get_retailbot2_stats,
    get_recent_smart_money_signals, get_smart_money_stats,
    get_retailbot2_control, set_retailbot2_control, RETAILBOT2_TUNABLE_FIELDS,
    get_screener_control, set_screener_control, SCREENER_TUNABLE_FIELDS,
)

router = APIRouter(prefix="/api/research", tags=["research-bots"])
logger = logging.getLogger("web.research_routes")


class BotControlRequest(BaseModel):
    enabled: bool
    overrides: Dict[str, float] = Field(default_factory=dict)


def _filter_overrides(overrides: Dict, allowed: set, bot_label: str) -> Dict:
    """Reject anything not on the explicit safe-list *at the API layer*,
    before it's even written to the control table -- defense in depth on
    top of each bot's own filter (see RETAILBOT2_TUNABLE_FIELDS /
    SCREENER_TUNABLE_FIELDS in db/db.py)."""
    rejected = [k for k in overrides if k not in allowed]
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"Not a tunable {bot_label} parameter: {', '.join(rejected)}. Allowed: {sorted(allowed)}",
        )
    return overrides


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


@router.get("/retailbot2/control")
def retailbot2_control_get():
    try:
        control = get_retailbot2_control()
        control["tunable_fields"] = sorted(RETAILBOT2_TUNABLE_FIELDS)
        return control
    except Exception as e:
        logger.exception("retailbot2_control_get failed")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load retailbot2 control (has paper/retailbot2.py been started at least once?): {e}",
        )


@router.post("/retailbot2/control")
def retailbot2_control_set(req: BotControlRequest):
    overrides = _filter_overrides(req.overrides, RETAILBOT2_TUNABLE_FIELDS, "retailbot2")
    try:
        set_retailbot2_control(req.enabled, overrides)
        return {"status": "ok", "note": "Takes effect within ~30s -- retailbot2 polls this table periodically, no restart needed."}
    except Exception as e:
        logger.exception("retailbot2_control_set failed")
        raise HTTPException(status_code=500, detail=f"Failed to update retailbot2 control: {e}")


@router.get("/screener/control")
def screener_control_get():
    try:
        control = get_screener_control()
        control["tunable_fields"] = sorted(SCREENER_TUNABLE_FIELDS)
        return control
    except Exception as e:
        logger.exception("screener_control_get failed")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load screener control (has Screen/screening.py been started at least once?): {e}",
        )


@router.post("/screener/control")
def screener_control_set(req: BotControlRequest):
    overrides = _filter_overrides(req.overrides, SCREENER_TUNABLE_FIELDS, "screener")
    try:
        set_screener_control(req.enabled, overrides)
        return {"status": "ok", "note": "Takes effect within ~60s -- the screener polls this table on its main loop tick, no restart needed."}
    except Exception as e:
        logger.exception("screener_control_set failed")
        raise HTTPException(status_code=500, detail=f"Failed to update screener control: {e}")
