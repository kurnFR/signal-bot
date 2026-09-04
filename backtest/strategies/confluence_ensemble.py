"""
Strategy: confluence_ensemble_v1.

Every strategy tested before this one relied on a SINGLE trigger condition
-- and none survived holdout validation. This combines several
independently weak signals into one weighted score, trading only when
enough of them agree -- closer to how systematic/institutional approaches
often work in practice (many partially-independent weak signals combined,
rather than one clean trigger expected to carry the whole edge).

Components (each contributes -1 bearish / 0 neutral / +1 bullish),
deliberately spanning different, complementary read-outs of the market so
a high combined score requires genuine cross-signal agreement, not just
repetition of the same underlying idea:

  - RSI: oversold -> bullish, overbought -> bearish (mean-reversion read)
  - Trend: fast EMA vs slow EMA (trend-following read)
  - MACD: histogram positive & rising -> bullish, negative & falling ->
    bearish (momentum read)
  - Volume: elevated volume + an up/down close -> direction confirmation
  - Support/Resistance: price near support -> bullish, near resistance ->
    bearish (level read)
  - Funding rate (only if available): extreme negative -> bullish, extreme
    positive -> bearish (positioning read)

Each component's contribution is weighted (CONFLUENCE_WEIGHT_*, default
1.0 each) and summed; a trade only fires when the total score reaches
MIN_CONFLUENCE_SCORE in either direction. Funding is included only when the
df was fetched with include_funding=True (this strategy is registered in
FUNDING_STRATEGIES) -- otherwise that component silently contributes 0,
same graceful-degradation pattern as funding_extreme.py.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "confluence_ensemble_v1"


def _rsi_component(row, params):
    if pd.isna(row.get("rsi")):
        return 0
    if row["rsi"] < params["RSI_OVERSOLD"]:
        return 1
    if row["rsi"] > params["RSI_OVERBOUGHT"]:
        return -1
    return 0


def _trend_component(row, params):
    if pd.isna(row.get("ema_fast")) or pd.isna(row.get("ema_slow")):
        return 0
    if row["ema_fast"] > row["ema_slow"]:
        return 1
    if row["ema_fast"] < row["ema_slow"]:
        return -1
    return 0


def _macd_component(row, prev_row, params):
    if pd.isna(row.get("macd_hist")) or pd.isna(prev_row.get("macd_hist")):
        return 0
    if row["macd_hist"] > 0 and row["macd_hist"] > prev_row["macd_hist"]:
        return 1
    if row["macd_hist"] < 0 and row["macd_hist"] < prev_row["macd_hist"]:
        return -1
    return 0


def _volume_component(row, params):
    if pd.isna(row.get("volume_ratio")):
        return 0
    if row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]:
        if row["close"] > row["open"]:
            return 1
        if row["close"] < row["open"]:
            return -1
    return 0


def _sr_component(row, params):
    if pd.isna(row.get("support")) or pd.isna(row.get("resistance")):
        return 0
    near_support = abs(row["close"] - row["support"]) / row["support"] * 100 <= params["SR_PROXIMITY_PCT"]
    near_resistance = abs(row["close"] - row["resistance"]) / row["resistance"] * 100 <= params["SR_PROXIMITY_PCT"]
    if near_support:
        return 1
    if near_resistance:
        return -1
    return 0


def _funding_component(row, params):
    funding_rate = row.get("funding_rate")
    if funding_rate is None or pd.isna(funding_rate):
        return 0
    funding_pct = funding_rate * 100  # raw fraction -> percentage points, see funding_extreme.py
    if funding_pct <= params["FUNDING_EXTREME_NEGATIVE_PCT"]:
        return 1
    if funding_pct >= params["FUNDING_EXTREME_POSITIVE_PCT"]:
        return -1
    return 0


def _total_score(row, prev_row, params):
    return (
        _rsi_component(row, params) * params["CONFLUENCE_WEIGHT_RSI"]
        + _trend_component(row, params) * params["CONFLUENCE_WEIGHT_TREND"]
        + _macd_component(row, prev_row, params) * params["CONFLUENCE_WEIGHT_MACD"]
        + _volume_component(row, params) * params["CONFLUENCE_WEIGHT_VOLUME"]
        + _sr_component(row, params) * params["CONFLUENCE_WEIGHT_SR"]
        + _funding_component(row, params) * params["CONFLUENCE_WEIGHT_FUNDING"]
    )


def _long_condition(row, prev_row, params):
    if pd.isna(row.get("atr_pct")):
        return False
    score = _total_score(row, prev_row, params)
    return score >= params["MIN_CONFLUENCE_SCORE"] and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _short_condition(row, prev_row, params):
    if pd.isna(row.get("atr_pct")):
        return False
    score = _total_score(row, prev_row, params)
    return score <= -params["MIN_CONFLUENCE_SCORE"] and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["CONFLUENCE_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()
    if "funding_rate" not in df.columns:
        df["funding_rate"] = float("nan")
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=params["TREND_EMA_FAST"], adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=params["TREND_EMA_SLOW"], adjust=False).mean()
    if "funding_rate" not in df.columns:
        df["funding_rate"] = float("nan")
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
