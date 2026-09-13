"""Point-in-time ML dataset construction with explicit target definitions."""
from typing import Mapping, Sequence

import pandas as pd

SHARED_FEATURE_COLUMNS = (
    "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
    "volume_sma", "volume_ratio", "support", "resistance",
    "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
    "fib_5", "fib_618", "fib_786", "fib_1",
)
BASE_POINT_IN_TIME_COLUMNS = ("open", "high", "low", "close", "volume")
TARGET_TYPES = ("binary_positive_r", "binary_r_above_half", "binary_r_above_one")


def _numeric_parameter_features(params: Mapping) -> dict:
    result = {}
    for key, value in params.items():
        if isinstance(value, bool):
            result[f"param__{key}"] = int(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[f"param__{key}"] = float(value)
    return result


def _make_target(outcome_r: float, target_type: str) -> int:
    if target_type == "binary_positive_r":
        return int(outcome_r > 0.0)
    if target_type == "binary_r_above_half":
        return int(outcome_r > 0.5)
    if target_type == "binary_r_above_one":
        return int(outcome_r > 1.0)
    raise ValueError(f"unsupported target_type: {target_type}")


def build_signal_dataset(
    df: pd.DataFrame,
    trades: Sequence[Mapping],
    params: Mapping | None = None,
    feature_columns: Sequence[str] = SHARED_FEATURE_COLUMNS,
    target_type: str = "binary_positive_r",
) -> pd.DataFrame:
    """Build point-in-time supervised rows from completed strategy trades.

    Target variants are binary and use only the completed trade's canonical R:
    positive_r, R above 0.5, or R above 1.0. Features remain restricted to the
    signal candle, so the future outcome is never exposed as an input.
    """
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {TARGET_TYPES}")
    required = {"open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing dataset columns: {', '.join(missing)}")

    parameter_features = _numeric_parameter_features(params or {})
    if not trades:
        columns = ["trade_index", "open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns,
                   "signal_direction", "target", "outcome_r"]
        columns.extend(parameter_features.keys())
        return pd.DataFrame(columns=list(dict.fromkeys(columns)))

    indexed = df.drop_duplicates("open_time", keep="last").set_index("open_time", drop=False)
    rows = []
    for trade_index, trade in enumerate(trades):
        if "signal_open_time" not in trade or "direction" not in trade:
            raise ValueError("each trade must contain signal_open_time and direction")
        signal_time = int(trade["signal_open_time"])
        if signal_time not in indexed.index:
            raise ValueError(f"No feature row for signal_open_time={signal_time}")
        row = indexed.loc[signal_time]
        values = {col: row[col] for col in ["open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns]}
        values["trade_index"] = trade_index
        values["signal_direction"] = 1 if trade["direction"] == "LONG" else -1 if trade["direction"] == "SHORT" else 0
        outcome_r = trade.get("r_multiple")
        net_pnl = trade.get("net_pnl")
        if outcome_r is None and net_pnl is None:
            raise ValueError("trade must contain r_multiple or net_pnl for the target")
        if outcome_r is None:
            raise ValueError("risk-aware targets require canonical r_multiple on every trade")
        outcome_r = float(outcome_r)
        values["target"] = _make_target(outcome_r, target_type)
        values["outcome_r"] = outcome_r
        values.update(parameter_features)
        rows.append(values)

    return pd.DataFrame(rows).sort_values("open_time", kind="stable").reset_index(drop=True)
