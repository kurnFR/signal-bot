"""Breakouts filtered by expanding volatility and a trend regime."""
import pandas as pd

from backtest.simulate import simulate

NAME = "volatility_breakout_v1"


def _long_condition(row, prev_row, params):
    columns = ["vb_resistance", "atr", "atr_baseline", "ema_regime", "volume_ratio"]
    if any(pd.isna(row[c]) for c in columns) or pd.isna(prev_row["vb_resistance"]):
        return False
    margin = params["VOL_BREAKOUT_MIN_MARGIN_PCT"] / 100
    return (
        row["close"] > row["vb_resistance"] * (1 + margin)
        and prev_row["close"] <= prev_row["vb_resistance"] * (1 + margin)
        and row["atr"] / row["atr_baseline"] >= params["VOL_BREAKOUT_ATR_EXPANSION_MULT"]
        and row["close"] > row["ema_regime"]
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
    )


def _short_condition(row, prev_row, params):
    columns = ["vb_support", "atr", "atr_baseline", "ema_regime", "volume_ratio"]
    if any(pd.isna(row[c]) for c in columns) or pd.isna(prev_row["vb_support"]):
        return False
    margin = params["VOL_BREAKOUT_MIN_MARGIN_PCT"] / 100
    return (
        row["close"] < row["vb_support"] * (1 - margin)
        and prev_row["close"] >= prev_row["vb_support"] * (1 - margin)
        and row["atr"] / row["atr_baseline"] >= params["VOL_BREAKOUT_ATR_EXPANSION_MULT"]
        and row["close"] < row["ema_regime"]
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["VOL_BREAKOUT_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def _prepare(df, params):
    prepared = df.copy()
    lookback = params["VOL_BREAKOUT_LOOKBACK_BARS"]
    prepared["vb_resistance"] = prepared["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    prepared["vb_support"] = prepared["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    baseline = params["VOL_BREAKOUT_ATR_BASELINE_BARS"]
    prepared["atr_baseline"] = prepared["atr"].shift(1).rolling(baseline, min_periods=baseline).mean()
    prepared["ema_regime"] = prepared["close"].ewm(
        span=params["VOL_BREAKOUT_EMA_PERIOD"], adjust=False
    ).mean()
    return prepared


def run(df, params):
    prepared = _prepare(df, params)
    return simulate(prepared, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df, params):
    prepared = _prepare(df, params)
    return simulate(prepared, params, _short_condition, _long_condition, _stop_target)