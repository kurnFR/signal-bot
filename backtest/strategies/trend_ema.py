"""
Strategy: trend_ema_v1 (classic institutional-style trend-following).

Trades in the direction of the trend at the moment a fast EMA crosses the
slow EMA, confirmed by MACD momentum -- the opposite philosophy again from
mean_reversion (trades WITH momentum, not against an extreme) and distinct
from breakout (reacts to a moving-average trend shift, not a price-level break).

EMAs are computed here, on the fly, from `close` -- they're strategy-
specific, not part of the shared `features` table (which holds general-
purpose indicators every strategy might use; EMA crossover is specific to
this one). Computing via pandas .ewm() at bar i only uses data up to and
including bar i, so this doesn't introduce any lookahead.

LONG: fast EMA crosses above slow EMA this bar (not just "is currently
      above" -- a real crossover event, so we don't fire on every bar of an
      already-established trend) + MACD histogram positive.
SHORT: mirror -- fast EMA crosses below slow EMA + MACD histogram negative.

Uses TREND_RISK_REWARD_RATIO (wider than mean-reversion's, similar
reasoning to the breakout strategy -- trend trades are given more room).
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "trend_ema_v1"


def _long_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["ema_fast", "ema_slow", "macd_hist", "atr_pct"]):
        return False
    if pd.isna(prev_row["ema_fast"]) or pd.isna(prev_row["ema_slow"]):
        return False
    crossed_up = prev_row["ema_fast"] <= prev_row["ema_slow"] and row["ema_fast"] > row["ema_slow"]
    return (
        crossed_up
        and row["macd_hist"] > 0
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["ema_fast", "ema_slow", "macd_hist", "atr_pct"]):
        return False
    if pd.isna(prev_row["ema_fast"]) or pd.isna(prev_row["ema_slow"]):
        return False
    crossed_down = prev_row["ema_fast"] >= prev_row["ema_slow"] and row["ema_fast"] < row["ema_slow"]
    return (
        crossed_down
        and row["macd_hist"] < 0
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["TREND_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
