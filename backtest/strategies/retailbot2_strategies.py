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
from typing import Dict, Any, List
from backtest.simulate import simulate
from paper.retailbot2 import Indicators


def _prepare_df(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Precompute all technical indicators required by RetailBot2 strategies."""
    df = df.copy().reset_index(drop=True)
    c_close = df["close"]

    # ATR
    atr_period = params.get("ATR_PERIOD", 14)
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
    rr = params.get("RISK_REWARD_RATIO", 2.0)
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
    lower = row.get("bb_lower")
    if pd.isna(lower) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    price = float(row["close"])
    prev_price = float(prev_row["close"])
    prox = params.get("BB_PROXIMITY_PCT", 0.003)
    near_lower = abs(price - lower) / (lower + 1e-10) < prox
    return near_lower or (prev_price < lower and price > lower)


def _bb_short(row, prev_row, params: dict) -> bool:
    upper = row.get("bb_upper")
    if pd.isna(upper) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    price = float(row["close"])
    prev_price = float(prev_row["close"])
    prox = params.get("BB_PROXIMITY_PCT", 0.003)
    near_upper = abs(price - upper) / (upper + 1e-10) < prox
    return near_upper or (prev_price > upper and price < upper)


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
    rsi = row.get("rsi")
    prev_rsi = prev_row.get("rsi")
    if pd.isna(rsi) or pd.isna(prev_rsi) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    oversold = params.get("RSI_OVERSOLD", 35)
    zone_width = params.get("RSI_ZONE_WIDTH", 5)
    rsi_was_oversold = prev_rsi < oversold
    rsi_recovering = rsi > prev_rsi
    rsi_in_bounce_zone = oversold <= rsi <= (oversold + zone_width)
    return rsi_was_oversold and rsi_recovering and rsi_in_bounce_zone


def _rsi_short(row, prev_row, params: dict) -> bool:
    rsi = row.get("rsi")
    prev_rsi = prev_row.get("rsi")
    if pd.isna(rsi) or pd.isna(prev_rsi) or pd.isna(row.get("atr")) or row.get("atr", 0) <= 0:
        return False
    overbought = params.get("RSI_OVERBOUGHT", 65)
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
    df = df.copy()
    n = len(df)
    lookback = params.get("PATTERN_LOOKBACK", 60)
    tol = params.get("PATTERN_TOLERANCE", 0.002)
    min_sep = params.get("PATTERN_MIN_BARS_APART", 10)

    is_double_bottom = [False] * n
    is_double_top = [False] * n

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    recent_peaks = []    # (idx, price)
    recent_troughs = []  # (idx, price)

    order = 4
    for i in range(order, n - order):
        # Peak check
        val_h = highs[i]
        if all(val_h > highs[i - k] for k in range(1, order + 1)) and all(val_h >= highs[i + k] for k in range(1, order + 1)):
            recent_peaks.append((i, val_h))
            recent_peaks = [p for p in recent_peaks if i - p[0] <= lookback]
            if len(recent_peaks) >= 2:
                p1_idx, p1_val = recent_peaks[-2]
                p2_idx, p2_val = recent_peaks[-1]
                if p2_idx - p1_idx >= min_sep and abs(p1_val - p2_val) / (p1_val + 1e-10) < tol:
                    pattern_high = max(p1_val, p2_val)
                    if closes[i] < pattern_high * (1 - tol / 2):
                        is_double_top[i] = True

        # Trough check
        val_l = lows[i]
        if all(val_l < lows[i - k] for k in range(1, order + 1)) and all(val_l <= lows[i + k] for k in range(1, order + 1)):
            recent_troughs.append((i, val_l))
            recent_troughs = [t for t in recent_troughs if i - t[0] <= lookback]
            if len(recent_troughs) >= 2:
                t1_idx, t1_val = recent_troughs[-2]
                t2_idx, t2_val = recent_troughs[-1]
                if t2_idx - t1_idx >= min_sep and abs(t1_val - t2_val) / (t1_val + 1e-10) < tol:
                    pattern_low = min(t1_val, t2_val)
                    if closes[i] > pattern_low * (1 + tol / 2):
                        is_double_bottom[i] = True

    df["dbl_bottom"] = is_double_bottom
    df["dbl_top"] = is_double_top
    return df


def _dbl_long(row, prev_row, params: dict) -> bool:
    return bool(row.get("dbl_bottom", False)) and not bool(prev_row.get("dbl_bottom", False))


def _dbl_short(row, prev_row, params: dict) -> bool:
    return bool(row.get("dbl_top", False)) and not bool(prev_row.get("dbl_top", False))


def run_dbl_pat(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_double_pivots(df, params)
    return simulate(df, params, _dbl_long, _dbl_short, _retail_stop_target)


def run_dbl_pat_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_double_pivots(df, params)
    return simulate(df, params, _dbl_short, _dbl_long, _retail_stop_target)


# ============================================================================
# 6. MACD_DIV / MACD_DIV_INV: MACD Divergence
# ============================================================================
def _precompute_macd_div(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    lb = params.get("DIVERGENCE_LOOKBACK", 30)
    is_bull_div = [False] * n
    is_bear_div = [False] * n

    highs = df["high"].values
    lows = df["low"].values
    hist = df["macd_hist"].values

    recent_min = []
    recent_max = []

    order = 3
    for i in range(order, n - order):
        v_low = lows[i]
        if all(v_low < lows[i - k] for k in range(1, order + 1)) and all(v_low <= lows[i + k] for k in range(1, order + 1)):
            recent_min.append((i, v_low, hist[i]))
            recent_min = [m for m in recent_min if i - m[0] <= lb]
            if len(recent_min) >= 2:
                i1, l1, h1 = recent_min[-2]
                i2, l2, h2 = recent_min[-1]
                if i2 - i1 >= 5 and l2 < l1 and h2 > h1:
                    is_bull_div[i] = True

        v_high = highs[i]
        if all(v_high > highs[i - k] for k in range(1, order + 1)) and all(v_high >= highs[i + k] for k in range(1, order + 1)):
            recent_max.append((i, v_high, hist[i]))
            recent_max = [m for m in recent_max if i - m[0] <= lb]
            if len(recent_max) >= 2:
                i1, hg1, h1 = recent_max[-2]
                i2, hg2, h2 = recent_max[-1]
                if i2 - i1 >= 5 and hg2 > hg1 and h2 < h1:
                    is_bear_div[i] = True

    df["bull_div"] = is_bull_div
    df["bear_div"] = is_bear_div
    return df


def _macd_long(row, prev_row, params: dict) -> bool:
    return bool(row.get("bull_div", False)) and not bool(prev_row.get("bull_div", False))


def _macd_short(row, prev_row, params: dict) -> bool:
    return bool(row.get("bear_div", False)) and not bool(prev_row.get("bear_div", False))


def run_macd_div(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_macd_div(df, params)
    return simulate(df, params, _macd_long, _macd_short, _retail_stop_target)


def run_macd_div_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_macd_div(df, params)
    return simulate(df, params, _macd_short, _macd_long, _retail_stop_target)


# ============================================================================
# 7. SR_TRAP / SR_TRAP_INV: Support & Resistance Bounce Trap
# ============================================================================
def _precompute_sr_traps(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    lookback = params.get("SR_LOOKBACK", 50)
    thresh = params.get("SR_TOUCH_THRESHOLD", 0.0015)
    sr_long = [False] * n
    sr_short = [False] * n

    # Step over df with window strides for efficiency
    step = 5
    for i in range(lookback + 5, n, step):
        slice_df = df.iloc[max(0, i - lookback):i]
        supports, resistances = Indicators.find_sr_levels(slice_df, lookback, thresh, 2)
        end_idx = min(n, i + step)
        for j in range(i, end_idx):
            price = float(df["close"].iloc[j])
            for s in supports[:2]:
                if abs(price - s) / (s + 1e-10) < thresh:
                    sr_long[j] = True
                    break
            for r in resistances[:2]:
                if abs(price - r) / (r + 1e-10) < thresh:
                    sr_short[j] = True
                    break

    df["sr_long"] = sr_long
    df["sr_short"] = sr_short
    return df


def _sr_long(row, prev_row, params: dict) -> bool:
    return bool(row.get("sr_long", False)) and not bool(prev_row.get("sr_long", False))


def _sr_short(row, prev_row, params: dict) -> bool:
    return bool(row.get("sr_short", False)) and not bool(prev_row.get("sr_short", False))


def run_sr_trap(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_sr_traps(df, params)
    return simulate(df, params, _sr_long, _sr_short, _retail_stop_target)


def run_sr_trap_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    df = _precompute_sr_traps(df, params)
    return simulate(df, params, _sr_short, _sr_long, _retail_stop_target)


# ============================================================================
# 8. COMPOSITE / COMPOSITE_INV: Retail Consensus Strategy
# ============================================================================
def _composite_long(row, prev_row, params: dict) -> bool:
    votes_long = sum([
        _bb_long(row, prev_row, params),
        _rsi_long(row, prev_row, params),
        _ma_long(row, prev_row, params),
        _spike_long(row, prev_row, params),
    ])
    votes_short = sum([
        _bb_short(row, prev_row, params),
        _rsi_short(row, prev_row, params),
        _ma_short(row, prev_row, params),
        _spike_short(row, prev_row, params),
    ])
    return votes_long >= 2 and votes_long > votes_short


def _composite_short(row, prev_row, params: dict) -> bool:
    votes_long = sum([
        _bb_long(row, prev_row, params),
        _rsi_long(row, prev_row, params),
        _ma_long(row, prev_row, params),
        _spike_long(row, prev_row, params),
    ])
    votes_short = sum([
        _bb_short(row, prev_row, params),
        _rsi_short(row, prev_row, params),
        _ma_short(row, prev_row, params),
        _spike_short(row, prev_row, params),
    ])
    return votes_short >= 2 and votes_short > votes_long


def run_composite(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
    return simulate(df, params, _composite_long, _composite_short, _retail_stop_target)


def run_composite_inverse(df: pd.DataFrame, params: dict) -> list:
    df = _prepare_df(df, params)
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
