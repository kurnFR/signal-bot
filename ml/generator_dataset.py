"""Candle-level, strategy-independent dataset labels for ML signal generation.

Rows represent every eligible closed candle, not only trades emitted by a
registered technical strategy. Features are captured at the signal candle;
future bars are used only to resolve the label.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ml.dataset import SHARED_FEATURE_COLUMNS

GENERATOR_CLASSES = {"SHORT": 0, "HOLD": 1, "LONG": 2}
GENERATOR_CLASS_NAMES = {value: key for key, value in GENERATOR_CLASSES.items()}
GENERATOR_LABEL_PROTOCOL = "atr_barrier_first_touch_v1"


def build_generator_dataset(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = SHARED_FEATURE_COLUMNS,
    horizon_bars: int = 12,
    barrier_atr_multiple: float = 1.5,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.0005,
    min_net_return: float = 0.0,
) -> pd.DataFrame:
    """Build one labeled opportunity per eligible closed candle.

    Entry is the next candle's open. Symmetric barriers are anchored to the
    signal candle's ATR. The first barrier touched determines LONG/SHORT;
    no touch before the horizon, or both barriers touched in the same bar,
    is labeled HOLD. Directional labels are retained only when the barrier
    return remains above the configured round-trip fee/slippage hurdle.

    Cost parameters are decimal fractions (0.001 means 0.1%). Features are
    copied exclusively from the signal candle and never from future bars.
    exit_time and label_end_time identify the last future bar used by the
    label and can be passed to the chronological purging splitter.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    if not isinstance(horizon_bars, int) or isinstance(horizon_bars, bool) or horizon_bars < 1:
        raise ValueError("horizon_bars must be a positive integer")
    for name, value in (
        ("barrier_atr_multiple", barrier_atr_multiple),
        ("fee_rate", fee_rate),
        ("slippage_rate", slippage_rate),
        ("min_net_return", min_net_return),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if barrier_atr_multiple <= 0:
        raise ValueError("barrier_atr_multiple must be positive")
    if fee_rate < 0 or slippage_rate < 0 or min_net_return < 0:
        raise ValueError("cost rates and min_net_return must be non-negative")

    features = tuple(feature_columns)
    if not features or len(set(features)) != len(features):
        raise ValueError("feature_columns must be a non-empty sequence of unique columns")
    required = {"open_time", "open", "high", "low", "close", "atr", *features}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing generator dataset columns: {', '.join(missing)}")
    if frame.empty:
        return pd.DataFrame(columns=[
            "open_time", "entry_time", "exit_time", "label_end_time",
            "entry_price", "label_exit_price", "target", "target_name",
            "gross_return", "net_return", "forward_return", "long_net_return", "short_net_return", "label_reason", *features,
        ])

    data = frame.copy().reset_index(drop=True)
    times = pd.to_numeric(data["open_time"], errors="coerce")
    if times.isna().any() or not np.isfinite(times.to_numpy(dtype=float)).all():
        raise ValueError("open_time must contain finite numeric timestamps")
    if not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("open_time must be strictly increasing and unique")
    data["open_time"] = times.astype("int64")

    for column in ("open", "high", "low", "close", "atr"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    ohlc = data[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(ohlc).all():
        raise ValueError("OHLC values must be finite")
    if (data["high"] < data[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high must be greater than or equal to open, close, and low")
    if (data["low"] > data[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low must be less than or equal to open, close, and high")

    if "is_closed" in data.columns:
        closed = pd.to_numeric(data["is_closed"], errors="coerce").fillna(0).eq(1).to_numpy()
    else:
        closed = np.ones(len(data), dtype=bool)

    round_trip_cost = 2.0 * (float(fee_rate) + float(slippage_rate))
    rows = []
    for signal_idx in range(0, len(data) - horizon_bars):
        if not closed[signal_idx] or not closed[signal_idx + 1:signal_idx + horizon_bars + 1].all():
            continue
        atr = float(data.at[signal_idx, "atr"])
        if not np.isfinite(atr) or atr <= 0:
            continue

        entry_idx = signal_idx + 1
        entry_price = float(data.at[entry_idx, "open"])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        upper = entry_price + atr * float(barrier_atr_multiple)
        lower = entry_price - atr * float(barrier_atr_multiple)
        if lower <= 0:
            continue

        target_name = "HOLD"
        label_exit_price = float(data.at[entry_idx + horizon_bars - 1, "close"])
        label_end_idx = entry_idx + horizon_bars - 1
        reason = "horizon_expired"
        gross_return = 0.0
        ambiguous = False

        for future_idx in range(entry_idx, entry_idx + horizon_bars):
            bar = data.iloc[future_idx]
            hit_upper = float(bar["high"]) >= upper
            hit_lower = float(bar["low"]) <= lower

            if hit_upper and hit_lower:
                label_end_idx = future_idx
                label_exit_price = float(bar["close"])
                reason = "ambiguous_both_barriers_same_bar"
                target_name = "HOLD"
                ambiguous = True
                gross_return = 0.0
                break

            if hit_upper:
                label_end_idx = future_idx
                label_exit_price = max(upper, float(bar["open"])) if float(bar["open"]) >= upper else upper
                gross_return = (label_exit_price - entry_price) / entry_price
                net_return = gross_return - round_trip_cost
                target_name = "LONG" if net_return >= min_net_return else "HOLD"
                reason = "upper_barrier_first" if target_name == "LONG" else "upper_barrier_below_cost_hurdle"
                break

            if hit_lower:
                label_end_idx = future_idx
                label_exit_price = min(lower, float(bar["open"])) if float(bar["open"]) <= lower else lower
                gross_return = (entry_price - label_exit_price) / entry_price
                net_return = gross_return - round_trip_cost
                target_name = "SHORT" if net_return >= min_net_return else "HOLD"
                reason = "lower_barrier_first" if target_name == "SHORT" else "lower_barrier_below_cost_hurdle"
                break

        forward_return = (float(label_exit_price) - entry_price) / entry_price
        if ambiguous:
            # The simulator resolves same-bar SL/TP ambiguity stop-first for
            # either direction. Keep the directional validation outcomes
            # conservative instead of using the ambiguous candle close.
            long_net_return = (lower - entry_price) / entry_price - round_trip_cost
            short_net_return = (entry_price - upper) / entry_price - round_trip_cost
        else:
            long_net_return = forward_return - round_trip_cost
            short_net_return = -forward_return - round_trip_cost
        net_return = gross_return - round_trip_cost if target_name != "HOLD" else 0.0
        signal = {
            "open_time": int(data.at[signal_idx, "open_time"]),
            "entry_time": int(data.at[entry_idx, "open_time"]),
            "exit_time": int(data.at[label_end_idx, "open_time"]),
            "label_end_time": int(data.at[label_end_idx, "open_time"]),
            "entry_price": entry_price,
            "label_exit_price": float(label_exit_price),
            "target": GENERATOR_CLASSES[target_name],
            "target_name": target_name,
            "gross_return": float(gross_return),
            "net_return": float(net_return),
            "forward_return": float(forward_return),
            "long_net_return": float(long_net_return),
            "short_net_return": float(short_net_return),
            "label_reason": reason,
        }
        for column in features:
            value = data.at[signal_idx, column]
            if isinstance(value, np.generic):
                value = value.item()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not np.isfinite(value):
                value = np.nan
            signal[column] = value
        rows.append(signal)

    columns = [
        "open_time", "entry_time", "exit_time", "label_end_time",
        "entry_price", "label_exit_price", "target", "target_name",
        "gross_return", "net_return", "forward_return", "long_net_return", "short_net_return", "label_reason", *features,
    ]
    return pd.DataFrame(rows, columns=columns)
