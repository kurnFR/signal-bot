"""Run a DB-backed, research-only ML experiment.

The experiment is leakage-safe: the selected model/threshold is chosen from
TRAIN and execution-aware VALIDATION only; the locked winner is evaluated once
on untouched TEST. Target definition is explicit and recorded in the result.
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

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backtest.params import BASE_PARAMS
from backtest.strategies import STRATEGIES
from db.db import fetch_ohlcv_with_features_df
from ml.dataset import SHARED_FEATURE_COLUMNS, TARGET_TYPES, build_signal_dataset
from ml.execution_tournament import run_execution_aware_tournament
from ml.tournament import build_candidates

logger = logging.getLogger("ml_real_experiment")
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_MARKET = "spot"
DEFAULT_TIMEFRAME = "1h"
DEFAULT_STRATEGY = "trend_ema_v1"
DEFAULT_MODEL = "logistic_regression"
DEFAULT_TARGET = "binary_positive_r"
DEFAULT_THRESHOLDS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
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
    return {"model_type": candidate.model_type, "model_params": dict(candidate.model_params),
            "feature_columns": list(candidate.feature_columns),
            "threshold_candidates": list(candidate.threshold_candidates)}


def _result_payload(result: Any, *, dataset_rows: int, market_rows: int) -> dict[str, Any]:
    experiment = result.experiment
    ranked = [{"candidate": _candidate_to_dict(score.candidate),
               "validation_total_outcome_r": score.validation_net_pnl_r,
               "validation_profit_factor": score.validation_profit_factor,
               "validation_max_drawdown_r": score.validation_max_drawdown_r,
               "validation_trade_count": score.validation_trade_count}
              for score in result.ranked_validation]
    return {"status": experiment.status, "dataset_rows": dataset_rows, "market_rows": market_rows,
            "experiment": asdict(experiment), "winner": _candidate_to_dict(result.winner),
            "ranked_validation": ranked,
            "validation_by_candidate": [{
                "candidate_index": item["candidate_index"],
                "candidate": _candidate_to_dict(item["candidate"]),
                "selected_threshold": item["selected_threshold"],
                "selected_metrics": dict(item["selected_metrics"]) if item["selected_metrics"] is not None else None,
                "probability_diagnostics": dict(item["probability_diagnostics"]),
                "threshold_results": [dict(row) for row in item["threshold_results"]],
            } for item in result.validation_by_candidate]}


def run_real_experiment(*, symbol: str = DEFAULT_SYMBOL, market: str = DEFAULT_MARKET,
                        timeframe: str = DEFAULT_TIMEFRAME, strategy_name: str = DEFAULT_STRATEGY,
                        model_type: str = DEFAULT_MODEL, target_type: str = DEFAULT_TARGET,
                        output_dir: str = DEFAULT_OUTPUT_DIR, min_validation_trades: int = 10,
                        seed: int = 42) -> Path:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy_name!r}. Available: {', '.join(sorted(STRATEGIES))}")
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {TARGET_TYPES}")

    market_df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=False)
    if market_df.empty or len(market_df) < 200:
        raise RuntimeError(f"Insufficient historical market rows: {len(market_df)}")

    strategy_fn = STRATEGIES[strategy_name]
    params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)
    baseline_trades = strategy_fn(market_df, params)
    if not baseline_trades:
        raise RuntimeError(f"Base strategy {strategy_name!r} produced no completed trades")

    dataset = build_signal_dataset(market_df, baseline_trades, params=params,
                                   feature_columns=SHARED_FEATURE_COLUMNS, target_type=target_type)
    if dataset.empty:
        raise RuntimeError("ML dataset is empty")

    if model_type in {"all", "tournament"}:
        from ml.tournament import get_expanded_model_grid
        grid = get_expanded_model_grid()
    else:
        grid = [(model_type, {})]

    candidates = build_candidates(model_grid=grid, feature_sets=[SHARED_FEATURE_COLUMNS],
                                  thresholds=DEFAULT_THRESHOLDS)
    experiment_id = f"real-{symbol.lower()}-{market.lower()}-{timeframe}-{strategy_name}-{model_type}-{target_type}"
    result = run_execution_aware_tournament(
        market_df, dataset, candidates, strategy_fn=strategy_fn, base_params=params,
        experiment_id=experiment_id, symbol=symbol, timeframe=timeframe, base_strategy=strategy_name,
        train_fraction=0.60, validation_fraction=0.20, min_validation_trades=min_validation_trades,
        max_candidates=max(50, len(candidates)), seed=seed, threshold_candidates=DEFAULT_THRESHOLDS, target_type=target_type)

    payload = _result_payload(result, dataset_rows=len(dataset), market_rows=len(market_df))
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result_path = destination / f"{experiment_id}.json"
    result_path.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
    logger.info("Research result saved to %s", result_path)

    # Persist model artifact for paper trading integration
    try:
        from ml.artifacts import ModelArtifact
        from ml.model_store import ModelStore
        models_dir = Path("ml_models")
        models_dir.mkdir(parents=True, exist_ok=True)
        is_eligible = bool(result.experiment.ml_test_metrics.get("total_outcome_r", 0) > 0)
        artifact = ModelArtifact(
            model_id=experiment_id,
            experiment_id=experiment_id,
            model_type=result.winner.model_type,
            base_strategy=strategy_name,
            feature_columns=result.winner.feature_columns,
            strategy_params=params,
            model_params=result.winner.model_params,
            threshold=result.experiment.threshold,
            train_metrics={},
            validation_metrics=dict(result.experiment.ml_validation_metrics),
            test_metrics=dict(result.experiment.ml_test_metrics),
            eligible_for_paper=is_eligible,
        )
        store = ModelStore(models_dir)
        try:
            store.save(artifact, result.model)
            logger.info("Persisted model artifact %s for paper trading", experiment_id)
        except FileExistsError:
            import joblib
            joblib.dump(result.model, models_dir / f"{experiment_id}.joblib")
            artifact.save_manifest(models_dir)
    except Exception as exc:
        logger.warning("Could not persist model artifact: %s", exc)

    return result_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--strategy", dest="strategy_name", default=DEFAULT_STRATEGY)
    parser.add_argument("--model", dest="model_type", default=DEFAULT_MODEL,
                        help="Model type: logistic_regression, random_forest, hist_gradient_boosting, or all/tournament")
    parser.add_argument("--target", dest="target_type", choices=TARGET_TYPES, default=DEFAULT_TARGET)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-validation-trades", type=int, default=10,
                        help="Minimum validation trades required for candidate evaluation (default: 10)")
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args()
    try:
        result_path = run_real_experiment(symbol=args.symbol, market=args.market, timeframe=args.timeframe,
                                          strategy_name=args.strategy_name, model_type=args.model_type,
                                          target_type=args.target_type, output_dir=args.output_dir,
                                          min_validation_trades=args.min_validation_trades, seed=args.seed)
    except Exception as exc:
        logger.error("ML research experiment failed: %s", exc)
        return 1
    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
