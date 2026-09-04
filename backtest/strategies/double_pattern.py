"""
Strategy: double_pattern_v1 (double bottom / double top, classic W/M
reversal patterns with neckline-break confirmation).

Pivot detection: a bar's low (or high) is a confirmed swing pivot once
PIVOT_LOOKBACK_BARS bars on BOTH sides are known to be higher (lower) --
this confirmation is only "knowable" PIVOT_LOOKBACK_BARS bars after the
pivot itself (you can't know a low was a local minimum until you've seen
enough bars afterward to confirm price moved away from it). This is a
genuine, inherent lag -- not a lookahead bug -- handled by shifting the
raw centered-rolling pivot signal forward by PIVOT_LOOKBACK_BARS bars
before it's ever used as "known" information.

Double bottom: two confirmed swing lows within DOUBLE_PATTERN_TOLERANCE_PCT
of each other, formed within DOUBLE_PATTERN_MAX_BARS_APART bars, with a
clear peak (the "neckline") between them. Entry triggers on a decisive
close ABOVE that neckline -- the standard textbook confirmation for this
pattern (not just the second touch itself, which is a weaker signal).
Double top is the mirror image.

This is a genuinely different trigger philosophy from every other strategy:
it requires a specific TWO-TOUCH price structure plus a breakout of the
level between them, rather than a single touch/break/crossover/wick event.
"""
import numpy as np
import pandas as pd
from backtest.simulate import simulate

NAME = "double_pattern_v1"


def _precompute_double_patterns(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    n = len(df)
    N = params["PIVOT_LOOKBACK_BARS"]
    window = 2 * N + 1

    rolling_min = df["low"].rolling(window=window, center=True, min_periods=window).min()
    rolling_max = df["high"].rolling(window=window, center=True, min_periods=window).max()
    is_pivot_low_raw = (df["low"] == rolling_min)
    is_pivot_high_raw = (df["high"] == rolling_max)

    # A pivot at original position i only becomes KNOWN at position i+N
    # (once N bars afterward have been seen). shift(N) moves values N
    # positions later in the index, so shifted[j] == raw[j-N] -- exactly
    # "what we know about position j-N, now that we've reached position j".
    pivot_low_confirmed = is_pivot_low_raw.shift(N).fillna(False).values
    pivot_low_price = df["low"].shift(N).values
    pivot_high_confirmed = is_pivot_high_raw.shift(N).fillna(False).values
    pivot_high_price = df["high"].shift(N).values

    highs = df["high"].values
    lows = df["low"].values

    tol = params["DOUBLE_PATTERN_TOLERANCE_PCT"] / 100
    max_gap = params["DOUBLE_PATTERN_MAX_BARS_APART"]

    double_bottom_neckline = [None] * n
    double_top_neckline = [None] * n

    recent_pivot_lows = []   # [(price, original_idx), ...]
    recent_pivot_highs = []
    active_bottom = None     # (neckline_price, expiry_bar_idx)
    active_top = None

    for j in range(n):
        if pivot_low_confirmed[j]:
            orig_idx = j - N
            recent_pivot_lows.append((pivot_low_price[j], orig_idx))
            recent_pivot_lows[:] = recent_pivot_lows[-5:]
            if len(recent_pivot_lows) >= 2:
                p1_price, p1_idx = recent_pivot_lows[-2]
                p2_price, p2_idx = recent_pivot_lows[-1]
                if 0 < p2_idx - p1_idx <= max_gap and abs(p2_price - p1_price) / p1_price <= tol:
                    interim_peak = highs[p1_idx:p2_idx + 1].max()
                    active_bottom = (interim_peak, j + max_gap)

        if pivot_high_confirmed[j]:
            orig_idx = j - N
            recent_pivot_highs.append((pivot_high_price[j], orig_idx))
            recent_pivot_highs[:] = recent_pivot_highs[-5:]
            if len(recent_pivot_highs) >= 2:
                p1_price, p1_idx = recent_pivot_highs[-2]
                p2_price, p2_idx = recent_pivot_highs[-1]
                if 0 < p2_idx - p1_idx <= max_gap and abs(p2_price - p1_price) / p1_price <= tol:
                    interim_trough = lows[p1_idx:p2_idx + 1].min()
                    active_top = (interim_trough, j + max_gap)

        if active_bottom is not None and j > active_bottom[1]:
            active_bottom = None
        if active_top is not None and j > active_top[1]:
            active_top = None

        if active_bottom is not None:
            double_bottom_neckline[j] = active_bottom[0]
        if active_top is not None:
            double_top_neckline[j] = active_top[0]

    df["double_bottom_neckline"] = double_bottom_neckline
    df["double_top_neckline"] = double_top_neckline
    return df


def _long_condition(row, prev_row, params):
    if row["double_bottom_neckline"] is None or pd.isna(row["atr_pct"]):
        return False
    neckline = row["double_bottom_neckline"]
    prev_neckline = prev_row["double_bottom_neckline"]
    broke_above = row["close"] > neckline
    fresh_break = prev_neckline is None or prev_row["close"] <= (prev_neckline if prev_neckline is not None else neckline)
    return broke_above and fresh_break and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _short_condition(row, prev_row, params):
    if row["double_top_neckline"] is None or pd.isna(row["atr_pct"]):
        return False
    neckline = row["double_top_neckline"]
    prev_neckline = prev_row["double_top_neckline"]
    broke_below = row["close"] < neckline
    fresh_break = prev_neckline is None or prev_row["close"] >= (prev_neckline if prev_neckline is not None else neckline)
    return broke_below and fresh_break and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["DOUBLE_PATTERN_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_double_patterns(df, params)
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    df = _precompute_double_patterns(df, params)
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
