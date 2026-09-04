"""
Strategy: breakout_continuation_v1.

Philosophy is the opposite of mean_reversion: instead of fading price at a
level, this trades WITH a decisive break beyond a meaningful recent range,
on the theory that a genuine breakout with volume support tends to continue.

v1 originally reused the shared 50-bar support/resistance from `features`
(the same one mean_reversion fades against). That turned out far too
reactive for a breakout definition: price ticking even fractionally above
a 50-bar rolling high happens constantly in ordinary chop, producing
900+ "breakout" trades on 15m over 2 years -- clearly mostly noise, not
genuine breaks, which is reflected in win rates sitting right around the
theoretical breakeven rate for the RR ratio used (~25% at 3:1).

Fix: compute a LONGER, strategy-specific rolling level here (BREAKOUT_LOOKBACK_BARS,
default 100 vs the shared 50) directly from raw high/low, and require price
to clear it by a minimum margin (BREAKOUT_MIN_MARGIN_PCT) -- not just a
single tick -- before counting it as a genuine break. This is intentionally
independent of the `features` table's support/resistance so mean_reversion's
behavior is completely unaffected by this change.

LONG: close clears a 100-bar rolling high by >= BREAKOUT_MIN_MARGIN_PCT +
      elevated volume + sufficient volatility.
SHORT: mirror -- close clears a 100-bar rolling low by the same margin.

Uses a wider risk:reward (BREAKOUT_RISK_REWARD_RATIO) than mean-reversion,
since trend/breakout trades are conventionally given more room to run.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "breakout_continuation_v1"


def _long_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["brk_resistance", "volume_ratio", "atr_pct"]):
        return False
    if pd.isna(prev_row["brk_resistance"]):
        return False
    margin = params["BREAKOUT_MIN_MARGIN_PCT"] / 100
    threshold = row["brk_resistance"] * (1 + margin)
    prev_threshold = prev_row["brk_resistance"] * (1 + margin)
    broke_above = row["close"] > threshold
    prev_below = prev_row["close"] <= prev_threshold  # require a fresh cross, not an already-broken level
    return (
        broke_above
        and prev_below
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["brk_support", "volume_ratio", "atr_pct"]):
        return False
    if pd.isna(prev_row["brk_support"]):
        return False
    margin = params["BREAKOUT_MIN_MARGIN_PCT"] / 100
    threshold = row["brk_support"] * (1 - margin)
    prev_threshold = prev_row["brk_support"] * (1 - margin)
    broke_below = row["close"] < threshold
    prev_above = prev_row["close"] >= prev_threshold
    return (
        broke_below
        and prev_above
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["BREAKOUT_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    df = df.copy()
    lookback = params["BREAKOUT_LOOKBACK_BARS"]
    # shift(1) excludes the current (possibly still-evaluating) bar from its
    # own rolling window -- same no-lookahead convention as features/indicators.py
    df["brk_resistance"] = df["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    df["brk_support"] = df["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    df = df.copy()
    lookback = params["BREAKOUT_LOOKBACK_BARS"]
    df["brk_resistance"] = df["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    df["brk_support"] = df["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
