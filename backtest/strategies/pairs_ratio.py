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
import logging
import numpy as np
import pandas as pd
from backtest.simulate import simulate
from features.indicators import atr as compute_atr

logger = logging.getLogger(__name__)

NAME = "pairs_ratio_v1"

_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _validate_input_timestamps(df: pd.DataFrame, name: str) -> tuple[bool, str]:
    """
    Validate that an input dataframe has sorted, strictly increasing, unique, positive timestamps.
    Rejects malformed, non-monotonic, or duplicate timestamp sequences.
    """
    if "open_time" not in df.columns:
        return False, f"Missing 'open_time' column in {name}"

    ot = pd.to_numeric(df["open_time"], errors="coerce")
    if ot.isna().any():
        return False, f"Non-numeric or NaN 'open_time' values found in {name}"

    if (ot <= 0).any():
        return False, f"Non-positive 'open_time' values found in {name}"

    diffs = ot.diff().dropna()
    if (diffs == 0).any():
        return False, f"Duplicate timestamps detected in {name}"

    if (diffs < 0).any():
        return False, f"Unsorted (non-monotonic) timestamps detected in {name}"

    return True, ""


def _build_ratio_df(df: pd.DataFrame, pair_df: pd.DataFrame, params: dict) -> pd.DataFrame:
    ratio_cols = ["open_time", "open", "high", "low", "close", "zscore", "atr", "atr_pct"]
    if df is None or len(df) == 0 or pair_df is None or len(pair_df) == 0:
        return pd.DataFrame(columns=ratio_cols)

    req_cols = ["open_time", "open", "high", "low", "close"]
    for col in req_cols:
        if col not in df.columns or col not in pair_df.columns:
            logger.warning("Missing required OHLC column '%s' for pairs_ratio construction", col)
            return pd.DataFrame(columns=ratio_cols)

    # 1. Validate timestamps are sorted, strictly increasing, and unique
    valid_a, err_a = _validate_input_timestamps(df, "working symbol (df)")
    if not valid_a:
        logger.warning("Rejected working symbol dataframe: %s", err_a)
        return pd.DataFrame(columns=ratio_cols)

    valid_b, err_b = _validate_input_timestamps(pair_df, "pair symbol (pair_df)")
    if not valid_b:
        logger.warning("Rejected pair symbol dataframe: %s", err_b)
        return pd.DataFrame(columns=ratio_cols)

    ot_a = pd.to_numeric(df["open_time"]).to_numpy()
    ot_b = pd.to_numeric(pair_df["open_time"]).to_numpy()

    # 2. Validate component A and B timestamps match exactly
    allow_mismatched = params.get("allow_mismatched_timestamps", False)
    if not allow_mismatched:
        if len(ot_a) != len(ot_b) or not np.array_equal(ot_a, ot_b):
            logger.warning(
                "Component A and B timestamps do not match exactly (%d vs %d timestamps). Rejecting per continuity policy.",
                len(ot_a), len(ot_b)
            )
            return pd.DataFrame(columns=ratio_cols)

    pair = pair_df[["open_time", "open", "high", "low", "close"]].rename(
        columns={"open": "pair_open", "high": "pair_high", "low": "pair_low", "close": "pair_close"}
    )
    merged = df.merge(pair, on="open_time", how="inner").sort_values("open_time").reset_index(drop=True)
    if merged.empty:
        logger.warning("No aligned timestamps between working symbol and pair symbol for ratio construction")
        return pd.DataFrame(columns=ratio_cols)

    for col in ["open", "high", "low", "close"]:
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        merged[f"pair_{col}"] = pd.to_numeric(merged[f"pair_{col}"], errors="coerce")

    # 3. Validate source candle geometry and positivity
    valid_mask = (
        np.isfinite(merged["open"]) & np.isfinite(merged["high"]) &
        np.isfinite(merged["low"]) & np.isfinite(merged["close"]) &
        np.isfinite(merged["pair_open"]) & np.isfinite(merged["pair_high"]) &
        np.isfinite(merged["pair_low"]) & np.isfinite(merged["pair_close"]) &
        (merged["open"] > 0) & (merged["high"] > 0) &
        (merged["low"] > 0) & (merged["close"] > 0) &
        (merged["pair_open"] > 0) & (merged["pair_high"] > 0) &
        (merged["pair_low"] > 0) & (merged["pair_close"] > 0) &
        (merged["high"] >= merged["open"]) & (merged["high"] >= merged["close"]) & (merged["high"] >= merged["low"]) &
        (merged["low"] <= merged["open"]) & (merged["low"] <= merged["close"]) &
        (merged["pair_high"] >= merged["pair_open"]) & (merged["pair_high"] >= merged["pair_close"]) & (merged["pair_high"] >= merged["pair_low"]) &
        (merged["pair_low"] <= merged["pair_open"]) & (merged["pair_low"] <= merged["pair_close"])
    )

    invalid_count = len(merged) - int(valid_mask.sum())
    if invalid_count > 0:
        dropped_times = merged.loc[~valid_mask, "open_time"].tolist()
        logger.warning(
            "Dropped %d invalid source candle pair(s) in pairs_ratio construction (sample open_times: %s)",
            invalid_count, dropped_times[:5]
        )

    valid = merged[valid_mask].reset_index(drop=True)
    if valid.empty:
        logger.warning("No valid aligned candle pairs remaining after filtering")
        return pd.DataFrame(columns=ratio_cols)

    # 4. Conservative intrabar ratio bounds (WO71931 preserved):
    # O_R = O_A / O_B
    # C_R = C_A / C_B
    # H_R = max(O_R, C_R, H_A / L_B)
    # L_R = min(O_R, C_R, L_A / H_B)
    ratio_open = valid["open"] / valid["pair_open"]
    ratio_close = valid["close"] / valid["pair_close"]
    ratio_high = np.maximum.reduce([
        ratio_open,
        ratio_close,
        valid["high"] / valid["pair_low"]
    ])
    ratio_low = np.minimum.reduce([
        ratio_open,
        ratio_close,
        valid["low"] / valid["pair_high"]
    ])

    ratio = pd.DataFrame({
        "open_time": valid["open_time"],
        "open": ratio_open,
        "high": ratio_high,
        "low": ratio_low,
        "close": ratio_close,
    })

    # 5. Expected interval determination
    tf = str(params.get("timeframe") or params.get("TIMEFRAME") or "1d").lower()
    expected_interval = int(params.get("interval_ms") or _TIMEFRAME_MS.get(tf, 86_400_000))

    # 6. Defensive timestamp-gap detection & partitioned indicator calculation
    # Consecutive valid candles must advance by exactly expected_interval ms.
    # Any jump != expected_interval starts a new continuous partition requiring fresh warmup.
    time_diffs = ratio["open_time"].diff()
    is_discontinuity = (time_diffs != expected_interval) & time_diffs.notna()
    if is_discontinuity.any():
        gap_indices = np.where(is_discontinuity)[0]
        gap_info = [
            (int(ratio.loc[idx - 1, "open_time"]), int(ratio.loc[idx, "open_time"]), int(time_diffs.loc[idx]))
            for idx in gap_indices[:5]
        ]
        logger.warning(
            "Detected %d timestamp discontinuity/gap(s) in pairs_ratio series (expected interval: %d ms, sample gaps: %s)",
            len(gap_indices), expected_interval, gap_info
        )

    partition_id = is_discontinuity.cumsum()

    # 7. Rolling z-score partitioned calculation (prevents pre-gap data leakage)
    lookback = params.get("PAIRS_ZSCORE_LOOKBACK_BARS", 50)
    rolling_mean = ratio.groupby(partition_id)["close"].transform(
        lambda s: s.rolling(window=lookback, min_periods=lookback).mean()
    )
    rolling_std = ratio.groupby(partition_id)["close"].transform(
        lambda s: s.rolling(window=lookback, min_periods=lookback).std()
    )
    ratio["zscore"] = (ratio["close"] - rolling_mean) / rolling_std.replace(0, float("nan"))

    # 8. Rolling ATR partitioned calculation
    atr_period = params.get("ATR_PERIOD", 14)
    atr_list = []
    atr_pct_list = []
    for _, group in ratio.groupby(partition_id):
        group_atr = compute_atr(group, atr_period)
        atr_list.append(group_atr["atr"])
        atr_pct_list.append(group_atr["atr_pct"])

    ratio["atr"] = pd.concat(atr_list).sort_index()
    ratio["atr_pct"] = pd.concat(atr_pct_list).sort_index()

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
