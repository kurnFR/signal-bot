"""
RetailBot2 Strategy Runners for Backtesting, Simulation, and Paper Trading.

Exposes executable backtest runners for all 7 Retail Death Trap strategies
and their inverse counterparts, as well as the composite consensus runner:
- BB_REV / BB_REV_INV (Bollinger Band Reversion)
- DBL_PAT / DBL_PAT_INV (Double Top/Bottom Pattern)
- RSI_CTR / RSI_CTR_INV (RSI Counter-Trend)
- MA_WHIP / MA_WHIP_INV (MA Crossover Whipsaw)
- SR_TRAP / SR_TRAP_INV (S/R Bounce Trap)
- MACD_DIV / MACD_DIV_INV (MACD Divergence)
- SPIKE_FADE / SPIKE_FADE_INV (Spike Fade)
- COMPOSITE / COMPOSITE_INV (Composite Consensus)
"""
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from typing import Dict, Any
from backtest.simulate import simulate
from paper.indicators import Indicators


def _prepare_df(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Precompute all technical indicators required by RetailBot2 strategies."""
    df = df.copy().reset_index(drop=True)
    c_close = df["close"]

    # ATR
    atr_period = params.get("RB2_ATR_PERIOD", 14)
    df["atr"] = Indicators.atr(df, atr_period)

    # RSI
    rsi_period = params.get("RSI_PERIOD", 14)
    df["rsi"] = Indicators.rsi(c_close, rsi_period)

    # EMAs
    ma_fast = params.get("MA_FAST", 9)
    ma_slow = params.get("MA_SLOW", 21)
    ma_trend = params.get("MA_TREND", 200)
    df["ema_fast"] = Indicators.ema(c_close, ma_fast)
    df["ema_slow"] = Indicators.ema(c_close, ma_slow)
    df["ema_trend"] = Indicators.ema(c_close, ma_trend)

    # MACD
    macd_fast = params.get("MACD_FAST", 12)
    macd_slow = params.get("MACD_SLOW", 26)
    macd_sig = params.get("MACD_SIGNAL_PERIOD", 9)
    df["macd_line"], df["macd_sig"], df["macd_hist"] = Indicators.macd(
        c_close, macd_fast, macd_slow, macd_sig
    )

    # Bollinger Bands
    bb_period = params.get("BB_PERIOD", 20)
    bb_std = params.get("BB_STD", 2.0)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = Indicators.bollinger(
        c_close, bb_period, bb_std
    )

    # Candle body and range
    df["body"] = (df["close"] - df["open"]).abs()
    df["range"] = df["high"] - df["low"]
    return df


def _retail_stop_target(direction: str, raw_entry_price: float, atr_at_signal: float, row, params: dict):
    rr = params.get("RB2_RISK_REWARD_RATIO", 2.0)
    sl_mult = params.get("ATR_SL_MULTIPLIER", 1.5)
    risk = (atr_at_signal if atr_at_signal > 0 else raw_entry_price * 0.01) * sl_mult
    if direction == "LONG":
        return raw_entry_price - risk, raw_entry_price + risk * rr
    else:
        return raw_entry_price + risk, raw_entry_price - risk * rr


# ============================================================================
# 1. BB_REV / BB_REV_INV: Bollinger Band Reversion
# ============================================================================
def _bb_long(row, prev_row, params: dict) -> bool:
    if row.name < params.get("BB_PERIOD", 20) + 8:
        return False
    lower = row.get("bb_lower")
    if pd.isna(lower) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    price = float(row["close"])
    prev_price = float(prev_row["close"])
    prox = params.get("BB_PROXIMITY_PCT", 0.003)
    near_lower = abs(price - lower) / (lower + 1e-10) < prox
    return near_lower or (prev_price < lower and price > lower)


def _bb_short(row, prev_row, params: dict) -> bool:
    if row.name < params.get("BB_PERIOD", 20) + 8:
        return False
    upper = row.get("bb_upper")
    if pd.isna(upper) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    price = float(row["close"])
    prev_price = float(prev_row["close"])
    prox = params.get("BB_PROXIMITY_PCT", 0.003)
    near_upper = abs(price - upper) / (upper + 1e-10) < prox
    return (near_upper or (prev_price > upper and price < upper)) and not _bb_long(row, prev_row, params)


def run_bb_rev(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _bb_long, _bb_short, _retail_stop_target)


def run_bb_rev_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _bb_short, _bb_long, _retail_stop_target)


# ============================================================================
# 2. RSI_CTR / RSI_CTR_INV: RSI Counter-Trend
# ============================================================================
def _rsi_long(row, prev_row, params: dict) -> bool:
    if row.name < 48:
        return False
    rsi = row.get("rsi")
    prev_rsi = prev_row.get("rsi")
    if pd.isna(rsi) or pd.isna(prev_rsi) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    oversold = params.get("RB2_RSI_OVERSOLD", 35)
    zone_width = params.get("RSI_ZONE_WIDTH", 5)
    rsi_was_oversold = prev_rsi < oversold
    rsi_recovering = rsi > prev_rsi
    rsi_in_bounce_zone = oversold <= rsi <= (oversold + zone_width)
    return rsi_was_oversold and rsi_recovering and rsi_in_bounce_zone


def _rsi_short(row, prev_row, params: dict) -> bool:
    if row.name < 48:
        return False
    rsi = row.get("rsi")
    prev_rsi = prev_row.get("rsi")
    if pd.isna(rsi) or pd.isna(prev_rsi) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    overbought = params.get("RB2_RSI_OVERBOUGHT", 65)
    zone_width = params.get("RSI_ZONE_WIDTH", 5)
    rsi_was_overbought = prev_rsi > overbought
    rsi_rejecting = rsi < prev_rsi
    rsi_in_reject_zone = (overbought - zone_width) <= rsi <= overbought
    return rsi_was_overbought and rsi_rejecting and rsi_in_reject_zone


def run_rsi_ctr(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _rsi_long, _rsi_short, _retail_stop_target)


def run_rsi_ctr_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _rsi_short, _rsi_long, _retail_stop_target)


# ============================================================================
# 3. MA_WHIP / MA_WHIP_INV: MA Crossover Whipsaw
# ============================================================================
def _ma_long(row, prev_row, params: dict) -> bool:
    if row.name < 48:
        return False
    fast = row.get("ema_fast")
    slow = row.get("ema_slow")
    prev_fast = prev_row.get("ema_fast")
    prev_slow = prev_row.get("ema_slow")
    atr = row.get("atr", 0)
    if pd.isna(fast) or pd.isna(slow) or pd.isna(prev_fast) or pd.isna(prev_slow) or atr <= 0:
        return False
    min_sep = atr * 0.05
    crossed_up = prev_fast <= prev_slow and fast > slow
    separation_ok = abs(fast - slow) > min_sep
    return crossed_up and separation_ok


def _ma_short(row, prev_row, params: dict) -> bool:
    if row.name < 48:
        return False
    fast = row.get("ema_fast")
    slow = row.get("ema_slow")
    prev_fast = prev_row.get("ema_fast")
    prev_slow = prev_row.get("ema_slow")
    atr = row.get("atr", 0)
    if pd.isna(fast) or pd.isna(slow) or pd.isna(prev_fast) or pd.isna(prev_slow) or atr <= 0:
        return False
    min_sep = atr * 0.05
    crossed_down = prev_fast >= prev_slow and fast < slow
    separation_ok = abs(fast - slow) > min_sep
    return crossed_down and separation_ok


def run_ma_whip(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _ma_long, _ma_short, _retail_stop_target)


def run_ma_whip_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _ma_short, _ma_long, _retail_stop_target)


# ============================================================================
# 4. SPIKE_FADE / SPIKE_FADE_INV: Spike Fade
# ============================================================================
def _spike_long(row, prev_row, params: dict) -> bool:
    if row.name < 28:
        return False
    atr = row.get("atr", 0)
    prev_atr = prev_row.get("atr", 0)
    if atr <= 0 or prev_atr <= 0:
        return False
    mult = params.get("SPIKE_ATR_MULTIPLIER", 2.5)
    prev_body = abs(prev_row["close"] - prev_row["open"])
    bear_spike = (prev_body > prev_atr * mult) and (prev_row["close"] < prev_row["open"])
    confirm_green = row["close"] > row["open"]
    return bear_spike and confirm_green


def _spike_short(row, prev_row, params: dict) -> bool:
    if row.name < 28:
        return False
    atr = row.get("atr", 0)
    prev_atr = prev_row.get("atr", 0)
    if atr <= 0 or prev_atr <= 0:
        return False
    mult = params.get("SPIKE_ATR_MULTIPLIER", 2.5)
    prev_body = abs(prev_row["close"] - prev_row["open"])
    bull_spike = (prev_body > prev_atr * mult) and (prev_row["close"] > prev_row["open"])
    confirm_red = row["close"] < row["open"]
    return bull_spike and confirm_red


def run_spike_fade(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _spike_long, _spike_short, _retail_stop_target)


def run_spike_fade_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _spike_short, _spike_long, _retail_stop_target)


# ============================================================================
# 5. DBL_PAT / DBL_PAT_INV: Double Top / Bottom Pattern
# ============================================================================
def _precompute_double_pivots(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    lookback = params.get("PATTERN_LOOKBACK", 60)
    tolerance = params.get("PATTERN_TOLERANCE", 0.002)
    min_sep = params.get("PATTERN_MIN_BARS_APART", 10)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    long = np.zeros(len(df), dtype=bool)
    short = np.zeros(len(df), dtype=bool)

    for i in range(lookback + 8, len(df)):
        window = slice(i - lookback, i + 1)
        for values, compare, direction in ((highs, np.greater, "SHORT"), (lows, np.less, "LONG")):
            recent = values[window]
            pivots = argrelextrema(recent, compare, order=4)[0]
            if len(pivots) < 2 or pivots[-1] - pivots[-2] < min_sep:
                continue
            first, last = recent[pivots[-2:]]
            if abs(first - last) / (first + 1e-10) >= tolerance:
                continue
            if direction == "SHORT" and closes[i] < max(first, last) * (1 - tolerance / 2):
                short[i] = True
                break
            if direction == "LONG" and closes[i] > min(first, last) * (1 + tolerance / 2):
                long[i] = True
    valid_atr = df["atr"].gt(0)
    return df.assign(dbl_bottom=long & valid_atr, dbl_top=short & valid_atr)


def _dbl_long(row, prev_row, params: dict) -> bool:
    return bool(row["dbl_bottom"])


def _dbl_short(row, prev_row, params: dict) -> bool:
    return bool(row["dbl_top"])


def run_dbl_pat(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_double_pivots(_prepare_df(df, params), params)
    return simulate(df, params, _dbl_long, _dbl_short, _retail_stop_target)


def run_dbl_pat_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_double_pivots(_prepare_df(df, params), params)
    return simulate(df, params, _dbl_short, _dbl_long, _retail_stop_target)


# ============================================================================
# 6. MACD_DIV / MACD_DIV_INV: MACD Divergence
# ============================================================================
def _precompute_macd_div(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    lookback = params.get("DIVERGENCE_LOOKBACK", 30)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    hist = df["macd_hist"].to_numpy()
    long = np.zeros(len(df), dtype=bool)
    short = np.zeros(len(df), dtype=bool)

    if lookback + 1 >= 20:
        for i in range(lookback + 38, len(df)):
            start = i - lookback
            window = slice(start, i + 1)
            for values, compare, divergence, flags in (
                (lows, np.less, np.greater, long),
                (highs, np.greater, np.less, short),
            ):
                pivots = argrelextrema(values[window], compare, order=3)[0]
                if len(pivots) < 2 or pivots[-1] - pivots[-2] < 5:
                    continue
                first, last = start + pivots[-2:]
                if compare(values[last], values[first]) and divergence(hist[last], hist[first]):
                    flags[i] = True
                    break
    valid_signal = df["atr"].gt(0) & df["macd_hist"].notna()
    return df.assign(bull_div=long & valid_signal, bear_div=short & valid_signal)


def _macd_long(row, prev_row, params: dict) -> bool:
    return bool(row["bull_div"])


def _macd_short(row, prev_row, params: dict) -> bool:
    return bool(row["bear_div"])


def run_macd_div(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_macd_div(_prepare_df(df, params), params)
    return simulate(df, params, _macd_long, _macd_short, _retail_stop_target)


def run_macd_div_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_macd_div(_prepare_df(df, params), params)
    return simulate(df, params, _macd_short, _macd_long, _retail_stop_target)


# ============================================================================
# 7. SR_TRAP / SR_TRAP_INV: Support & Resistance Bounce Trap
# ============================================================================
def _precompute_sr_traps(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    lookback = params.get("SR_LOOKBACK", 50)
    threshold = params.get("SR_TOUCH_THRESHOLD", 0.0015)
    min_touches = params.get("SR_MIN_TOUCHES", 2)
    long = np.zeros(len(df), dtype=bool)
    short = np.zeros(len(df), dtype=bool)

    for i in range(lookback + 8, len(df)):
        # The helper requires five extra rows and takes its own lookback.
        supports, resistances = Indicators.find_sr_levels(
            df.iloc[i - lookback - 4:i + 1], lookback, threshold, min_touches
        )
        price = float(df["close"].iat[i])
        long[i] = any(abs(price - level) / (level + 1e-10) < threshold for level in supports[:2])
        short[i] = not long[i] and any(
            abs(price - level) / (level + 1e-10) < threshold for level in resistances[:2]
        )
    valid_atr = df["atr"].gt(0)
    return df.assign(sr_long=long & valid_atr, sr_short=short & valid_atr)


def _sr_long(row, prev_row, params: dict) -> bool:
    return bool(row["sr_long"])


def _sr_short(row, prev_row, params: dict) -> bool:
    return bool(row["sr_short"])


def run_sr_trap(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_sr_traps(_prepare_df(df, params), params)
    return simulate(df, params, _sr_long, _sr_short, _retail_stop_target)


def run_sr_trap_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _precompute_sr_traps(_prepare_df(df, params), params)
    return simulate(df, params, _sr_short, _sr_long, _retail_stop_target)


# ============================================================================
# 8. COMPOSITE / COMPOSITE_INV: Retail Consensus Strategy
# ============================================================================
def _prepare_composite_df(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = _prepare_df(df, params)
    df = _precompute_sr_traps(df, params)
    df = _precompute_macd_div(df, params)
    return _precompute_double_pivots(df, params)


def _composite_votes(row, prev_row, params: dict) -> tuple[int, int]:
    votes_long = sum([
        _rsi_long(row, prev_row, params),
        _ma_long(row, prev_row, params),
        _sr_long(row, prev_row, params),
        _macd_long(row, prev_row, params),
        _bb_long(row, prev_row, params),
        _dbl_long(row, prev_row, params),
        _spike_long(row, prev_row, params),
    ])
    votes_short = sum([
        _rsi_short(row, prev_row, params),
        _ma_short(row, prev_row, params),
        _sr_short(row, prev_row, params),
        _macd_short(row, prev_row, params),
        _bb_short(row, prev_row, params),
        _dbl_short(row, prev_row, params),
        _spike_short(row, prev_row, params),
    ])
    return votes_long, votes_short


def _composite_long(row, prev_row, params: dict) -> bool:
    votes_long, votes_short = _composite_votes(row, prev_row, params)
    return votes_long > 0 and votes_long >= votes_short


def _composite_short(row, prev_row, params: dict) -> bool:
    votes_long, votes_short = _composite_votes(row, prev_row, params)
    return votes_short > votes_long


def run_composite(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_composite_df(df, params)
    return simulate(df, params, _composite_long, _composite_short, _retail_stop_target)


def run_composite_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_composite_df(df, params)
    return simulate(df, params, _composite_short, _composite_long, _retail_stop_target)


# ============================================================================
# REGISTRY & EXPORTS
# ============================================================================
RETAILBOT2_STRATEGIES: Dict[str, Any] = {
    # Shadow strategies
    "BB_REV": run_bb_rev,
    "DBL_PAT": run_dbl_pat,
    "RSI_CTR": run_rsi_ctr,
    "MA_WHIP": run_ma_whip,
    "SR_TRAP": run_sr_trap,
    "MACD_DIV": run_macd_div,
    "SPIKE_FADE": run_spike_fade,
    "COMPOSITE": run_composite,

    # Inverse strategies
    "BB_REV_INV": run_bb_rev_inverse,
    "DBL_PAT_INV": run_dbl_pat_inverse,
    "RSI_CTR_INV": run_rsi_ctr_inverse,
    "MA_WHIP_INV": run_ma_whip_inverse,
    "SR_TRAP_INV": run_sr_trap_inverse,
    "MACD_DIV_INV": run_macd_div_inverse,
    "SPIKE_FADE_INV": run_spike_fade_inverse,
    "COMPOSITE_INV": run_composite_inverse,

    # Backward-compatible & user-specified aliases
    "DBL_PAT_INV_inverse": run_dbl_pat_inverse,
    "BB_REV_INV_inverse": run_bb_rev_inverse,
    "inverse_BB_REV": run_bb_rev_inverse,
    "inverse_DBL_PAT": run_dbl_pat_inverse,
    "inverse_RSI_CTR": run_rsi_ctr_inverse,
    "inverse_MA_WHIP": run_ma_whip_inverse,
    "inverse_SR_TRAP": run_sr_trap_inverse,
    "inverse_MACD_DIV": run_macd_div_inverse,
    "inverse_SPIKE_FADE": run_spike_fade_inverse,
    "inverse_COMPOSITE": run_composite_inverse,
}

RETAILBOT2_STRATEGY_NAMES = set(RETAILBOT2_STRATEGIES.keys())
