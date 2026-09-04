"""
API Router for Phase B: Live Paper Trading.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

from paper.engine import (
    get_active_positions, get_paper_trades, get_paper_configs,
    set_paper_config, manual_close_position, sync_and_evaluate_paper_trading,
    get_paper_metrics
)

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


class PaperConfigRequest(BaseModel):
    symbol: str = Field(..., example="ETHUSDT")
    market: str = Field("spot", example="spot")
    timeframe: str = Field("1d", example="1d")
    strategy_name: str = Field(..., example="confluence_ensemble_v1")
    is_active: bool = Field(True)
    allocated_capital: float = Field(5000.0)
    risk_per_trade_pct: float = Field(1.0)


@router.get("/positions")
def list_positions():
    positions = get_active_positions()
    return {"positions": positions}


@router.get("/trades")
def list_trades(limit: int = 50):
    trades = get_paper_trades(limit)
    return {"trades": trades}


@router.get("/metrics")
def get_metrics():
    metrics = get_paper_metrics()
    return {"metrics": metrics}


@router.get("/configs")
def list_configs():
    configs = get_paper_configs()
    return {"configs": configs}


@router.post("/configs")
def update_config(req: PaperConfigRequest):
    res = set_paper_config(
        req.symbol.strip().upper(),
        req.market.strip().lower(),
        req.timeframe.strip(),
        req.strategy_name.strip(),
        req.is_active,
        req.allocated_capital,
        req.risk_per_trade_pct
    )
    return res


@router.post("/positions/{position_id}/close")
def close_position(position_id: int):
    try:
        res = manual_close_position(position_id)
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sync")
def sync_market_tick():
    eval_res = sync_and_evaluate_paper_trading()
    return {"status": "success", "results": eval_res}
