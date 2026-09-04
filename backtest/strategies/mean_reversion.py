"""
Strategy: confluence_reversal_v1 (mean-reversion / exhaustion at support/resistance).

LONG: RSI oversold + price near rolling support + elevated volume +
      sufficient volatility + MACD histogram turning up.
SHORT: mirror image at resistance.

This is the original Step 3 strategy, refactored to use the shared
simulate() engine -- identical behavior to before, just restructured so
it can be compared side-by-side with alternative strategies.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "confluence_reversal_v1"


def _long_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["rsi", "support", "volume_ratio", "atr_pct", "macd_hist"]) or pd.isna(prev_row["macd_hist"]):
        return False
    near_support = abs(row["close"] - row["support"]) / row["support"] * 100 <= params["SR_PROXIMITY_PCT"]
    return (
        row["rsi"] < params["RSI_OVERSOLD"]
        and near_support
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
        and row["macd_hist"] > prev_row["macd_hist"]
    )


def _short_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["rsi", "resistance", "volume_ratio", "atr_pct", "macd_hist"]) or pd.isna(prev_row["macd_hist"]):
        return False
    near_resistance = abs(row["close"] - row["resistance"]) / row["resistance"] * 100 <= params["SR_PROXIMITY_PCT"]
    return (
        row["rsi"] > params["RSI_OVERBOUGHT"]
        and near_resistance
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
        and row["macd_hist"] < prev_row["macd_hist"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
