"""Run the first real historical ML experiment against the local market DB.

This is deliberately research-only. It reads historical market/features data,
builds labels from the existing strategy, runs the execution-aware TRAIN /
VALIDATION tournament, evaluates the locked winner once on TEST, and writes a
JSON result. It never promotes a model or writes paper/live trading state.

Default experiment:
    BTCUSDT / spot / 1h / trend_ema_v1 / logistic_regression / 60-20-20

Usage:
    python3 -m ml.run_real_experiment
    python3 -m ml.run_real_experiment --symbol BTCUSDT --timeframe 1h
    python3 -m ml.run_real_experiment --market spot --output-dir ml_results
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# Allow execution from the repository root without requiring package install.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.params import BASE_PARAMS
from backtest.strategies import STRATEGIES
from db.db import fetch_ohlcv_with_features_df
from ml.dataset import SHARED_FEATURE_COLUMNS, build_signal_dataset
from ml.execution_tournament import run_execution_aware_tournament
from ml.tournament import build_candidates

logger = logging.getLogger("ml_real_experiment")

DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_MARKET = "spot"
DEFAULT_TIMEFRAME = "1h"
DEFAULT_STRATEGY = "trend_ema_v1"
DEFAULT_MODEL = "logistic_regression"
DEFAULT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
DEFAULT_OUTPUT_DIR = "ml_results"


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    return {
        "model_type": candidate.model_type,
        "model_params": dict(candidate.model_params),
        "feature_columns": list(candidate.feature_columns),
        "threshold_candidates": list(candidate.threshold_candidates),
    }


def _result_payload(result: Any, *, dataset_rows: int, market_rows: int) -> dict[str, Any]:
    experiment = result.experiment
    ranked = [
        {
            "candidate": _candidate_to_dict(score.candidate),
            "validation_total_outcome_r": score.validation_net_pnl_r,
            "validation_profit_factor": score.validation_profit_factor,
            "validation_max_drawdown_r": score.validation_max_drawdown_r,
            "validation_trade_count": score.validation_trade_count,
        }
        for score in result.ranked_validation
    ]
    return {
        "status": experiment.status,
        "dataset_rows": dataset_rows,
        "market_rows": market_rows,
        "experiment": asdict(experiment),
        "winner": _candidate_to_dict(result.winner),
        "ranked_validation": ranked,
        "validation_by_candidate": [
            {
                "candidate_index": item["candidate_index"],
                "candidate": _candidate_to_dict(item["candidate"]),
                "selected_threshold": item["selected_threshold"],
                "selected_metrics": dict(item["selected_metrics"]),
                "threshold_results": [dict(row) for row in item["threshold_results"]],
            }
            for item in result.validation_by_candidate
        ],
    }


def run_real_experiment(
    *,
    symbol: str = DEFAULT_SYMBOL,
    market: str = DEFAULT_MARKET,
    timeframe: str = DEFAULT_TIMEFRAME,
    strategy_name: str = DEFAULT_STRATEGY,
    model_type: str = DEFAULT_MODEL,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    seed: int = 42,
) -> Path:
    """Execute one local DB-backed research experiment and save its result."""
    if strategy_name not in STRATEGIES:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"Unknown strategy {strategy_name!r}. Available: {available}")

    logger.info("Loading %s %s %s historical data", symbol, market, timeframe)
    market_df = fetch_ohlcv_with_features_df(
        symbol, market, timeframe, closed_only=True,
        include_funding=False,
    )
    if market_df.empty:
        raise RuntimeError("Database returned no historical OHLCV/features rows")
    if len(market_df) < 200:
        raise RuntimeError(f"Only {len(market_df)} market rows returned; need at least 200")

    strategy_fn = STRATEGIES[strategy_name]
    params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)
    baseline_trades = strategy_fn(market_df, params)
    if not baseline_trades:
        raise RuntimeError(
            f"Base strategy {strategy_name!r} produced no completed trades on the loaded data"
        )

    dataset = build_signal_dataset(
        market_df,
        baseline_trades,
        params=params,
        feature_columns=SHARED_FEATURE_COLUMNS,
    )
    if dataset.empty:
        raise RuntimeError("ML dataset is empty after building it from baseline trades")

    candidate = build_candidates(
        model_grid=[(model_type, {})],
        feature_sets=[SHARED_FEATURE_COLUMNS],
        thresholds=DEFAULT_THRESHOLDS,
    )

    experiment_id = (
        f"real-{symbol.lower()}-{market.lower()}-{timeframe}-"
        f"{strategy_name}-{model_type}"
    )
    result = run_execution_aware_tournament(
        market_df,
        dataset,
        candidate,
        strategy_fn=strategy_fn,
        base_params=params,
        experiment_id=experiment_id,
        symbol=symbol,
        timeframe=timeframe,
        base_strategy=strategy_name,
        train_fraction=0.60,
        validation_fraction=0.20,
        min_validation_trades=10,
        max_candidates=1,
        seed=seed,
        threshold_candidates=DEFAULT_THRESHOLDS,
    )

    payload = _result_payload(result, dataset_rows=len(dataset), market_rows=len(market_df))
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result_path = destination / f"{experiment_id}.json"
    result_path.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")

    validation = result.experiment.ml_validation_metrics
    test = result.experiment.ml_test_metrics
    logger.info(
        "LOCKED winner: model=%s threshold=%.2f validation_trades=%s "
        "validation_R=%.4f validation_PF=%s",
        result.winner.model_type,
        result.experiment.threshold,
        validation.get("total_trades"),
        validation.get("total_outcome_r"),
        validation.get("profit_factor"),
    )
    logger.info(
        "OOS TEST: trades=%s net_pnl=%s total_R=%s PF=%s max_dd_R=%s",
        test.get("total_trades"),
        test.get("net_pnl"),
        test.get("total_outcome_r"),
        test.get("profit_factor"),
        test.get("max_drawdown_r"),
    )
    logger.info("Research result saved to %s", result_path)
    return result_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--strategy", dest="strategy_name", default=DEFAULT_STRATEGY)
    parser.add_argument("--model", dest="model_type", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args()
    try:
        result_path = run_real_experiment(
            symbol=args.symbol,
            market=args.market,
            timeframe=args.timeframe,
            strategy_name=args.strategy_name,
            model_type=args.model_type,
            output_dir=args.output_dir,
            seed=args.seed,
        )
    except Exception as exc:
        logger.error("ML research experiment failed: %s", exc)
        return 1
    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
