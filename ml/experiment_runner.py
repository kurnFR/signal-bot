"""End-to-end orchestration for leakage-safe ML strategy experiments.

The runner trains and selects the model on TRAIN/VALIDATION, then evaluates
TEST by invoking the existing strategy simulator with the locked ML filter.
It does not promote models to paper trading and does not mutate the global
strategy registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.strategies import STRATEGIES
from ml.dataset import build_signal_dataset
from ml.evaluation import evaluate_filtered_trades
from ml.experiment import ExperimentConfig
from ml.runner import run_experiment
from ml.strategy import MLSignalFilter


@dataclass(frozen=True)
class EndToEndExperimentResult:
    config: ExperimentConfig
    baseline_trades: int
    dataset_rows: int
    training: Any
    validation_evaluation: dict[str, Any]
    test_evaluation: dict[str, Any]


def _build_locked_filter(strategy_fn, config: ExperimentConfig, training) -> MLSignalFilter:
    """Create an inference-only filter from the already-trained model."""
    ml_filter = MLSignalFilter(
        base_strategy=strategy_fn,
        model_type=config.model_type,
        model_params=config.model_params,
        threshold=training.threshold.threshold,
    )
    ml_filter.model = training.model
    ml_filter.feature_columns = tuple(training.feature_columns)
    ml_filter.model_id = config.experiment_id
    return ml_filter


def _test_start_time(test: pd.DataFrame) -> int:
    if test.empty or "open_time" not in test.columns:
        raise ValueError("test split must contain open_time rows")
    return int(test["open_time"].min())


def run_strategy_experiment(
    feature_df: pd.DataFrame,
    strategy_name: str,
    config: ExperimentConfig,
    *,
    strategy_params: dict[str, Any] | None = None,
    train_end_time: int | None = None,
    validation_end_time: int | None = None,
    min_validation_trades: int = 10,
    threshold_candidates=None,
) -> EndToEndExperimentResult:
    """Train ML, lock its threshold, then run the locked model on real OOS.

    TRAIN/VALIDATION are used for model fitting and threshold selection. TEST
    is executed through the same registered strategy and ``backtest.simulate``
    path as ordinary backtests. The simulator receives the full historical
    frame for indicator warm-up but opens no trade before the TEST boundary.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    if config.base_strategy != strategy_name:
        raise ValueError("config.base_strategy must match strategy_name")
    if "open_time" not in feature_df.columns:
        raise ValueError("feature_df must contain open_time")

    params = dict(strategy_params or config.strategy_params)
    strategy_fn = STRATEGIES[strategy_name]
    trades = strategy_fn(feature_df, params)
    if not trades:
        raise ValueError("strategy produced no completed trades")

    dataset = build_signal_dataset(
        feature_df,
        trades,
        params=params,
        feature_columns=config.feature_columns,
    )
    if dataset.empty:
        raise ValueError("strategy experiment dataset is empty")

    result = run_experiment(
        feature_df,
        trades,
        config,
        train_end_time=train_end_time,
        validation_end_time=validation_end_time,
        min_validation_trades=min_validation_trades,
        threshold_candidates=threshold_candidates,
    )

    train = result.split.train
    validation = result.split.validation
    test = result.split.test
    model = result.training.model
    threshold = result.training.threshold.threshold
    preprocessor = getattr(model, "_signal_bot_preprocessor", None)
    if preprocessor is None:
        raise RuntimeError("trained model is missing its fitted preprocessor")
    imputer, scaler = preprocessor

    def probabilities(frame: pd.DataFrame):
        x = imputer.transform(frame.loc[:, result.training.feature_columns])
        x = scaler.transform(x)
        return model.predict_proba(x)[:, 1]

    # Validation remains a diagnostic subset evaluation. It is not used to
    # claim final performance; threshold selection itself happens in trainer.
    validation_evaluation = evaluate_filtered_trades(
        trades,
        validation,
        probabilities(validation),
        threshold,
    )

    # IMPORTANT: TEST is now executed by the real simulator, rather than by
    # merely selecting already-completed baseline trades. This preserves the
    # simulator's entry/exit/SL/TP/trailing/accounting behavior and lets the
    # ML filter change which signals actually become trades.
    test_start = _test_start_time(test)
    base_test_params = dict(params)
    base_test_params["_simulation_start_open_time"] = test_start
    baseline_test_trades = strategy_fn(feature_df, base_test_params)

    locked_filter = _build_locked_filter(strategy_fn, config, result.training)
    ml_test_params = dict(base_test_params)
    ml_test_params["_ml_signal_filter"] = locked_filter
    ml_test_trades = strategy_fn(feature_df, ml_test_params)

    baseline_metrics = compute_metrics(baseline_test_trades)
    ml_metrics = compute_metrics(ml_test_trades)
    baseline_net_pnl = float(sum(float(t.get("net_pnl", 0.0)) for t in baseline_test_trades))
    ml_net_pnl = float(sum(float(t.get("net_pnl", 0.0)) for t in ml_test_trades))

    test_evaluation = {
        "evaluation_mode": "real_simulator_oos",
        "test_start_open_time": test_start,
        "threshold": threshold,
        "baseline": baseline_metrics,
        "ml_filtered": ml_metrics,
        "baseline_net_pnl": round(baseline_net_pnl, 8),
        "ml_net_pnl": round(ml_net_pnl, 8),
        "delta_net_pnl": round(ml_net_pnl - baseline_net_pnl, 8),
        "delta_expectancy_r": (
            round((ml_metrics["expectancy_r"] or 0.0) - (baseline_metrics["expectancy_r"] or 0.0), 8)
            if ml_metrics["expectancy_r"] is not None and baseline_metrics["expectancy_r"] is not None
            else None
        ),
        "delta_profit_factor": (
            round(ml_metrics["profit_factor"] - baseline_metrics["profit_factor"], 8)
            if isinstance(ml_metrics["profit_factor"], (int, float))
            and isinstance(baseline_metrics["profit_factor"], (int, float))
            and ml_metrics["profit_factor"] != float("inf")
            and baseline_metrics["profit_factor"] != float("inf")
            else None
        ),
        "delta_max_drawdown_r": (
            round(ml_metrics["max_drawdown_r"] - baseline_metrics["max_drawdown_r"], 8)
            if ml_metrics["max_drawdown_r"] is not None and baseline_metrics["max_drawdown_r"] is not None
            else None
        ),
        "trade_count_delta": ml_metrics["total_trades"] - baseline_metrics["total_trades"],
    }

    return EndToEndExperimentResult(
        config=config,
        baseline_trades=len(trades),
        dataset_rows=len(dataset),
        training=result.training,
        validation_evaluation=validation_evaluation,
        test_evaluation=test_evaluation,
    )
