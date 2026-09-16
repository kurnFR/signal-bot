"""Generic trade simulation engine shared by all strategies."""
import pandas as pd

from backtest.accounting import calculate_trade_accounting


def simulate(df: pd.DataFrame, params: dict, long_condition, short_condition, stop_target) -> list:
    """Simulate closed-candle signals with next-bar-open execution.

    ``_simulation_start_open_time`` and ``_simulation_end_open_time`` are
    runtime-only chronological bounds. Historical rows remain available for
    indicator warm-up, but no signal/entry/exit is allowed outside the window.
    """
    trades = []
    n = len(df)
    i = 1
    fee = params["BACKTEST_FEE_PCT"]
    slip = params["BACKTEST_SLIPPAGE_PCT"]
    max_hold = params["BACKTEST_MAX_HOLD_BARS"]
    use_trailing = params.get("USE_TRAILING_STOP", False)
    trail_activation_r = params.get("TRAIL_ACTIVATION_R", 1.0)
    trail_distance_atr_mult = params.get("TRAIL_DISTANCE_ATR_MULT", 1.5)
    ml_filter = params.get("_ml_signal_filter")
    simulation_start = params.get("_simulation_start_open_time")
    simulation_end = params.get("_simulation_end_open_time")

    import numpy as np

    if ml_filter is not None and not hasattr(ml_filter, "predict_probability"):
        raise TypeError("_ml_signal_filter must expose predict_probability()")

    open_times = df["open_time"].values
    if simulation_start is not None:
        simulation_start = int(simulation_start)
        start_idx = int(np.searchsorted(open_times, simulation_start, side="left"))
        i = max(1, start_idx)

    max_allowed_idx = n - 1
    if simulation_end is not None:
        simulation_end = int(simulation_end)
        end_idx = int(np.searchsorted(open_times, simulation_end, side="right")) - 1
        max_allowed_idx = min(n - 1, max(-1, end_idx))

    while i < n - 1:
        row = df.iloc[i]
        row_time = int(row["open_time"])
        if simulation_end is not None and row_time > simulation_end:
            break
        prev_row = df.iloc[i - 1]
        direction = "LONG" if long_condition(row, prev_row, params) else ("SHORT" if short_condition(row, prev_row, params) else None)
        if direction is None:
            i += 1
            continue

        ml_probability = None
        if ml_filter is not None:
            signal_frame = pd.DataFrame([row.to_dict()], index=[row.name])
            probabilities = ml_filter.predict_probability(signal_frame)
            if len(probabilities) != 1:
                raise ValueError("ML signal filter must return exactly one probability per signal")
            ml_probability = float(probabilities[0])
            threshold = float(ml_filter.threshold)
            if not 0.0 < threshold < 1.0:
                raise ValueError("ML signal filter threshold must be between 0 and 1")
            if ml_probability < threshold:
                i += 1
                continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_bar = df.iloc[entry_idx]
        entry_time = int(entry_bar["open_time"])
        if simulation_end is not None and entry_time > simulation_end:
            break
        raw_entry_price = float(entry_bar["open"])
        atr_at_signal = row["atr"]
        if pd.isna(atr_at_signal) or atr_at_signal <= 0:
            if use_trailing:
                import logging
                logging.getLogger("backtest.simulate").warning(
                    "Signal at bar index %d (open_time=%s) has NaN or non-positive ATR (%s); skipping to avoid invalid stop/trailing calculation",
                    i, row_time, atr_at_signal
                )
            i += 1
            continue

        stop_loss, take_profit = stop_target(direction, raw_entry_price, atr_at_signal, row, params)
        initial_risk_price = abs(raw_entry_price - stop_loss)
        exit_price = exit_reason = exit_idx = None
        current_stop = stop_loss
        trailing_active = False
        favorable_extreme = raw_entry_price

        j = entry_idx
        last_allowed_idx = max_allowed_idx
        if last_allowed_idx < entry_idx:
            break

        while j <= last_allowed_idx:
            bar = df.iloc[j]
            if use_trailing and initial_risk_price > 0:
                if direction == "LONG" and bar["low"] <= current_stop:
                    exit_price, exit_reason = current_stop, "TRAIL" if trailing_active else "SL"
                elif direction == "SHORT" and bar["high"] >= current_stop:
                    exit_price, exit_reason = current_stop, "TRAIL" if trailing_active else "SL"
                if exit_price is None:
                    if direction == "LONG":
                        favorable_extreme = max(favorable_extreme, bar["high"])
                        unrealized_r = (favorable_extreme - raw_entry_price) / initial_risk_price
                        if not trailing_active and unrealized_r >= trail_activation_r:
                            trailing_active = True
                        if trailing_active:
                            current_stop = max(current_stop, favorable_extreme - trail_distance_atr_mult * atr_at_signal)
                    else:
                        favorable_extreme = min(favorable_extreme, bar["low"])
                        unrealized_r = (raw_entry_price - favorable_extreme) / initial_risk_price
                        if not trailing_active and unrealized_r >= trail_activation_r:
                            trailing_active = True
                        if trailing_active:
                            current_stop = min(current_stop, favorable_extreme + trail_distance_atr_mult * atr_at_signal)
            else:
                if direction == "LONG":
                    if bar["low"] <= stop_loss:
                        exit_price, exit_reason = stop_loss, "SL"
                    elif bar["high"] >= take_profit:
                        exit_price, exit_reason = take_profit, "TP"
                else:
                    if bar["high"] >= stop_loss:
                        exit_price, exit_reason = stop_loss, "SL"
                    elif bar["low"] <= take_profit:
                        exit_price, exit_reason = take_profit, "TP"

            if exit_price is not None:
                exit_idx = j
                break
            if j - entry_idx >= max_hold:
                exit_price, exit_reason, exit_idx = float(bar["close"]), "TIMEOUT", j
                break
            j += 1

        if exit_price is None:
            exit_idx = last_allowed_idx
            exit_price, exit_reason = float(df.iloc[exit_idx]["close"]), "END_OF_DATA"

        accounting = calculate_trade_accounting(
            direction=direction,
            quantity=1.0,
            entry_price=raw_entry_price,
            exit_price=float(exit_price),
            fee_pct=fee,
            entry_slippage_pct=slip,
            exit_slippage_pct=slip,
            stop_price=float(stop_loss),
        )
        trade = {
            "direction": direction,
            "signal_open_time": int(row["open_time"]),
            "entry_time": int(entry_bar["open_time"]),
            "entry_price": float(accounting["entry_exec_price"]),
            "exit_time": int(df.iloc[exit_idx]["open_time"]),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "gross_pnl": float(accounting["gross_pnl"]),
            "fees": float(accounting["fees"]),
            "funding_cost": 0.0,
            "net_pnl": float(accounting["net_pnl"]),
            "net_return_pct": float(accounting["return_pct_on_entry_notional"]),
            "r_multiple": float(accounting["r_multiple"]) if accounting["r_multiple"] is not None else None,
            "holding_bars": exit_idx - entry_idx,
        }
        if ml_probability is not None:
            trade["ml_probability"] = ml_probability
        trades.append(trade)
        i = exit_idx + 1

    return trades
