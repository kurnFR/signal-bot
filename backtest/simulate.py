"""
Generic trade simulation engine shared by every strategy in backtest/strategies/.

Each strategy supplies:
  - long_condition(row, prev_row, params) -> bool
  - short_condition(row, prev_row, params) -> bool
  - stop_target(direction, raw_entry_price, atr_at_signal, row, params) -> (stop_loss, take_profit)

...and this module handles the shared mechanics that are identical
regardless of strategy: executing at the next bar's open (no lookahead),
walking forward bar-by-bar to see which of SL/TP/timeout hits first,
applying fees/slippage, computing R-multiples, and only holding one
position at a time. This logic was already verified against hand-crafted
synthetic data (entry timing, SL/TP correctness, no-lookahead guarantee) --
reusing it for new strategies means we don't have to re-verify that
mechanical correctness for every new strategy, only the new entry logic.

TRAILING STOP (opt-in via params["USE_TRAILING_STOP"] = True): once a
trade's unrealized profit reaches TRAIL_ACTIVATION_R x its initial risk,
the stop-loss starts trailing behind the best price seen so far by
TRAIL_DISTANCE_ATR_MULT x ATR (only ever moving in the favorable
direction, never loosening) -- and the fixed take-profit is REMOVED
entirely (the whole point is not to cap the upside at a fixed target).
Before activation, behaves identically to the fixed-stop path. Default
(USE_TRAILING_STOP unset or False) is byte-for-byte the original,
already-tested fixed SL/TP behavior -- no existing strategy's results
change unless it explicitly opts in.
"""
import pandas as pd


def simulate(df: pd.DataFrame, params: dict, long_condition, short_condition, stop_target) -> list:
    """
    df: merged ohlcv+features (+ any strategy-specific columns already
        added by the caller, e.g. EMA columns), sorted ascending, closed
        candles only.
    params: dict of whatever the strategy's condition/stop_target
        functions need, plus the shared execution params:
        BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT, BACKTEST_MAX_HOLD_BARS,
        and optionally USE_TRAILING_STOP, TRAIL_ACTIVATION_R,
        TRAIL_DISTANCE_ATR_MULT.

    Returns a list of trade dicts (same schema regardless of strategy, so
    metrics.py and the DB layer don't need to know which strategy ran).
    """
    trades = []
    n = len(df)
    i = 1  # start at 1 so prev_row (i-1) always exists

    fee = params["BACKTEST_FEE_PCT"]
    slip = params["BACKTEST_SLIPPAGE_PCT"]
    max_hold = params["BACKTEST_MAX_HOLD_BARS"]
    use_trailing = params.get("USE_TRAILING_STOP", False)
    trail_activation_r = params.get("TRAIL_ACTIVATION_R", 1.0)
    trail_distance_atr_mult = params.get("TRAIL_DISTANCE_ATR_MULT", 1.5)

    while i < n - 1:  # need i+1 to exist for entry execution
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        direction = None
        if long_condition(row, prev_row, params):
            direction = "LONG"
        elif short_condition(row, prev_row, params):
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

        stop_loss, take_profit = stop_target(direction, raw_entry_price, atr_at_signal, row, params)

        if direction == "LONG":
            entry_price = raw_entry_price * (1 + slip)
        else:
            entry_price = raw_entry_price * (1 - slip)

        initial_risk_price = abs(raw_entry_price - stop_loss)

        exit_price = None
        exit_reason = None
        exit_idx = None

        current_stop = stop_loss
        trailing_active = False
        favorable_extreme = raw_entry_price

        j = entry_idx
        while j < n:
            bar = df.iloc[j]

            if use_trailing and initial_risk_price > 0:
                # Candle high/low ordering is unknown: check the existing
                # stop before using this candle to move or activate it.
                if direction == "LONG":
                    hit_sl = bar["low"] <= current_stop
                    if hit_sl:
                        exit_price = current_stop
                        exit_reason = "TRAIL" if trailing_active else "SL"
                else:
                    hit_sl = bar["high"] >= current_stop
                    if hit_sl:
                        exit_price = current_stop
                        exit_reason = "TRAIL" if trailing_active else "SL"
                # No fixed take-profit in trailing mode -- upside is uncapped.
                if exit_price is None:
                    if direction == "LONG":
                        favorable_extreme = max(favorable_extreme, bar["high"])
                        unrealized_r = (favorable_extreme - raw_entry_price) / initial_risk_price
                        if not trailing_active and unrealized_r >= trail_activation_r:
                            trailing_active = True
                        if trailing_active:
                            candidate_stop = favorable_extreme - trail_distance_atr_mult * atr_at_signal
                            current_stop = max(current_stop, candidate_stop)
                    else:
                        favorable_extreme = min(favorable_extreme, bar["low"])
                        unrealized_r = (raw_entry_price - favorable_extreme) / initial_risk_price
                        if not trailing_active and unrealized_r >= trail_activation_r:
                            trailing_active = True
                        if trailing_active:
                            candidate_stop = favorable_extreme + trail_distance_atr_mult * atr_at_signal
                            current_stop = min(current_stop, candidate_stop)
            else:
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
            exit_idx = n - 1
            exit_price = df.iloc[exit_idx]["close"]
            exit_reason = "END_OF_DATA"

        if direction == "LONG":
            exit_price_after_costs = exit_price * (1 - slip)
            gross_return_pct = (exit_price_after_costs - entry_price) / entry_price * 100
        else:
            exit_price_after_costs = exit_price * (1 + slip)
            gross_return_pct = (entry_price - exit_price_after_costs) / entry_price * 100

        net_return_pct = gross_return_pct - (fee * 100 * 2)
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

        i = exit_idx + 1  # only one position open at a time

    return trades
