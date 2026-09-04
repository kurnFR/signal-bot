"""
Strategy: supply_demand_v1.

Identifies "base -> displacement" zones (a small-bodied consolidation
candle immediately followed by a strong directional move) -- the
consolidation range is treated as a supply or demand zone that price is
expected to react from on a return visit, on the theory that unfilled
institutional orders remain resting there.

Zone detection and "is this zone still active" tracking require looking
back over prior bars -- this is precomputed once per run() call via a
single forward pass over the data (active_demand_low/high,
active_supply_low/high columns), then simulate() only ever reads the
current row (same interface as every other strategy). No lookahead: a
zone only becomes usable starting the SAME bar as its confirming
displacement candle (which itself only uses that bar's own OHLC, same
convention as every other strategy's signal bar) -- never a future bar.

LONG: price's low touches into the currently active demand zone (low <=
      zone_high) while the close stays above the zone's low (a reaction,
      not a breakdown through it) + bullish reaction candle + sufficient
      volatility. A zone is NOT retired after one successful touch -- it
      remains tradable until price decisively closes through it, matching
      how a strong zone is commonly retested multiple times in practice.
SHORT: mirror with an active supply zone.
"""
import pandas as pd
from backtest.simulate import simulate

NAME = "supply_demand_v1"


def _precompute_zones(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    n = len(df)
    opens = df["open"].values
    closes = df["close"].values
    lows = df["low"].values
    highs = df["high"].values
    atr = df["atr"].values

    body = closes - opens
    abs_body = abs(body)

    is_base = abs_body <= params["SD_BASE_MAX_ATR_MULT"] * atr
    is_bull_displacement = body >= params["SD_DISPLACEMENT_ATR_MULT"] * atr
    is_bear_displacement = (-body) >= params["SD_DISPLACEMENT_ATR_MULT"] * atr

    active_demand_low = [None] * n
    active_demand_high = [None] * n
    active_supply_low = [None] * n
    active_supply_high = [None] * n

    cur_demand = None  # (zone_low, zone_high, formed_at_bar)
    cur_supply = None
    max_age = params["SD_MAX_ZONE_AGE_BARS"]

    for i in range(n):
        # Record the zone state as it existed BEFORE this bar's own possible
        # confirmation -- a zone only becomes tradable starting the bar
        # AFTER it forms, never the same bar as its own displacement candle
        # (whose low/high often sits right at the zone it just departed
        # from, which would otherwise cause a phantom same-bar "retest").
        if cur_demand is not None:
            active_demand_low[i] = cur_demand[0]
            active_demand_high[i] = cur_demand[1]
        if cur_supply is not None:
            active_supply_low[i] = cur_supply[0]
            active_supply_high[i] = cur_supply[1]

        # Mitigation, using the zone that was active going into this bar
        if cur_demand is not None and closes[i] < cur_demand[0]:
            cur_demand = None
        if cur_supply is not None and closes[i] > cur_supply[1]:
            cur_supply = None

        # Form a NEW zone using bar i-1 (base) confirmed by bar i
        # (displacement) -- becomes visible starting the NEXT iteration.
        if i >= 1 and not pd.isna(atr[i]) and not pd.isna(atr[i - 1]):
            if is_base[i - 1] and is_bull_displacement[i]:
                cur_demand = (lows[i - 1], highs[i - 1], i)
            if is_base[i - 1] and is_bear_displacement[i]:
                cur_supply = (lows[i - 1], highs[i - 1], i)

        if cur_demand is not None and i - cur_demand[2] > max_age:
            cur_demand = None
        if cur_supply is not None and i - cur_supply[2] > max_age:
            cur_supply = None

    df["active_demand_low"] = active_demand_low
    df["active_demand_high"] = active_demand_high
    df["active_supply_low"] = active_supply_low
    df["active_supply_high"] = active_supply_high
    return df


def _long_condition(row, prev_row, params):
    if row["active_demand_low"] is None or pd.isna(row["atr_pct"]):
        return False
    touched = row["low"] <= row["active_demand_high"]
    held = row["close"] > row["active_demand_low"]
    bullish_reaction = row["close"] > row["open"]
    return touched and held and bullish_reaction and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _short_condition(row, prev_row, params):
    if row["active_supply_low"] is None or pd.isna(row["atr_pct"]):
        return False
    touched = row["high"] >= row["active_supply_low"]
    held = row["close"] < row["active_supply_high"]
    bearish_reaction = row["close"] < row["open"]
    return touched and held and bearish_reaction and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]


def _stop_target(direction, raw_entry_price, atr_at_signal, row, params):
    rr = params["SD_RISK_REWARD_RATIO"]
    if direction == "LONG":
        return raw_entry_price - atr_at_signal, raw_entry_price + atr_at_signal * rr
    else:
        return raw_entry_price + atr_at_signal, raw_entry_price - atr_at_signal * rr


def run(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_zones(df, params)
    return simulate(df, params, _long_condition, _short_condition, _stop_target)


def run_inverse(df: pd.DataFrame, params: dict) -> list:
    """Inverted: wherever the original goes LONG, this goes SHORT, and vice versa."""
    df = _precompute_zones(df, params)
    return simulate(df, params, _short_condition, _long_condition, _stop_target)
