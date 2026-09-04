"""
Strategy: funding_extreme_reversal_v1.

Every strategy tested so far derives its signal purely from price/volume
patterns (RSI, EMAs, S/R, Fibonacci, SMC, supply/demand, double tops) --
and none has shown a validated edge on BTC/ETH/BNB after holdout checks.
This strategy is structurally different: it uses funding rate, a genuine
piece of information about aggregate futures market positioning that
exists independently of any price pattern.

Hypothesis: extreme funding reflects crowded, over-leveraged positioning.
Extreme POSITIVE funding (longs paying a lot to stay long) often precedes
a downward correction as that crowded long positioning unwinds. Extreme
NEGATIVE funding (shorts paying a lot) often precedes an upward squeeze.
This is a genuine sentiment/positioning signal, not a price pattern --
usable on SPOT price data even though funding itself is a futures concept
(a common real technique: spot traders watching perp funding as a
market-wide positioning gauge).

LONG: funding_rate <= FUNDING_EXTREME_NEGATIVE_PCT (crowded shorts) +
      sufficient volatility to be worth trading.
SHORT: funding_rate >= FUNDING_EXTREME_POSITIVE_PCT (crowded longs) +
       sufficient volatility.

Requires the df to have been fetched with include_funding=True (see
db/db.py's fetch_ohlcv_with_features_df) -- the funding_rate column is
joined with no lookahead (only funding settlements that had already
occurred by a candle's open_time are visible to it).

UNITS: Binance returns funding_rate as a raw fraction (e.g. 0.00003081
means 0.003081%), but FUNDING_EXTREME_POSITIVE_PCT/NEGATIVE_PCT in
config.py are written as percentage points (0.05 meaning "0.05%") for
readability. Converted here (x100) before comparing -- comparing the raw
fraction directly against a "0.05" threshold would silently require 5%
funding per settlement, which essentially never happens (a real bug found
during testing: it produced zero trades on years of real data because the
threshold was 500x too extreme, not because genuine extremes are rare).
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "funding_extreme_reversal_v1"


def _long_condition(row, prev_row, params):
    if pd.isna(row.get("funding_rate")) or pd.isna(row["atr_pct"]):
        return False
    funding_pct = row["funding_rate"] * 100
    return (
        funding_pct <= params["FUNDING_EXTREME_NEGATIVE_PCT"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _short_condition(row, prev_row, params):
    if pd.isna(row.get("funding_rate")) or pd.isna(row["atr_pct"]):
        return False
    funding_pct = row["funding_rate"] * 100
    return (
        funding_pct >= params["FUNDING_EXTREME_POSITIVE_PCT"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
    )


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["FUNDING_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    if "funding_rate" not in df.columns:
        return []  # df wasn't fetched with include_funding=True -- nothing to do
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    if "funding_rate" not in df.columns:
        return []
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
