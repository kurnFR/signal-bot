"""
Strategy: pairs_ratio_v1 (relative-value / statistical-arbitrage style).

Every strategy tested before this one bets on the ABSOLUTE price direction
of one asset. This is structurally different: it trades the RATIO between
a symbol and a base/quote leg (default BTCUSDT) reverting to its recent
mean -- a classic institutional statistical-arbitrage technique. It's
"market-neutral" in spirit: doesn't care whether the whole market goes up
or down, only whether the two assets' relative pricing has stretched
further than usual and tends to revert.

Construction: builds a synthetic "ratio OHLC" series (this symbol's O/H/L/C
divided by the base symbol's O/H/L/C at each aligned bar) -- the standard
convention used by ratio charts on trading platforms. Note this is an
approximation: dividing highs/lows doesn't represent a single real
tradeable price the way dividing closes does, since each leg's intra-bar
high/low don't necessarily occur at the same moment -- accepted industry
convention, not a bug, but worth knowing.

LONG (bet the ratio rises, i.e. long this symbol relative to the base):
      z-score of the ratio's close vs. its own rolling mean/std is <=
      -PAIRS_ZSCORE_ENTRY_THRESHOLD (this symbol has underperformed the
      base leg more than usual).
SHORT: mirror, z-score >= +PAIRS_ZSCORE_ENTRY_THRESHOLD.

Stop/target sized off the ratio's own ATR (reusing features/indicators.py's
already-tested Wilder ATR implementation, applied to the synthetic ratio
series) x PAIRS_RISK_REWARD_RATIO -- same convention as every other
strategy, just operating on ratio units instead of price units. R-multiples
and % returns are unaffected by this (they're relative measures either way).

IMPORTANT SCOPE NOTE for later: this backtests as if the ratio itself were
a single tradeable instrument. Actually trading this signal live means
opening TWO offsetting real positions (long one leg, short the other,
ideally sized to be roughly dollar/beta-neutral) -- the live signal engine
(a later step) will need to generate both legs' orders, not just one.

Requires a second symbol's OHLCV (the base/quote leg) passed via
params["pair_df"] (see PAIRS_STRATEGIES in backtest/strategies/__init__.py
for how the driver scripts fetch and train/holdout-split it consistently
with the working symbol -- same pattern as trend_alignment.py's htf_df).
Skips symbols being paired against themselves (e.g. BTCUSDT vs BTCUSDT).
"""
import pandas as pd
from backtest.simulate import simulate
from features.indicators import atr as compute_atr

NAME = "pairs_ratio_v1"


def _build_ratio_df(df: pd.DataFrame, pair_df: pd.DataFrame, params: dict) -> pd.DataFrame:
    pair = pair_df[["open_time", "open", "high", "low", "close"]].rename(
        columns={"open": "pair_open", "high": "pair_high", "low": "pair_low", "close": "pair_close"}
    )
    merged = df.merge(pair, on="open_time", how="inner").sort_values("open_time").reset_index(drop=True)

    ratio = pd.DataFrame({
        "open_time": merged["open_time"],
        "open": merged["open"] / merged["pair_open"],
        "high": merged["high"] / merged["pair_high"],
        "low": merged["low"] / merged["pair_low"],
        "close": merged["close"] / merged["pair_close"],
    })

    lookback = params["PAIRS_ZSCORE_LOOKBACK_BARS"]
    rolling_mean = ratio["close"].rolling(window=lookback, min_periods=lookback).mean()
    rolling_std = ratio["close"].rolling(window=lookback, min_periods=lookback).std()
    ratio["zscore"] = (ratio["close"] - rolling_mean) / rolling_std.replace(0, float("nan"))

    atr_df = compute_atr(ratio, params["ATR_PERIOD"])
    ratio["atr"] = atr_df["atr"]
    ratio["atr_pct"] = atr_df["atr_pct"]

    return ratio


def _long_condition(row, prev_row, params):
    if pd.isna(row.get("zscore")) or pd.isna(row.get("atr_pct")):
        return False
    return (
        row["zscore"] <= -params["PAIRS_ZSCORE_ENTRY_THRESHOLD"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    if pd.isna(row.get("zscore")) or pd.isna(row.get("atr_pct")):
        return False
    return (
        row["zscore"] >= params["PAIRS_ZSCORE_ENTRY_THRESHOLD"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["PAIRS_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def _get_pair_df(params):
    base_symbol = params.get("pair_symbol", params.get("PAIRS_BASE_SYMBOL"))
    if params.get("symbol") == base_symbol:
        return None  # can't pair a symbol against itself

    pair_df = params.get("pair_df")
    if pair_df is None:
        # Fallback for ad-hoc use -- driver scripts normally pass pair_df
        # explicitly, already train/holdout-split consistently (see
        # PAIRS_STRATEGIES in backtest/strategies/__init__.py).
        from db.db import fetch_ohlcv_with_features_df
        pair_df = fetch_ohlcv_with_features_df(base_symbol, params.get("market", "spot"),
                                                params.get("timeframe"), closed_only=True)
    return pair_df


def run(df: pd.DataFrame, params: dict) -> list:
    pair_df = _get_pair_df(params)
    if pair_df is None or len(pair_df) == 0:
        return []
    ratio_df = _build_ratio_df(df, pair_df, params)
    return simulate(ratio_df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    pair_df = _get_pair_df(params)
    if pair_df is None or len(pair_df) == 0:
        return []
    ratio_df = _build_ratio_df(df, pair_df, params)
    return simulate(ratio_df, params, _short_condition, _long_condition, _stop_target)
