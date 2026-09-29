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
import math

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
    invalid_values = [
        k for k, value in overrides.items()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
    ]
    if invalid_values:
        raise HTTPException(status_code=400, detail=f"Override values must be finite numbers: {', '.join(invalid_values)}")

    bounds = {
        "rsi_oversold": (1, 49), "rsi_overbought": (51, 99), "rsi_zone_width": (0, 20),
        "sr_touch_threshold": (0.00001, 0.1), "sr_min_touches": (1, 20),
        "bb_std": (0.1, 10), "bb_proximity_pct": (0, 0.1),
        "pattern_tolerance": (0.00001, 0.1), "pattern_min_bars_apart": (1, 500),
        "spike_atr_multiplier": (0.1, 20), "atr_sl_multiplier": (0.1, 20),
        "rr_ratio": (0.1, 20), "risk_per_trade": (0.0001, 0.1),
        "max_open_trades_per_mode": (1, 100), "max_trades_per_symbol": (1, 10),
        "min_trade_interval_hours": (0, 168), "rvol_multiplier": (1, 100),
        "divergence_max_price_change": (0, 20), "velocity_threshold": (0.1, 100),
        "max_alerts_per_hour": (1, 1000), "max_alerts_per_symbol_per_hour": (1, 100),
        "alert_cooldown_sec": (0, 86400),
        "min_signal_rvol": (1, 100), "min_signal_velocity": (1, 100),
        "min_quote_volume_24h": (0, 1e12), "min_market_cap_usd": (0, 1e12),
        "min_quality_score": (0, 100),
    }
    for key, value in overrides.items():
        if key in bounds:
            lo, hi = bounds[key]
            if not lo <= float(value) <= hi:
                raise HTTPException(status_code=400, detail=f"{key} must be between {lo} and {hi}")
    return overrides


@router.get("/retailbot2/positions")
def retailbot2_positions(limit: int = Field(100, ge=1, le=200)):
    try:
        return {"positions": get_retailbot2_open_trades(limit=limit)}
    except Exception as e:
        logger.exception("retailbot2_positions failed")
        raise HTTPException(status_code=500, detail=f"Failed to load retailbot2 positions: {e}")


@router.get("/retailbot2/trades")
def retailbot2_trades(limit: int = Field(50, ge=1, le=200)):
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
def screener_signals(limit: int = Field(50, ge=1, le=200)):
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
