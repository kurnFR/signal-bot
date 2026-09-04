"""
Strategy: trend_alignment_v1 (multi-timeframe trend confirmation).

Hypothesis: trend_ema_v1 (single-timeframe EMA crossover) failed walk-forward
validation partly because it whipsaws -- it fires on a crossover regardless
of what the bigger picture is doing, including against the higher-timeframe
trend. This strategy only takes a working-timeframe crossover entry when a
HIGHER timeframe (daily) is trending the same direction -- a standard
institutional-style multi-timeframe confirmation filter, and a genuinely
different structural hypothesis, not just a re-tuned version of what's
already been tested.

LONG: working-timeframe fast EMA crosses above slow EMA (same crossover
      event as trend_ema_v1) AND the most recently CLOSED daily candle's
      EMA(fast) > EMA(slow) (daily trend also up) AND MACD histogram > 0 on
      the working timeframe AND sufficient volatility.
SHORT: mirror image.

Meant to run on a timeframe BELOW daily (e.g. 4h) -- there's no timeframe
above daily currently collected to confirm against, so this returns no
trades if asked to run on '1d' itself (see run()).

NO LOOKAHEAD ACROSS TIMEFRAMES: a daily candle only becomes usable starting
the NEXT calendar day after it closes. This is enforced with pd.merge_asof
using an "available_at" timestamp (daily open_time + 1 day), direction=
'backward' -- a working-timeframe bar can only ever see a daily candle that
had fully finished forming before that bar existed.
"""
import sys
import os
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.simulate import simulate

NAME = "trend_alignment_v1"

HTF_TIMEFRAME = "1d"
ONE_DAY_MS = 24 * 60 * 60 * 1000


def _compute_htf_trend(htf_df: pd.DataFrame, ema_fast_period: int, ema_slow_period: int) -> pd.DataFrame:
    htf_df = htf_df.copy()
    htf_df["htf_ema_fast"] = htf_df["close"].ewm(span=ema_fast_period, adjust=False).mean()
    htf_df["htf_ema_slow"] = htf_df["close"].ewm(span=ema_slow_period, adjust=False).mean()
    htf_df["htf_trend_up"] = htf_df["htf_ema_fast"] > htf_df["htf_ema_slow"]
    # A daily candle only becomes usable information starting the NEXT
    # calendar day -- you can't know a daily candle's final EMA position
    # until it has actually closed.
    htf_df["available_at"] = htf_df["open_time"] + ONE_DAY_MS
    return htf_df[["available_at", "htf_trend_up"]]


def _attach_htf_trend(df: pd.DataFrame, htf_df: pd.DataFrame, ema_fast_period: int, ema_slow_period: int) -> pd.DataFrame:
    htf_prepared = _compute_htf_trend(htf_df, ema_fast_period, ema_slow_period).sort_values("available_at")
    df = df.sort_values("open_time")
    merged = pd.merge_asof(
        df, htf_prepared,
        left_on="open_time", right_on="available_at",
        direction="backward",
    )
    return merged


def _long_condition(row, prev_row, params):
    required = ["ema_fast", "ema_slow", "macd_hist", "atr_pct", "htf_trend_up"]
    if any(pd.isna(row[c]) for c in required):
        return False
    if pd.isna(prev_row["ema_fast"]) or pd.isna(prev_row["ema_slow"]):
        return False
    crossed_up = prev_row["ema_fast"] <= prev_row["ema_slow"] and row["ema_fast"] > row["ema_slow"]
    return (
        crossed_up
        and bool(row["htf_trend_up"])
        and row["macd_hist"] > 0
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    required = ["ema_fast", "ema_slow", "macd_hist", "atr_pct", "htf_trend_up"]
    if any(pd.isna(row[c]) for c in required):
        return False
    if pd.isna(prev_row["ema_fast"]) or pd.isna(prev_row["ema_slow"]):
        return False
    crossed_down = prev_row["ema_fast"] >= prev_row["ema_slow"] and row["ema_fast"] < row["ema_slow"]
    return (
        crossed_down
        and not bool(row["htf_trend_up"])
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
    if params.get("timeframe") == HTF_TIMEFRAME:
        return []  # no higher timeframe available to confirm against

    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()

    htf_df = params.get("htf_df")
    if htf_df is None:
        # Fallback for ad-hoc use: fetch live. run_backtest.py/walk_forward.py
        # normally pass htf_df explicitly (already train/holdout-split
        # consistently with the working timeframe -- see backtest/holdout.py).
        from db.db import fetch_ohlcv_with_features_df
        htf_df = fetch_ohlcv_with_features_df(params["symbol"], params["market"], HTF_TIMEFRAME, closed_only=True)

    if len(htf_df) == 0:
        return []

    df = _attach_htf_trend(df, htf_df, params["TREND_EMA_FAST"], params["TREND_EMA_SLOW"])
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    if params.get("timeframe") == HTF_TIMEFRAME:
        return []

    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()

    htf_df = params.get("htf_df")
    if htf_df is None:
        from db.db import fetch_ohlcv_with_features_df
        htf_df = fetch_ohlcv_with_features_df(params["symbol"], params["market"], HTF_TIMEFRAME, closed_only=True)

    if len(htf_df) == 0:
        return []

    df = _attach_htf_trend(df, htf_df, params["TREND_EMA_FAST"], params["TREND_EMA_SLOW"])
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
