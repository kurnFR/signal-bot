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
GENERATOR_LABEL_PROTOCOL = "execution_aligned_stop_target_timeout_v2"


def build_generator_dataset(
    frame: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = SHARED_FEATURE_COLUMNS,
    horizon_bars: int = 12,
    barrier_atr_multiple: float = 1.5,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.0005,
    min_net_return: float = 0.0,
    stop_atr_multiple: float = 1.5,\n    risk_reward_ratio: float = 2.0,\n    max_hold_bars: int = 72,
) -> pd.DataFrame:
    """Build one labeled opportunity per eligible closed candle.

    Entry is the next candle's open. Each direction is independently simulated
    with the configured ATR stop, reward multiple, max holding period, and
    canonical fee/slippage accounting. Stop is checked before target on a bar
    touching both. The better net direction is labeled LONG/SHORT only when it
    clears the configured return hurdle; otherwise the row is HOLD.

    Cost parameters are decimal fractions (0.001 means 0.1%). Features are
    copied exclusively from the signal candle and never from future bars.
    exit_time and label_end_time identify the last future bar used by the
    both directional outcomes and can be passed to the chronological purging splitter.
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
    for name, value in (("stop_atr_multiple", stop_atr_multiple), ("risk_reward_ratio", risk_reward_ratio)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if isinstance(max_hold_bars, bool) or not isinstance(max_hold_bars, int) or max_hold_bars < 1:
        raise ValueError("max_hold_bars must be a positive integer")
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

    from backtest.accounting import calculate_trade_accounting

    rows = []
    for signal_idx in range(0, len(data) - max_hold_bars - 1):
        if not closed[signal_idx] or not closed[signal_idx + 1:signal_idx + max_hold_bars + 2].all():
            continue
        atr = float(data.at[signal_idx, "atr"])
        if not np.isfinite(atr) or atr <= 0:
            continue

        entry_idx = signal_idx + 1
        entry_price = float(data.at[entry_idx, "open"])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        directional = {}
        for direction in ("LONG", "SHORT"):
            stop_distance = atr * float(stop_atr_multiple)
            if direction == "LONG":
                stop_price = entry_price - stop_distance
                target_price = entry_price + stop_distance * float(risk_reward_ratio)
            else:
                stop_price = entry_price + stop_distance
                target_price = entry_price - stop_distance * float(risk_reward_ratio)
            if stop_price <= 0 or target_price <= 0:
                directional[direction] = None
                continue

            exit_price = None
            exit_idx = entry_idx
            reason = "max_hold_timeout"
            final_idx = min(entry_idx + max_hold_bars, len(data) - 1)
            for future_idx in range(entry_idx, final_idx + 1):
                bar = data.iloc[future_idx]
                # Match backtest.simulate: stop is checked before target on
                # a candle that touches both levels.
                if direction == "LONG":
                    if float(bar["low"]) <= stop_price:
                        exit_price, reason = stop_price, "stop_loss"
                    elif float(bar["high"]) >= target_price:
                        exit_price, reason = target_price, "take_profit"
                else:
                    if float(bar["high"]) >= stop_price:
                        exit_price, reason = stop_price, "stop_loss"
                    elif float(bar["low"]) <= target_price:
                        exit_price, reason = target_price, "take_profit"
                if exit_price is not None:
                    exit_idx = future_idx
                    break
                if future_idx - entry_idx >= max_hold_bars:
                    exit_price = float(bar["close"])
                    exit_idx = future_idx
                    reason = "max_hold_timeout"
                    break
            if exit_price is None:
                # Insufficient future bars are never silently treated as a
                # completed max-hold outcome.
                directional[direction] = None
                continue

            accounting = calculate_trade_accounting(
                direction=direction,
                quantity=1.0,
                entry_price=entry_price,
                exit_price=float(exit_price),
                fee_pct=float(fee_rate),
                entry_slippage_pct=float(slippage_rate),
                exit_slippage_pct=float(slippage_rate),
                stop_price=stop_price,
            )
            net_return = float(accounting["return_pct_on_entry_notional"]) / 100.0
            directional[direction] = {
                "net_return": net_return,
                "exit_idx": exit_idx,
                "exit_price": float(exit_price),
                "reason": reason,
                "gross_return": (
                    (float(exit_price) - entry_price) / entry_price
                    if direction == "LONG"
                    else (entry_price - float(exit_price)) / entry_price
                ),
            }

        if directional.get("LONG") is None or directional.get("SHORT") is None:
            continue
        long_result = directional["LONG"]
        short_result = directional["SHORT"]
        best_direction = "LONG" if long_result["net_return"] >= short_result["net_return"] else "SHORT"
        best_result = directional[best_direction]
        target_name = best_direction if best_result["net_return"] >= min_net_return else "HOLD"
        label_end_idx = max(long_result["exit_idx"], short_result["exit_idx"])
        signal = {
            "open_time": int(data.at[signal_idx, "open_time"]),
            "entry_time": int(data.at[entry_idx, "open_time"]),
            "exit_time": int(data.at[label_end_idx, "open_time"]),
            "label_end_time": int(data.at[label_end_idx, "open_time"]),
            "entry_price": entry_price,
            "label_exit_price": best_result["exit_price"],
            "target": GENERATOR_CLASSES[target_name],
            "target_name": target_name,
            "gross_return": float(best_result["gross_return"]),
            "net_return": float(best_result["net_return"]) if target_name != "HOLD" else 0.0,
            "forward_return": float(best_result["gross_return"]),
            "long_net_return": float(long_result["net_return"]),
            "short_net_return": float(short_result["net_return"]),
            "label_reason": best_result["reason"] if target_name != "HOLD" else "directional_returns_below_hurdle",
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
