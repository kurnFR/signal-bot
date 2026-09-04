"""
Indicator calculations, implemented directly with pandas/numpy (no
pandas-ta / talib dependency) so the exact math is transparent and stable
across library versions.

CRITICAL: this module is imported by build_features.py (Step 2), the
backtester (Step 3), and the live signal engine (Step 6+). Never duplicate
this logic elsewhere -- if backtest and live diverge on how an indicator
is computed, backtested performance stops predicting live performance.

All functions take a DataFrame sorted ascending by time with at least
columns: open, high, low, close, volume -- and return a DataFrame of new
columns aligned to the same index. They do not mutate the input.
"""
import numpy as np
import pandas as pd


def rsi(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # Where avg_loss is 0 (no losses in window) RSI is defined as 100
    out = out.where(avg_loss != 0, 100)
    return out.rename("rsi")


def macd(df: pd.DataFrame, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """Standard MACD: fast/slow EMA of close, signal EMA of the MACD line."""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist,
    })


def atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Wilder's ATR, plus ATR as a percent of closing price (for volatility
    filters like VOLATILITY_THRESHOLD_PCT which are defined as a %)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    atr_pct = (atr_val / df["close"]) * 100
    return pd.DataFrame({"atr": atr_val, "atr_pct": atr_pct})


def volume_profile(df: pd.DataFrame, sma_period: int) -> pd.DataFrame:
    """Rolling volume average and the current bar's ratio to it -- used to
    flag 'elevated volume' bars against VOLUME_MULTIPLIER."""
    vol_sma = df["volume"].rolling(window=sma_period, min_periods=sma_period).mean()
    vol_ratio = df["volume"] / vol_sma.replace(0, np.nan)
    return pd.DataFrame({"volume_sma": vol_sma, "volume_ratio": vol_ratio})


def rolling_support_resistance(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """
    Simple rolling-channel support/resistance: the highest high and lowest
    low over the trailing `lookback` bars (HTF_S_R_LOOKBACK_BARS). Excludes
    the current (possibly still-forming) bar from its own window so a
    single wick doesn't redefine the level it's being compared against.
    """
    resistance = df["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    support = df["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    return pd.DataFrame({"resistance": resistance, "support": support})


FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def fibonacci_levels(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """
    Fibonacci retracement levels derived from the swing high/low over the
    trailing `lookback` bars (same window as support/resistance, so both
    describe the same recent price range). Levels are anchored high-to-low
    (i.e. fib_0 = swing high, fib_1 = swing low) -- when using these for a
    long-side pullback-buy setup, read them as retracement from the high;
    for a short-side setup, treat fib_1..fib_0 as retracement from the low.
    """
    swing_high = df["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    swing_low = df["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    diff = swing_high - swing_low

    out = {"swing_high": swing_high, "swing_low": swing_low}
    for ratio in FIB_RATIOS:
        col = f"fib_{str(ratio).replace('0.', '')}" if ratio != 0.0 else "fib_0"
        if ratio == 1.0:
            col = "fib_1"
        out[col] = swing_high - ratio * diff
    return pd.DataFrame(out)


def compute_all_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Runs every indicator above on a single symbol/market/timeframe's OHLCV
    history and returns one combined DataFrame aligned to df's index.

    `params` keys expected (all present in config.py):
      RSI_WINDOW, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD,
      VOLUME_SMA_PERIOD, HTF_S_R_LOOKBACK_BARS
    """
    parts = [
        rsi(df, params["RSI_WINDOW"]),
        macd(df, params["MACD_FAST"], params["MACD_SLOW"], params["MACD_SIGNAL"]),
        atr(df, params["ATR_PERIOD"]),
        volume_profile(df, params["VOLUME_SMA_PERIOD"]),
        rolling_support_resistance(df, params["HTF_S_R_LOOKBACK_BARS"]),
        fibonacci_levels(df, params["HTF_S_R_LOOKBACK_BARS"]),
    ]
    return pd.concat(parts, axis=1)
