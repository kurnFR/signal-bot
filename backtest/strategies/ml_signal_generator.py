"""Standalone ML signal generator strategy adapter.

Unlike MLSignalFilter, this strategy does not call a base strategy. It scores
eligible closed candles directly and emits LONG/SHORT/HOLD decisions.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from backtest.simulate import simulate
from ml.generator_runtime import load_generator_predictor, predict_generator_signals

NAME = "ml_signal_generator_v1"
logger = logging.getLogger("backtest.strategies.ml_signal_generator")


def _long_condition(row, prev_row, params):
    return int(row.get("_ml_generator_decision", 1)) == 2


def _short_condition(row, prev_row, params):
    return int(row.get("_ml_generator_decision", 1)) == 0


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    stop_multiple = float(params.get("ML_GENERATOR_STOP_ATR_MULT", 1.5))
    reward_multiple = float(params.get("ML_GENERATOR_RISK_REWARD_RATIO", 2.0))
    if (
        not math.isfinite(stop_multiple) or stop_multiple <= 0
        or not math.isfinite(reward_multiple) or reward_multiple <= 0
    ):
        raise ValueError("generator stop and reward multiples must be finite and positive")
    distance = float(atr_at_signal) * stop_multiple
    if direction == "LONG":
        return raw_entry_price - distance, raw_entry_price + distance * reward_multiple
    return raw_entry_price + distance, raw_entry_price - distance * reward_multiple


def run(df: pd.DataFrame, params: dict) -> list[dict]:
    """Generate trades directly from model decisions through the canonical simulator."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    model_id = params.get("ml_generator_model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        logger.warning("Generator strategy requires ml_generator_model_id; no signals emitted")
        return []

    models_dir = str(params.get("ML_GENERATOR_MODELS_DIR", "ml_models"))
    require_eligible = bool(params.get("_require_generator_eligible", False))
    try:
        artifact, model = load_generator_predictor(
            model_id, models_dir, require_eligible=require_eligible
        )
        predictions = predict_generator_signals(df, artifact, model)
    except Exception as exc:
        logger.error(
            "Unable to load/evaluate generator model %s (%s); failing closed",
            model_id, type(exc).__name__,
        )
        return []

    # The immutable artifact records the execution assumptions used by its
    # canonical validation/test runs. Reuse them in paper inference so the
    # same model cannot silently trade with different stops, costs, or exits.
    execution = artifact.label_config
    runtime_params = dict(params)
    execution_fields = {
        "stop_atr_multiple": "ML_GENERATOR_STOP_ATR_MULT",
        "risk_reward_ratio": "ML_GENERATOR_RISK_REWARD_RATIO",
        "max_hold_bars": "BACKTEST_MAX_HOLD_BARS",
    }
    for artifact_key, runtime_key in execution_fields.items():
        if artifact_key in execution:
            runtime_params[runtime_key] = execution[artifact_key]

    # Paper accounting uses server-configured costs. Refuse to trade an
    # artifact evaluated under different costs instead of silently changing
    # its economics after promotion.
    try:
        import math
        from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT
        artifact_fee = float(execution["fee_rate"])
        artifact_slippage = float(execution["slippage_rate"])
        if not math.isclose(artifact_fee, BACKTEST_FEE_PCT, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("generator fee assumptions differ from current paper accounting")
        if not math.isclose(artifact_slippage, BACKTEST_SLIPPAGE_PCT, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("generator slippage assumptions differ from current paper accounting")
    except Exception as exc:
        logger.error("Generator %s execution-cost contract mismatch (%s); failing closed", model_id, type(exc).__name__)
        return []

    signal_df = df.copy()
    signal_df["_ml_generator_decision"] = predictions["decision"].to_numpy()
    signal_df["_ml_generator_probability"] = predictions["confidence"].to_numpy()
    signal_df["_ml_generator_p_long"] = predictions["p_long"].to_numpy()
    signal_df["_ml_generator_p_short"] = predictions["p_short"].to_numpy()

    trades = simulate(
        signal_df,
        runtime_params,
        _long_condition,
        _short_condition,
        _stop_target,
    )
    probabilities_by_time = {
        int(signal_df.iloc[index]["open_time"]): predictions.iloc[index]
        for index in range(len(signal_df))
    }
    for trade in trades:
        prediction = probabilities_by_time.get(int(trade["signal_open_time"]))
        trade["max_hold_bars"] = int(runtime_params.get("BACKTEST_MAX_HOLD_BARS", 200))
        if prediction is not None:
            trade["ml_probability"] = float(prediction["confidence"])
            trade["ml_probability_long"] = (
                float(prediction["p_long"]) if pd.notna(prediction["p_long"]) else None
            )
            trade["ml_probability_short"] = (
                float(prediction["p_short"]) if pd.notna(prediction["p_short"]) else None
            )
        trade["ml_generator_model_id"] = model_id
        trade["strategy_name"] = NAME
    return trades
