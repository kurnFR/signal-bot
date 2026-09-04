"""
Confluence-based exhaustion/reversal strategy, built from your original
parameter set (VOLUME_MULTIPLIER, RSI thresholds, ATR-based risk, S/R
proximity, RISK_REWARD_RATIO).

Logic (long side; short is the mirror image at resistance):
  - RSI oversold (RSI_OVERSOLD)
  - price at/near rolling support (within SR_PROXIMITY_PCT)
  - volume elevated vs its rolling average (volume_ratio >= VOLUME_MULTIPLIER)
  - market volatile enough to be worth trading (atr_pct >= VOLATILITY_THRESHOLD_PCT)
  - MACD histogram turning up (momentum shifting in the entry's favor)

This is a single, explicit, auditable rule set -- not a black box. Whether
it's actually profitable is exactly what Step 3's backtest exists to find
out; nothing here is assumed to work until backtested.

CRITICAL -- no lookahead bias:
  - A signal decided using bar i's data can only execute at bar i+1's open
    (you can't know a candle closed until it closes, and can't trade at a
    price before it's quoted).
  - support/resistance/fib levels in the `features` table already exclude
    the current bar from their own rolling window (see features/indicators.py),
    so a level is never derived from data that includes the bar being
    evaluated against it.
  - Only ONE position open at a time per symbol/timeframe (matches how a
    single bot instance would actually trade it) -- a new signal is ignored
    while a trade is open.

This module is imported by the backtester (Step 3) AND is meant to be
imported unmodified by the live signal engine (Step 6+) -- never
reimplement this logic elsewhere.
"""
import pandas as pd


def _long_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["rsi", "support", "volume_ratio", "atr_pct", "macd_hist"]) or pd.isna(prev_row["macd_hist"]):
        return False
    near_support = abs(row["close"] - row["support"]) / row["support"] * 100 <= params["SR_PROXIMITY_PCT"]
    return (
        row["rsi"] < params["RSI_OVERSOLD"]
        and near_support
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
        and row["macd_hist"] > prev_row["macd_hist"]
    )


def _short_condition(row, prev_row, params):
    if any(pd.isna(row[c]) for c in ["rsi", "resistance", "volume_ratio", "atr_pct", "macd_hist"]) or pd.isna(prev_row["macd_hist"]):
        return False
    near_resistance = abs(row["close"] - row["resistance"]) / row["resistance"] * 100 <= params["SR_PROXIMITY_PCT"]
    return (
        row["rsi"] > params["RSI_OVERBOUGHT"]
        and near_resistance
        and row["volume_ratio"] >= params["VOLUME_MULTIPLIER"]
        and row["atr_pct"] >= params["VOLATILITY_THRESHOLD_PCT"]
        and row["macd_hist"] < prev_row["macd_hist"]
    )


def run_backtest_single(df: pd.DataFrame, params: dict) -> list:
    """
    df: merged ohlcv+features for one symbol/market/timeframe, sorted
        ascending by open_time, closed candles only (caller's responsibility
        to filter out any still-forming last row).
    params: dict with keys RSI_OVERSOLD, RSI_OVERBOUGHT, SR_PROXIMITY_PCT,
        VOLUME_MULTIPLIER, VOLATILITY_THRESHOLD_PCT, RISK_REWARD_RATIO,
        BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT, BACKTEST_MAX_HOLD_BARS

    Returns a list of trade dicts, one per completed trade.
    """
    trades = []
    n = len(df)
    i = 1  # start at 1 so prev_row (i-1) always exists

    fee = params["BACKTEST_FEE_PCT"]
    slip = params["BACKTEST_SLIPPAGE_PCT"]
    max_hold = params["BACKTEST_MAX_HOLD_BARS"]

    while i < n - 1:  # need i+1 to exist for entry execution
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        direction = None
        if _long_condition(row, prev_row, params):
            direction = "LONG"
        elif _short_condition(row, prev_row, params):
            direction = "SHORT"

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry_bar = df.iloc[entry_idx]
        raw_entry_price = entry_bar["open"]
        atr_at_signal = row["atr"]
        if pd.isna(atr_at_signal) or atr_at_signal <= 0:
            i += 1
            continue

        if direction == "LONG":
            entry_price = raw_entry_price * (1 + slip)  # buy slightly worse than quoted (slippage)
            stop_loss = raw_entry_price - atr_at_signal
            take_profit = raw_entry_price + atr_at_signal * params["RISK_REWARD_RATIO"]
        else:
            entry_price = raw_entry_price * (1 - slip)  # sell slightly worse than quoted
            stop_loss = raw_entry_price + atr_at_signal
            take_profit = raw_entry_price - atr_at_signal * params["RISK_REWARD_RATIO"]

        exit_price = None
        exit_reason = None
        exit_idx = None

        j = entry_idx
        while j < n:
            bar = df.iloc[j]
            if direction == "LONG":
                hit_sl = bar["low"] <= stop_loss
                hit_tp = bar["high"] >= take_profit
                if hit_sl:  # conservative: if both possible in the same bar, assume SL hit first
                    exit_price, exit_reason = stop_loss, "SL"
                elif hit_tp:
                    exit_price, exit_reason = take_profit, "TP"
            else:
                hit_sl = bar["high"] >= stop_loss
                hit_tp = bar["low"] <= take_profit
                if hit_sl:
                    exit_price, exit_reason = stop_loss, "SL"
                elif hit_tp:
                    exit_price, exit_reason = take_profit, "TP"

            if exit_price is not None:
                exit_idx = j
                break
            if j - entry_idx >= max_hold:
                exit_price, exit_reason, exit_idx = bar["close"], "TIMEOUT", j
                break
            j += 1

        if exit_price is None:
            # ran off the end of history still holding -- close at last available price
            exit_idx = n - 1
            exit_price = df.iloc[exit_idx]["close"]
            exit_reason = "END_OF_DATA"

        if direction == "LONG":
            exit_price_after_costs = exit_price * (1 - slip)
            gross_return_pct = (exit_price_after_costs - entry_price) / entry_price * 100
        else:
            exit_price_after_costs = exit_price * (1 + slip)
            gross_return_pct = (entry_price - exit_price_after_costs) / entry_price * 100

        net_return_pct = gross_return_pct - (fee * 100 * 2)  # entry + exit fee
        risk_pct = abs(entry_price - stop_loss) / entry_price * 100
        r_multiple = net_return_pct / risk_pct if risk_pct > 0 else None

        trades.append({
            "direction": direction,
            "signal_open_time": int(row["open_time"]),
            "entry_time": int(entry_bar["open_time"]),
            "entry_price": float(entry_price),
            "exit_time": int(df.iloc[exit_idx]["open_time"]),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "net_return_pct": float(net_return_pct),
            "r_multiple": float(r_multiple) if r_multiple is not None else None,
            "holding_bars": exit_idx - entry_idx,
        })

        i = exit_idx + 1  # only one position open at a time -- resume scanning after this trade closes

    return trades
