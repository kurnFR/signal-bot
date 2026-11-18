"""Generic trade simulation engine shared by all strategies."""
import logging
import math
import numpy as np
import pandas as pd

from backtest.accounting import (
    calculate_trade_accounting,
    resolve_stop_target_exit,
    calculate_funding_cost,
)

logger = logging.getLogger("backtest.simulate")


def simulate(df: pd.DataFrame, params: dict, long_condition, short_condition, stop_target) -> list:
    """Simulate closed-candle signals with next-bar-open execution.

    ``_simulation_start_open_time`` and ``_simulation_end_open_time`` are
    runtime-only chronological bounds. Historical rows remain available for
    indicator warm-up, but no signal/entry/exit is allowed outside the window.
    """
    trades = []
    if df is None or df.empty:
        return trades
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

    market = str(params.get("market", "spot")).lower()
    symbol = params.get("symbol")
    strategy_name = params.get("strategy_name", "")
    is_pairs = (
        strategy_name == "pairs_ratio_v1"
        or params.get("is_pairs_ratio", False)
        or (df is not None and "ratio" in df.columns)
    )

    funding_events_all: list = []
    funding_data_missing: bool = False

    if market == "futures":
        if is_pairs:
            # Pairs ratio trades a synthetic ratio without an executable two-leg sizing model.
            # Documented as unsupported for funding accrual per WO 71937 architectural boundary.
            pass
        elif "funding_events" in params and params["funding_events"] is not None:
            raw_events = params["funding_events"]
            validated = []
            for ev in raw_events:
                evt_time = int(ev[0])
                mp = float(ev[1])
                fr = float(ev[2])
                if not math.isfinite(mp) or not math.isfinite(fr):
                    raise ValueError(f"Non-finite funding data: mark_price={mp}, funding_rate={fr}")
                if mp <= 0:
                    raise ValueError(f"Invalid non-positive mark price: mark_price={mp}")
                validated.append((evt_time, mp, fr))
            validated.sort(key=lambda x: x[0])
            funding_events_all = validated
            if not funding_events_all:
                funding_data_missing = True
                logger.warning(
                    "Historical funding data unavailable for symbol %s in simulation window; "
                    "falling back to documented zero-cost funding.",
                    symbol,
                )
        elif symbol:
            win_start = int(df["open_time"].min())
            win_end = int(df["open_time"].max()) + int(max_hold * 86400 * 1000)
            try:
                from db.db import fetch_settled_funding_events
                funding_events_all = fetch_settled_funding_events(
                    symbol=symbol,
                    start_time=win_start,
                    end_time=win_end,
                )
                if not funding_events_all:
                    funding_data_missing = True
                    logger.warning(
                        "Historical funding data unavailable for symbol %s in window [%d, %d]; "
                        "falling back to documented zero-cost funding.",
                        symbol, win_start, win_end,
                    )
            except Exception as e:
                funding_data_missing = True
                logger.warning(
                    "Failed to retrieve historical funding data for symbol %s in window [%d, %d]: %s; "
                    "falling back to documented zero-cost funding.",
                    symbol, win_start, win_end, e,
                )
        else:
            funding_data_missing = True
            logger.warning(
                "No symbol provided for futures backtest; historical funding data unavailable, "
                "falling back to documented zero-cost funding."
            )

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
                if direction == "LONG":
                    if float(bar["open"]) <= current_stop:
                        exit_price = float(bar["open"])
                    elif bar["low"] <= current_stop:
                        exit_price = current_stop
                    if exit_price is not None:
                        exit_reason = "TRAIL" if trailing_active else "SL"
                elif direction == "SHORT":
                    if float(bar["open"]) >= current_stop:
                        exit_price = float(bar["open"])
                    elif bar["high"] >= current_stop:
                        exit_price = current_stop
                    if exit_price is not None:
                        exit_reason = "TRAIL" if trailing_active else "SL"
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
                resolved_exit = resolve_stop_target_exit(
                    direction,
                    float(bar["open"]),
                    float(bar["high"]),
                    float(bar["low"]),
                    float(stop_loss),
                    float(take_profit),
                )
                if resolved_exit is not None:
                    exit_price, exit_reason = resolved_exit

            if exit_price is not None:
                exit_idx = j
                break
            if j - entry_idx + 1 >= max_hold:
                exit_price, exit_reason, exit_idx = float(bar["close"]), "TIMEOUT", j
                break
            j += 1

        if exit_price is None:
            exit_idx = last_allowed_idx
            exit_price, exit_reason = float(df.iloc[exit_idx]["close"]), "END_OF_DATA"

        qty = float(params.get("quantity", 1.0))
        entry_t = int(entry_bar["open_time"])
        exit_bar = df.iloc[exit_idx]
        # TIMEOUT and END_OF_DATA exits are valued at the candle close, so
        # include settled funding events through close_time when available.
        # Intrabar SL/TP timestamps are unknowable from OHLCV alone; retain
        # the candle open as a conservative boundary rather than fabricating
        # an intrabar execution timestamp.
        if exit_reason in ("TIMEOUT", "END_OF_DATA") and "close_time" in df.columns:
            exit_t = int(exit_bar["close_time"])
        else:
            exit_t = int(exit_bar["open_time"])

        if market == "futures" and not is_pairs:
            if funding_data_missing:
                funding_cost = 0.0
                trade_funding_status = "missing_data_zero_cost_fallback"
            else:
                trade_events = [
                    (ev[1], ev[2])
                    for ev in funding_events_all
                    if entry_t < ev[0] <= exit_t
                ]
                if trade_events:
                    long_cost = calculate_funding_cost(qty, trade_events)
                    funding_cost = long_cost if direction == "LONG" else -long_cost
                    trade_funding_status = "settled"
                else:
                    funding_cost = 0.0
                    trade_funding_status = "no_events_in_interval"
        elif is_pairs:
            funding_cost = 0.0
            trade_funding_status = "unsupported_pairs_ratio_two_leg"
        else:
            funding_cost = 0.0
            trade_funding_status = "not_applicable"

        accounting = calculate_trade_accounting(
            direction=direction,
            quantity=qty,
            entry_price=raw_entry_price,
            exit_price=float(exit_price),
            fee_pct=fee,
            entry_slippage_pct=slip,
            exit_slippage_pct=slip,
            stop_price=float(stop_loss),
            funding_cost=funding_cost,
        )
        trade = {
            "direction": direction,
            "signal_open_time": int(row["open_time"]),
            "entry_time": entry_t,
            "entry_price": float(accounting["entry_exec_price"]),
            "exit_time": exit_t,
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "gross_pnl": float(accounting["gross_pnl"]),
            "fees": float(accounting["fees"]),
            "funding_cost": float(accounting["funding_cost"]),
            "funding_status": trade_funding_status,
            "net_pnl": float(accounting["net_pnl"]),
            "net_return_pct": float(accounting["return_pct_on_entry_notional"]),
            "r_multiple": float(accounting["r_multiple"]) if accounting["r_multiple"] is not None else None,
            "holding_bars": exit_idx - entry_idx + 1,
        }
        if ml_probability is not None:
            trade["ml_probability"] = ml_probability
        trades.append(trade)
        i = exit_idx + 1

    return trades
