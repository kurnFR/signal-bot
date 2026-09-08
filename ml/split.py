"""Chronological dataset splitting for ML experiments.

Random shuffling is deliberately not supported. Boundaries are expressed as
row fractions or absolute timestamps and every split preserves source order.
"""
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TimeSeriesSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def split_by_fractions(
    df: pd.DataFrame,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> TimeSeriesSplit:
    """Split a time-ordered DataFrame into train/validation/test.

    The input must already be ordered by ``open_time``. No rows are shuffled.
    The three fractions must be positive and leave a positive test fraction.
    """
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must be < 1")
    if "open_time" not in df.columns:
        raise ValueError("DataFrame must contain open_time")
    if len(df) < 3:
        raise ValueError("at least 3 rows are required")

    ordered = df.sort_values("open_time", kind="stable").reset_index(drop=True)
    n = len(ordered)
    train_end = int(n * train_fraction)
    validation_end = train_end + int(n * validation_fraction)

    # Guarantee all three partitions contain at least one row.
    train_end = max(1, min(train_end, n - 2))
    validation_end = max(train_end + 1, min(validation_end, n - 1))

    return TimeSeriesSplit(
        train=ordered.iloc[:train_end].reset_index(drop=True),
        validation=ordered.iloc[train_end:validation_end].reset_index(drop=True),
        test=ordered.iloc[validation_end:].reset_index(drop=True),
    )


def split_by_timestamps(
    df: pd.DataFrame,
    train_end_time: int,
    validation_end_time: int,
) -> TimeSeriesSplit:
    """Split using absolute open_time boundaries.

    Rows before ``train_end_time`` are train; rows from train_end_time up to
    validation_end_time are validation; rows at/after validation_end_time are
    test. This is useful when multiple timeframes must share calendar
    boundaries, matching backtest.holdout's anti-leakage approach.
    """
    if validation_end_time <= train_end_time:
        raise ValueError("validation_end_time must be greater than train_end_time")
    if "open_time" not in df.columns:
        raise ValueError("DataFrame must contain open_time")

    ordered = df.sort_values("open_time", kind="stable").reset_index(drop=True)
    train = ordered[ordered["open_time"] < train_end_time]
    validation = ordered[
        (ordered["open_time"] >= train_end_time)
        & (ordered["open_time"] < validation_end_time)
    ]
    test = ordered[ordered["open_time"] >= validation_end_time]

    if train.empty or validation.empty or test.empty:
        raise ValueError("timestamp boundaries must produce non-empty train/validation/test splits")

    return TimeSeriesSplit(
        train=train.reset_index(drop=True),
        validation=validation.reset_index(drop=True),
        test=test.reset_index(drop=True),
    )
