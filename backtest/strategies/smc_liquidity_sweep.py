"""
Strategy: smc_liquidity_sweep_v1 (Smart Money Concepts -- liquidity sweep /
stop-hunt reversal).

Hypothesis: price frequently wicks beyond a recent swing high/low to
trigger resting stop-loss orders ("liquidity") clustered just beyond that
level, before reversing -- a pattern commonly described in Smart Money
Concepts trading as a liquidity grab. This is a genuinely different trigger
philosophy from every other strategy tested so far: it reacts to a FAILED
break (a wick beyond the level that gets rejected), rather than a confirmed
break (breakout_continuation_v1), a level being merely touched
(mean_reversion), or a moving-average event (trend_ema_v1/trend_alignment_v1).

LONG: this bar's low wicks below the rolling support level by at least
      SMC_WICK_MIN_ATR_MULT x ATR, but the close recovers back above
      support (rejection, not a genuine breakdown) + sufficient volatility.
SHORT: mirror -- wick above resistance, close recovers back below it.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "smc_liquidity_sweep_v1"


def _long_condition(row, prev_row, params):
    required = ["support", "atr", "atr_pct"]
    if any(pd.isna(row[c]) for c in required):
        return False
    wick_size = row["support"] - row["low"]
    swept = row["low"] < row["support"]
    rejected = row["close"] > row["support"]
    meaningful_wick = wick_size >= params["SMC_WICK_MIN_ATR_MULT"] * row["atr"]
    return (
        swept and rejected and meaningful_wick
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    required = ["resistance", "atr", "atr_pct"]
    if any(pd.isna(row[c]) for c in required):
        return False
    wick_size = row["high"] - row["resistance"]
    swept = row["high"] > row["resistance"]
    rejected = row["close"] < row["resistance"]
    meaningful_wick = wick_size >= params["SMC_WICK_MIN_ATR_MULT"] * row["atr"]
    return (
        swept and rejected and meaningful_wick
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["SMC_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
