import json
import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from backtest.strategies import STRATEGIES
from backtest.simulate import simulate
from web.routes.backtest import STRATEGY_METADATA

router = APIRouter(prefix="/api/custom-strategy", tags=["custom-strategy"])
logger = logging.getLogger("web.custom_strategy")

CUSTOM_FILE = "/home/BIS/signal-bot/custom_strategies.json"


class RuleCondition(BaseModel):
    indicator: str = Field(..., example="rsi") # rsi, macd_hist, volume_ratio, atr_pct, close
    operator: str = Field(..., example="<")    # <, <=, >, >=, ==
    value: float = Field(..., example=30.0)


class CustomStrategyRequest(BaseModel):
    name: str = Field(..., example="rsi_volume_surge_v1")
    displayName: str = Field(..., example="RSI + Volume Surge")
    description: str = Field("Custom rule-based strategy", example="Fades RSI extremes with volume confirmation")
    longConditions: List[RuleCondition]
    shortConditions: List[RuleCondition]
    slAtrMult: float = Field(1.5, ge=0.5, le=5.0)
    riskRewardRatio: float = Field(2.5, ge=1.0, le=6.0)
    useTrailingStop: bool = Field(False)
    trailActivationR: float = Field(1.0)
    trailDistanceAtrMult: float = Field(1.5)


def _eval_condition(row, prev_row, cond: dict) -> bool:
    ind = cond["indicator"]
    val = cond["value"]
    op = cond["operator"]
    
    current_val = row.get(ind)
    if current_val is None or (isinstance(current_val, float) and current_val != current_val):
        return False

    if op == "<":
        return float(current_val) < float(val)
    elif op == "<=":
        return float(current_val) <= float(val)
    elif op == ">":
        return float(current_val) > float(val)
    elif op == ">=":
        return float(current_val) >= float(val)
    elif op == "==":
        return abs(float(current_val) - float(val)) < 1e-6
    return False


def _create_strategy_runner(def_dict: dict):
    long_conds = def_dict["longConditions"]
    short_conds = def_dict["shortConditions"]
    sl_mult = def_dict.get("slAtrMult", 1.5)
    rr = def_dict.get("riskRewardRatio", 2.5)

    def long_condition(row, prev_row, params):
        if not long_conds:
            return False
        return all(_eval_condition(row, prev_row, c) for c in long_conds)

    def short_condition(row, prev_row, params):
        if not short_conds:
            return False
        return all(_eval_condition(row, prev_row, c) for c in short_conds)

    def stop_target(direction, entry_price, atr, row, params):
        cur_sl_mult = params.get("CUSTOM_SL_ATR_MULT", sl_mult)
        cur_rr = params.get("CUSTOM_RISK_REWARD_RATIO", rr)
        stop_dist = cur_sl_mult * atr
        target_dist = stop_dist * cur_rr
        if direction == "LONG":
            return entry_price - stop_dist, entry_price + target_dist
        else:
            return entry_price + stop_dist, entry_price - target_dist

    def run(df, params):
        return simulate(df, params, long_condition, short_condition, stop_target)

    def run_inverse(df, params):
        # Swap long and short condition
        return simulate(df, params, short_condition, long_condition, stop_target)

    return run, run_inverse


def load_custom_strategies():
    if not os.path.exists(CUSTOM_FILE):
        return
    try:
        with open(CUSTOM_FILE, "r") as f:
            saved = json.load(f)
        for s in saved:
            name = s["name"]
            run_fn, run_inv_fn = _create_strategy_runner(s)
            STRATEGIES[name] = run_fn
            STRATEGIES[f"inverse_{name}"] = run_inv_fn
            STRATEGY_METADATA[name] = {
                "displayName": s.get("displayName", name),
                "category": "Custom Strategy",
                "validated": False,
                "description": s.get("description", ""),
                "params": [
                    {"key": "CUSTOM_SL_ATR_MULT", "label": "Stop-Loss (ATR Multiple)", "type": "float", "default": s.get("slAtrMult", 1.5), "min": 0.5, "max": 5.0, "step": 0.25},
                    {"key": "CUSTOM_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": s.get("riskRewardRatio", 2.5), "min": 1.0, "max": 6.0, "step": 0.5},
                ]
            }
            logger.info(f"Loaded custom strategy: {name}")
    except Exception as e:
        logger.error(f"Failed to load custom strategies: {e}")


@router.post("/create")
def create_custom_strategy(req: CustomStrategyRequest):
    clean_name = req.name.strip().lower().replace(" ", "_")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Strategy name cannot be empty")

    def_dict = req.dict()
    def_dict["name"] = clean_name

    run_fn, run_inv_fn = _create_strategy_runner(def_dict)
    STRATEGIES[clean_name] = run_fn
    STRATEGIES[f"inverse_{clean_name}"] = run_inv_fn

    STRATEGY_METADATA[clean_name] = {
        "displayName": req.displayName or clean_name,
        "category": "Custom Strategy",
        "validated": False,
        "description": req.description,
        "params": [
            {"key": "CUSTOM_SL_ATR_MULT", "label": "Stop-Loss (ATR Multiple)", "type": "float", "default": req.slAtrMult, "min": 0.5, "max": 5.0, "step": 0.25},
            {"key": "CUSTOM_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": req.riskRewardRatio, "min": 1.0, "max": 6.0, "step": 0.5},
        ]
    }

    # Persist to JSON file
    saved = []
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, "r") as f:
                saved = json.load(f)
        except Exception:
            saved = []

    # Update or append
    saved = [s for s in saved if s["name"] != clean_name]
    saved.append(def_dict)
    with open(CUSTOM_FILE, "w") as f:
        json.dump(saved, f, indent=2)

    return {
        "status": "success",
        "name": clean_name,
        "displayName": req.displayName,
        "message": f"Strategy {clean_name} created and registered successfully!"
    }


# Initial load
load_custom_strategies()
