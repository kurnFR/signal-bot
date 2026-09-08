"""High-level ML experiment runner.

This adapter connects the point-in-time dataset and leakage-safe trainer while
keeping database/backtest orchestration outside the ML core. Callers provide
already-generated strategy trades and feature data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from ml.dataset import build_signal_dataset
from ml.experiment import ExperimentConfig
from ml.split import TimeSeriesSplit, split_by_fractions, split_by_timestamps
from ml.trainer import TrainingResult, train_experiment


@dataclass(frozen=True)
class MLRunResult:
    dataset_rows: int
    split: TimeSeriesSplit
    training: TrainingResult


def run_experiment(
    feature_df: pd.DataFrame,
    trades: Sequence[Mapping],
    config: ExperimentConfig,
    *,
    train_end_time: int | None = None,
    validation_end_time: int | None = None,
    min_validation_trades: int = 10,
    threshold_candidates=None,
) -> MLRunResult:
    """Build a supervised dataset, split chronologically, and train once.

    Absolute timestamps are preferred for production experiments because they
    make repeated runs comparable. Fractions remain useful for exploratory
    experiments.
    """
    dataset = build_signal_dataset(
        feature_df,
        trades,
        params=config.strategy_params,
        feature_columns=config.feature_columns,
    )
    if dataset.empty:
        raise ValueError("ML experiment dataset is empty")

    if (train_end_time is None) != (validation_end_time is None):
        raise ValueError("train_end_time and validation_end_time must be supplied together")

    if train_end_time is not None:
        split = split_by_timestamps(dataset, train_end_time, validation_end_time)
    else:
        split = split_by_fractions(
            dataset,
            config.train_fraction,
            config.validation_fraction,
        )

    training = train_experiment(
        split.train,
        split.validation,
        split.test,
        config,
        min_validation_trades=min_validation_trades,
        threshold_candidates=threshold_candidates,
    )
    return MLRunResult(len(dataset), split, training)
