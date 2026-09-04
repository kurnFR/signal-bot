"""
Train/holdout splitting.

The cutoff is computed as an absolute TIMESTAMP (not "last N rows"), so it
can be applied consistently across dataframes of different timeframes for
the same symbol (e.g. a 4h working series and a 1d higher-timeframe series
used together by trend_alignment_v1) -- both get split at the exact same
point in calendar time, which is essential: if the working timeframe and
the HTF timeframe used different holdout boundaries, the HTF series could
leak information from the "holdout" calendar period into a "train" period
decision, silently defeating the whole point of the split.
"""
import pandas as pd


def compute_holdout_cutoff(df: pd.DataFrame, holdout_fraction: float) -> int:
    """Returns the open_time (ms) at which the holdout period begins."""
    split_idx = int(len(df) * (1 - holdout_fraction))
    split_idx = min(split_idx, len(df) - 1)
    return int(df.iloc[split_idx]["open_time"])


def split_by_cutoff(df: pd.DataFrame, cutoff_time: int):
    """Everything with open_time < cutoff_time is train; >= cutoff_time is holdout."""
    train_df = df[df["open_time"] < cutoff_time].reset_index(drop=True)
    holdout_df = df[df["open_time"] >= cutoff_time].reset_index(drop=True)
    return train_df, holdout_df
