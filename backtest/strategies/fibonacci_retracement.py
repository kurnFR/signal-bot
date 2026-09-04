"""
Strategy: fibonacci_retracement_v1.

Classic "buy the pullback in an uptrend at a key Fibonacci level" concept.
Trend bias comes from a fast/slow EMA (same idea as trend_ema_v1); the
entry trigger is price pulling back into the 61.8%-78.6% retracement zone
of the existing shared swing (features.fib_618 / features.fib_786, already
computed with no lookahead by features/indicators.py) and showing a
same-direction reaction candle.

LONG: EMA trend bias up (ema_fast > ema_slow) + this bar's low reaches the
      61.8% retracement level or deeper (low <= fib_618) but the close
      holds above the 78.6% level (close >= fib_786 -- didn't fully break
      the zone) + a bullish reaction candle (close > open) + sufficient
      volatility.
SHORT: mirror -- downtrend bias, price rallies into the retracement zone
       measured from the swing low upward, bearish reaction candle. (The
       shared fib_618/fib_786 columns are anchored high-to-low, which is
       the correct side for a long's pullback but the wrong side for a
       short's -- so the short computes its own mirrored levels from
       swing_low upward.)

Distinct from trend_ema_v1: that strategy enters ON the crossover itself
(early, sometimes into a fakeout); this strategy waits for an established
trend AND a specific pullback depth before entering -- a different entry
timing philosophy even though both reference trend direction.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "fibonacci_retracement_v1"


def _long_condition(row, prev_row, params):
    required = ["fib_618", "fib_786", "atr_pct", "ema_fast", "ema_slow"]
    if any(pd.isna(row[c]) for c in required):
        return False
    uptrend = row["ema_fast"] > row["ema_slow"]
    touched_zone = row["low"] <= row["fib_618"]
    held_above_786 = row["close"] >= row["fib_786"]
    bullish_reaction = row["close"] > row["open"]
    return (
        uptrend and touched_zone and held_above_786 and bullish_reaction
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    required = ["swing_high", "swing_low", "atr_pct", "ema_fast", "ema_slow"]
    if any(pd.isna(row[c]) for c in required):
        return False
    diff = row["swing_high"] - row["swing_low"]
    short_fib_618 = row["swing_low"] + 0.618 * diff
    short_fib_786 = row["swing_low"] + 0.786 * diff
    downtrend = row["ema_fast"] < row["ema_slow"]
    touched_zone = row["high"] >= short_fib_618
    held_below_786 = row["close"] <= short_fib_786
    bearish_reaction = row["close"] < row["open"]
    return (
        downtrend and touched_zone and held_below_786 and bearish_reaction
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["FIB_RISK_REWARD_RATIO"]
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
