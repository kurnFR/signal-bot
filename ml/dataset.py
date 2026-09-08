"""Point-in-time ML dataset construction.

This module only assembles rows from information available at the strategy
signal candle. Labels come from completed backtest trades and therefore are
strictly future outcomes, never model inputs.
"""
from typing import Mapping, Sequence

import pandas as pd

# Exact shared feature columns currently produced by features/build_features.py
# and persisted by db.upsert_features. Keep this explicit: adding a feature to
# an ML experiment should be a deliberate, reviewable change.
SHARED_FEATURE_COLUMNS = (
    "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
    "volume_sma", "volume_ratio", "support", "resistance",
    "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
    "fib_5", "fib_618", "fib_786", "fib_1",
)

BASE_POINT_IN_TIME_COLUMNS = ("open", "high", "low", "close", "volume")


def _numeric_parameter_features(params: Mapping) -> dict:
    """Return scalar numeric/bool strategy parameters without mutating params.

    Strings such as PAIRS_BASE_SYMBOL are intentionally excluded from the
    first tabular baseline. They can be represented explicitly later when the
    model schema supports categorical features.
    """
    result = {}
    for key, value in params.items():
        if isinstance(value, bool):
            result[f"param__{key}"] = int(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[f"param__{key}"] = float(value)
    return result


def build_signal_dataset(
    df: pd.DataFrame,
    trades: Sequence[Mapping],
    params: Mapping | None = None,
    feature_columns: Sequence[str] = SHARED_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Build one supervised row per completed strategy signal/trade.

    ``df`` must contain the shared features and OHLCV columns used at the
    signal candle. ``trades`` must be produced by the repository backtester
    and contain ``signal_open_time``, ``direction`` and a completed outcome.

    The target is ``1`` when the completed canonical trade has positive
    ``net_pnl`` (or positive ``r_multiple``), otherwise ``0``. This is a
    historical outcome label, so it is intentionally attached only after
    selecting the point-in-time feature row.
    """
    required = {"open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing dataset columns: {', '.join(missing)}")

    if not trades:
        columns = ["trade_index", "open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns,
                   "signal_direction", "target", "outcome_r"]
        columns.extend(_numeric_parameter_features(params or {}).keys())
        return pd.DataFrame(columns=list(dict.fromkeys(columns)))

    indexed = df.drop_duplicates("open_time", keep="last").set_index("open_time", drop=False)
    rows = []
    parameter_features = _numeric_parameter_features(params or {})

    for trade_index, trade in enumerate(trades):
        if "signal_open_time" not in trade or "direction" not in trade:
            raise ValueError("each trade must contain signal_open_time and direction")
        signal_time = int(trade["signal_open_time"])
        if signal_time not in indexed.index:
            # A missing feature row means we cannot prove the model saw the
            # correct point-in-time information. Fail closed instead of
            # silently dropping the example.
            raise ValueError(f"No feature row for signal_open_time={signal_time}")

        row = indexed.loc[signal_time]
        values = {col: row[col] for col in ["open_time", *BASE_POINT_IN_TIME_COLUMNS, *feature_columns]}
        values["trade_index"] = trade_index
        values["signal_direction"] = 1 if trade["direction"] == "LONG" else -1 if trade["direction"] == "SHORT" else 0

        outcome_r = trade.get("r_multiple")
        net_pnl = trade.get("net_pnl")
        if outcome_r is None and net_pnl is None:
            raise ValueError("trade must contain r_multiple or net_pnl for the target")
        if outcome_r is not None:
            target = int(float(outcome_r) > 0)
        else:
            target = int(float(net_pnl) > 0)

        values["target"] = target
        values["outcome_r"] = float(outcome_r) if outcome_r is not None else None
        values.update(parameter_features)
        rows.append(values)

    result = pd.DataFrame(rows).sort_values("open_time", kind="stable").reset_index(drop=True)
    return result
