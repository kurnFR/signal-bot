"""End-to-end orchestration for leakage-safe ML strategy experiments.

This module deliberately stops at research/evaluation. It does not promote
models to paper trading and does not mutate the global strategy registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtest.strategies import STRATEGIES
from ml.dataset import build_signal_dataset
from ml.evaluation import evaluate_filtered_trades
from ml.experiment import ExperimentConfig
from ml.runner import run_experiment


@dataclass(frozen=True)
class EndToEndExperimentResult:
    config: ExperimentConfig
    baseline_trades: int
    dataset_rows: int
    training: Any
    validation_evaluation: dict[str, Any]
    test_evaluation: dict[str, Any]


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
    """Generate base trades, train ML, and compare validation/test subsets.

    The caller supplies the already-generated historical feature frame. The
    existing registered strategy is the sole source of baseline trades.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name}")
    if config.base_strategy != strategy_name:
        raise ValueError("config.base_strategy must match strategy_name")

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

    # Dataset rows preserve trade_index, while split frames retain those rows.
    validation_evaluation = evaluate_filtered_trades(
        trades,
        validation,
        probabilities(validation),
        threshold,
    )
    test_evaluation = evaluate_filtered_trades(
        trades,
        test,
        probabilities(test),
        threshold,
    )

    return EndToEndExperimentResult(
        config=config,
        baseline_trades=len(trades),
        dataset_rows=len(dataset),
        training=result.training,
        validation_evaluation=validation_evaluation,
        test_evaluation=test_evaluation,
    )
