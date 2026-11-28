"""
API Router for the Paper Trading research archive and
Screen/screening.py (Smart Money Volume Detector) visibility panels.

The RetailBot2 worker is retired and migration 041
(`db/migrate_remove_rdt_tables.sql`) drops its `rdt_*` tables for good.
The `/retailbot2/*` GET routes below now serve the live Paper Trading
ledger instead: open positions from `paper_positions`, closed trades and
per-strategy stats from `paper_trades`, with the per-deployment
`paper_configs.report_enabled` flag as the visibility filter. The URLs
are unchanged so the UI change is minimal; `mode` in every response is
now always `"paper"` (paper trading has no shadow/inverse modes).

The former `GET /retailbot2/strategies` and `GET /retailbot2/control`
routes are deleted with this PR, together with their UI calls: the
lifecycle flags they served now live in `paper_configs` (per symbol /
market / timeframe / strategy) and are managed by the Paper Trading UI,
and `bot_control` had no live reader left (the worker that polled it is
deleted). The former POST `/retailbot2/strategies` and
POST `/retailbot2/control` lifecycle writes were retired with the worker
-- there is no process left to poll those tables, so writing them could
only mislead the UI into showing controls as live. The Screener control
endpoints are unaffected: the screener is still a live process that polls
its own control table.
"""
import csv
import io
import logging
import math
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db.db import (
    PAPER_ARCHIVE_COLUMNS,
    get_paper_open_positions, get_paper_recent_trades, get_paper_trade_stats,
    get_paper_archive_summary, iter_paper_archive_batches,
    get_recent_smart_money_signals, get_smart_money_stats,
    get_screener_control, set_screener_control, get_screener_status, SCREENER_TUNABLE_FIELDS,
)

router = APIRouter(prefix="/api/research", tags=["research-bots"])
logger = logging.getLogger("web.research_routes")


class BotControlRequest(BaseModel):
    enabled: bool
    overrides: Dict[str, Any] = Field(default_factory=dict)


def _filter_overrides(overrides: Dict, allowed: set, bot_label: str) -> Dict:
    """Reject anything not on the explicit safe-list *at the API layer*,
    before it's even written to the control table -- defense in depth on
    top of the screener's own filter (see SCREENER_TUNABLE_FIELDS in
    db/db.py)."""
    rejected = [k for k in overrides if k not in allowed]
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"Not a tunable {bot_label} parameter: {', '.join(rejected)}. Allowed: {sorted(allowed)}",
        )
    boolean_fields = {
        "enable_divergence_detection",
        "enable_velocity_detection",
        "require_confluence",
    }
    invalid_values = []
    for key, value in overrides.items():
        if key in boolean_fields:
            if not isinstance(value, bool):
                invalid_values.append(key)
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            invalid_values.append(key)
    if invalid_values:
        raise HTTPException(status_code=400, detail=f"Override values must be valid booleans or finite numbers: {', '.join(invalid_values)}")

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
        "min_alert_quality_score": (0, 100),
        "min_alert_quote_volume": (0, 1e12),
        "max_signal_age_sec": (1, 86400),
        "expired_signal_retention_days": (1, 3650),
        "expired_signal_cleanup_interval_days": (1, 365),
    }
    for key, value in overrides.items():
        if key in bounds:
            lo, hi = bounds[key]
            if not lo <= float(value) <= hi:
                raise HTTPException(status_code=400, detail=f"{key} must be between {lo} and {hi}")
    return overrides


@router.get("/retailbot2/positions")
def retailbot2_positions(limit: int = Query(100, ge=1, le=200)):
    try:
        return {"positions": get_paper_open_positions(limit=limit)}
    except Exception as e:
        logger.error("retailbot2_positions failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load paper positions")


@router.get("/retailbot2/trades")
def retailbot2_trades(limit: int = Query(50, ge=1, le=200)):
    try:
        return {"trades": get_paper_recent_trades(limit=limit)}
    except Exception as e:
        logger.error("retailbot2_trades failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load paper trades")


@router.get("/retailbot2/stats")
def retailbot2_stats():
    try:
        return get_paper_trade_stats()
    except Exception as e:
        logger.error("retailbot2_stats failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load paper trade stats")


def _stream_archive_csv(dataset: str, batch_size: int = 500):
    columns = PAPER_ARCHIVE_COLUMNS[dataset]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    yield buf.getvalue().encode("utf-8")
    buf.seek(0)
    buf.truncate(0)

    for batch in iter_paper_archive_batches(dataset, batch_size=batch_size):
        for row in batch:
            writer.writerow(["" if val is None else str(val) for val in row])
        chunk = buf.getvalue()
        if chunk:
            yield chunk.encode("utf-8")
        buf.seek(0)
        buf.truncate(0)


@router.get("/retailbot2/archive/summary")
def retailbot2_archive_summary():
    try:
        summary = get_paper_archive_summary()
        return {
            "tradesCount": int(summary["trades_count"]),
            "statsCount": int(summary["stats_count"]),
        }
    except Exception as e:
        logger.error("retailbot2_archive_summary failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load paper archive summary")


@router.get("/retailbot2/archive/trades.csv")
def retailbot2_archive_trades_csv():
    return StreamingResponse(
        _stream_archive_csv("paper_trades"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="paper_trades_archive.csv"'},
    )


@router.get("/retailbot2/archive/stats.csv")
def retailbot2_archive_stats_csv():
    return StreamingResponse(
        _stream_archive_csv("paper_strategy_stats"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="paper_strategy_stats_archive.csv"'},
    )


@router.get("/screener/signals")
def screener_signals(limit: int = Query(50, ge=1, le=200)):
    try:
        return {"signals": get_recent_smart_money_signals(limit=limit)}
    except Exception as e:
        logger.error("screener_signals failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load screener signals")


@router.get("/screener/stats")
def screener_stats():
    try:
        return get_smart_money_stats()
    except Exception as e:
        logger.error("screener_stats failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to load screener stats")


@router.get("/screener/status")
def screener_status():
    try:
        # Screener control and heartbeat state are migration-managed. Do not
        # report a synthetic enabled state when the schema/database is unavailable.
        control = get_screener_control()
        status = get_screener_status()
        status["enabled"] = bool(control.get("enabled", True))
        status["timeframe"] = status.get("timeframe") or os.getenv("TIMEFRAME", "1m")
        status["last_signal_at"] = status.get("last_signal_at")
        return status
    except Exception as e:
        logger.error("screener_status failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Failed to load screener status; verify database connectivity and run 'python -m db.migrate'",
        )


@router.get("/screener/control")
def screener_control_get():
    try:
        control = get_screener_control()
        control["tunable_fields"] = sorted(SCREENER_TUNABLE_FIELDS)
        return control
    except Exception as e:
        logger.error("screener_control_get failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Failed to load screener control; verify the screener worker has initialized its control state",
        )


@router.post("/screener/control")
def screener_control_set(req: BotControlRequest):
    overrides = _filter_overrides(req.overrides, SCREENER_TUNABLE_FIELDS, "screener")
    try:
        set_screener_control(req.enabled, overrides)
        return {"status": "ok", "note": "Takes effect within ~60s -- the screener polls this table on its main loop tick, no restart needed."}
    except Exception as e:
        logger.error("screener_control_set failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to update screener control")
