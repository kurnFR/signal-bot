"""
Phase B: Live Paper Trading / Shadow Execution Engine.
- Strictly parameterized SQL to prevent SQL injection.
- Direct invocation of the same STRATEGIES registry used in backtesting.
- Open position tracking, trailing stop evaluation, SL/TP management.
- Live-forward trade logging into paper_trades.
"""
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from db.db import get_pool, fetch_ohlcv_with_features_df
from backtest.strategies import STRATEGIES, FUNDING_STRATEGIES, TREND_ALIGNMENT_STRATEGIES, PAIRS_STRATEGIES
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.params import BASE_PARAMS
from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT, PAIRS_BASE_SYMBOL

logger = logging.getLogger("paper.engine")


def seed_validated_paper_configs():
    """Initializes paper configs with the 2 holdout-validated strategies."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) as cnt FROM paper_configs")
        if cur.fetchone()["cnt"] == 0:
            defaults = [
                ("ETHUSDT", "spot", "1d", "confluence_ensemble_v1", 1, 5000.0, 1.0),
                ("BNBUSDT", "futures", "1d", "pairs_ratio_v1", 1, 5000.0, 1.0),
            ]
            for row in defaults:
                cur.execute(
                    """
                    INSERT INTO paper_configs 
                        (symbol, market, timeframe, strategy_name, is_active, allocated_capital, risk_per_trade_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE is_active = VALUES(is_active)
                    """,
                    row
                )
            conn.commit()
            logger.info("Initialized validated paper trading configs (ETH 1d ensemble, BNB 1d pairs)")
        cur.close()
    finally:
        conn.close()


def get_paper_configs() -> List[Dict[str, Any]]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, symbol, market, timeframe, strategy_name, is_active, 
                   allocated_capital, risk_per_trade_pct, created_at
            FROM paper_configs
            ORDER BY is_active DESC, symbol ASC
            """
        )
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["is_active"] = bool(r["is_active"])
            r["allocated_capital"] = float(r["allocated_capital"])
            r["risk_per_trade_pct"] = float(r["risk_per_trade_pct"])
            r["created_at"] = str(r["created_at"])
        return rows
    finally:
        conn.close()


def set_paper_config(
    symbol: str, market: str, timeframe: str, strategy_name: str,
    is_active: bool, allocated_capital: float = 5000.0, risk_per_trade_pct: float = 1.0
) -> Dict[str, Any]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            INSERT INTO paper_configs 
                (symbol, market, timeframe, strategy_name, is_active, allocated_capital, risk_per_trade_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                is_active = VALUES(is_active),
                allocated_capital = VALUES(allocated_capital),
                risk_per_trade_pct = VALUES(risk_per_trade_pct)
            """,
            (symbol, market, timeframe, strategy_name, 1 if is_active else 0, allocated_capital, risk_per_trade_pct)
        )
        conn.commit()
        cur.close()
        return {"status": "success", "symbol": symbol, "strategy": strategy_name, "is_active": is_active}
    finally:
        conn.close()


def get_active_positions() -> List[Dict[str, Any]]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT 
                id, symbol, market, timeframe, strategy_name, direction,
                entry_time, entry_time_wib, entry_price, current_price,
                stop_loss, take_profit, current_stop, trailing_active,
                initial_risk, unrealized_pnl_pct, unrealized_r,
                holding_bars, status, last_update_time
            FROM paper_positions
            WHERE status = 'OPEN'
            ORDER BY id DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["entry_price"] = float(r["entry_price"])
            r["current_price"] = float(r["current_price"])
            r["stop_loss"] = float(r["stop_loss"])
            r["take_profit"] = float(r["take_profit"])
            r["current_stop"] = float(r["current_stop"])
            r["trailing_active"] = bool(r["trailing_active"])
            r["initial_risk"] = float(r["initial_risk"])
            r["unrealized_pnl_pct"] = float(r["unrealized_pnl_pct"])
            r["unrealized_r"] = float(r["unrealized_r"])
            r["entry_time_wib"] = str(r["entry_time_wib"]) if r["entry_time_wib"] else ""
        return rows
    finally:
        conn.close()


def get_paper_trades(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT 
                id, position_id, symbol, market, timeframe, strategy_name,
                direction, entry_time, entry_time_wib, entry_price,
                exit_time, exit_time_wib, exit_price, exit_reason,
                stop_loss, take_profit, net_return_pct, r_multiple, holding_bars, created_at
            FROM paper_trades
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["entry_price"] = float(r["entry_price"])
            r["exit_price"] = float(r["exit_price"])
            r["stop_loss"] = float(r["stop_loss"])
            r["take_profit"] = float(r["take_profit"])
            r["net_return_pct"] = round(float(r["net_return_pct"]) * 100, 3)
            r["r_multiple"] = float(r["r_multiple"])
            r["entry_time_wib"] = str(r["entry_time_wib"]) if r["entry_time_wib"] else ""
            r["exit_time_wib"] = str(r["exit_time_wib"]) if r["exit_time_wib"] else ""
            r["created_at"] = str(r["created_at"])
        return rows
    finally:
        conn.close()


def get_paper_metrics() -> Dict[str, Any]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN r_multiple <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(r_multiple) as sum_r,
                AVG(r_multiple) as avg_r
            FROM paper_trades
            """
        )
        summary = cur.fetchone()
        cur.close()

        total = summary["total_trades"] or 0
        wins = summary["wins"] or 0
        losses = summary["losses"] or 0
        sum_r = float(summary["sum_r"] or 0.0)
        avg_r = float(summary["avg_r"] or 0.0)
        win_rate = round((wins / total * 100), 2) if total > 0 else 0.0

        return {
            "totalTrades": total,
            "wins": wins,
            "losses": losses,
            "winRatePct": win_rate,
            "cumulativeR": round(sum_r, 4),
            "expectancyR": round(avg_r, 4),
        }
    finally:
        conn.close()


def close_position_in_db(pos: Dict[str, Any], exit_price: float, exit_time: int, exit_reason: str):
    direction = pos["direction"]
    entry_price = float(pos["entry_price"])
    initial_risk = float(pos["initial_risk"])
    fee = BACKTEST_FEE_PCT
    slip = BACKTEST_SLIPPAGE_PCT

    if direction == "LONG":
        raw_ret = (exit_price - entry_price) / entry_price
        eff_exit = exit_price * (1 - slip)
        net_ret = (eff_exit - entry_price) / entry_price - (2 * fee)
    else:
        raw_ret = (entry_price - exit_price) / entry_price
        eff_exit = exit_price * (1 + slip)
        net_ret = (entry_price - eff_exit) / entry_price - (2 * fee)

    r_mult = (raw_ret * entry_price) / initial_risk if initial_risk > 0 else 0.0

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        # 1. Update position status to CLOSED
        cur.execute(
            """
            UPDATE paper_positions 
            SET status = 'CLOSED', current_price = %s, last_update_time = %s 
            WHERE id = %s
            """,
            (exit_price, exit_time, pos["id"])
        )

        # 2. Insert into paper_trades ledger
        cur.execute(
            """
            INSERT INTO paper_trades (
                position_id, symbol, market, timeframe, strategy_name, direction,
                entry_time, entry_price, exit_time, exit_price, exit_reason,
                stop_loss, take_profit, net_return_pct, r_multiple, holding_bars
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pos["id"], pos["symbol"], pos["market"], pos["timeframe"], pos["strategy_name"],
                direction, pos["entry_time"], entry_price, exit_time, exit_price, exit_reason,
                pos["stop_loss"], pos["take_profit"], net_ret, r_mult, pos["holding_bars"]
            )
        )
        conn.commit()
        cur.close()
        logger.info(f"Closed paper position #{pos['id']} ({pos['symbol']} {direction}) via {exit_reason}: {r_mult:+.2f}R")
    finally:
        conn.close()


def manual_close_position(position_id: int) -> Dict[str, Any]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id = %s AND status = 'OPEN'", (position_id,))
        pos = cur.fetchone()
        cur.close()
        if not pos:
            raise ValueError(f"Open position #{position_id} not found")

        now_ms = int(time.time() * 1000)
        exit_price = float(pos["current_price"])
        close_position_in_db(pos, exit_price, now_ms, "MANUAL")
        return {"status": "success", "message": f"Position #{position_id} manually closed"}
    finally:
        conn.close()


def sync_and_evaluate_paper_trading() -> Dict[str, Any]:
    """
    Evaluates latest market data against all active paper configurations and open positions.
    1. Updates open positions against current candles (checks SL, TP, Trailing Stop, Holding Bars).
    2. Runs strategy condition checks for newly closed candles to open new paper positions.
    """
    configs = get_paper_configs()
    active_cfgs = [c for c in configs if c["is_active"]]
    positions = get_active_positions()

    open_pos_map = {(p["symbol"], p["market"], p["timeframe"], p["strategy_name"]): p for p in positions}
    eval_results = {"evaluated_configs": len(active_cfgs), "positions_updated": 0, "new_positions_opened": 0, "positions_closed": 0}

    for cfg in active_cfgs:
        symbol = cfg["symbol"]
        market = cfg["market"]
        timeframe = cfg["timeframe"]
        strategy_name = cfg["strategy_name"]
        key = (symbol, market, timeframe, strategy_name)

        if strategy_name not in STRATEGIES:
            continue

        include_funding = strategy_name in FUNDING_STRATEGIES
        df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=include_funding)
        if len(df) < 50:
            continue

        latest_bar = df.iloc[-1]
        prev_bar = df.iloc[-2]
        current_close = float(latest_bar["close"])
        candle_open_time = int(latest_bar["open_time"])

        # Check existing open position
        if key in open_pos_map:
            pos = open_pos_map[key]
            direction = pos["direction"]
            entry_price = float(pos["entry_price"])
            initial_risk = float(pos["initial_risk"])
            current_stop = float(pos["current_stop"])
            take_profit = float(pos["take_profit"])
            trailing_active = bool(pos["trailing_active"])
            trail_act_r = float(pos["trail_activation_r"])
            trail_dist_mult = float(pos["trail_distance_atr_mult"])
            atr = float(latest_bar.get("atr", 0))

            holding_bars = pos["holding_bars"] + 1

            # Unrealized calculations
            if direction == "LONG":
                unrealized_pct = (current_close - entry_price) / entry_price
                unrealized_r = (current_close - entry_price) / initial_risk if initial_risk > 0 else 0.0
            else:
                unrealized_pct = (entry_price - current_close) / entry_price
                unrealized_r = (entry_price - current_close) / initial_risk if initial_risk > 0 else 0.0

            # Check exit conditions
            exit_reason = None
            exit_price = None

            if direction == "LONG":
                if float(latest_bar["low"]) <= current_stop:
                    exit_reason = "TRAIL" if trailing_active else "SL"
                    exit_price = current_stop
                elif not trailing_active and float(latest_bar["high"]) >= take_profit:
                    exit_reason = "TP"
                    exit_price = take_profit
                elif holding_bars >= pos["max_hold_bars"]:
                    exit_reason = "TIMEOUT"
                    exit_price = current_close
                else:
                    # Update trailing stop if eligible
                    if unrealized_r >= trail_act_r and atr > 0:
                        trailing_active = True
                        candidate_stop = current_close - (trail_dist_mult * atr)
                        current_stop = max(current_stop, candidate_stop)
            else:
                if float(latest_bar["high"]) >= current_stop:
                    exit_reason = "TRAIL" if trailing_active else "SL"
                    exit_price = current_stop
                elif not trailing_active and float(latest_bar["low"]) <= take_profit:
                    exit_reason = "TP"
                    exit_price = take_profit
                elif holding_bars >= pos["max_hold_bars"]:
                    exit_reason = "TIMEOUT"
                    exit_price = current_close
                else:
                    if unrealized_r >= trail_act_r and atr > 0:
                        trailing_active = True
                        candidate_stop = current_close + (trail_dist_mult * atr)
                        current_stop = min(current_stop, candidate_stop)

            if exit_reason:
                close_position_in_db(pos, exit_price, candle_open_time, exit_reason)
                eval_results["positions_closed"] += 1
            else:
                # Update position state in DB
                conn = get_pool().get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE paper_positions 
                        SET current_price = %s, current_stop = %s, trailing_active = %s,
                            unrealized_pnl_pct = %s, unrealized_r = %s, holding_bars = %s,
                            last_update_time = %s
                        WHERE id = %s
                        """,
                        (current_close, current_stop, 1 if trailing_active else 0,
                         unrealized_pct, unrealized_r, holding_bars, candle_open_time, pos["id"])
                    )
                    conn.commit()
                    cur.close()
                    eval_results["positions_updated"] += 1
                finally:
                    conn.close()

        else:
            # Evaluate entry signal for new position
            run_params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)

            if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
                htf_full = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
                run_params["htf_df"] = htf_full

            if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
                pair_full = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
                run_params["pair_df"] = pair_full
                run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

            # Run full simulation to see if latest closed candle triggered a new trade
            strat_fn = STRATEGIES[strategy_name]
            sim_trades = strat_fn(df, run_params)

            if sim_trades:
                latest_trade = sim_trades[-1]
                # Check if this trade occurred on the latest closed candle
                if latest_trade.get("signal_open_time") == candle_open_time or latest_trade.get("entry_time") == candle_open_time:
                    direction = latest_trade["direction"]
                    raw_entry = float(latest_trade["entry_price"])
                    stop_loss = float(latest_trade["stop_loss"])
                    take_profit = float(latest_trade["take_profit"])
                    initial_risk = abs(raw_entry - stop_loss)
                    atr = float(latest_bar.get("atr", 0))

                    conn = get_pool().get_connection()
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT INTO paper_positions (
                                symbol, market, timeframe, strategy_name, direction,
                                entry_time, entry_price, current_price, stop_loss, take_profit,
                                current_stop, trailing_active, trail_activation_r, trail_distance_atr_mult,
                                atr_at_signal, initial_risk, unrealized_pnl_pct, unrealized_r,
                                holding_bars, max_hold_bars, last_update_time, status
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 1.0, 1.5, %s, %s, 0.0, 0.0, 0, 200, %s, 'OPEN')
                            """,
                            (
                                symbol, market, timeframe, strategy_name, direction,
                                candle_open_time, raw_entry, current_close, stop_loss, take_profit,
                                stop_loss, atr, initial_risk, candle_open_time
                            )
                        )
                        conn.commit()
                        cur.close()
                        eval_results["new_positions_opened"] += 1
                        logger.info(f"Opened new paper position for {symbol} ({direction}) via {strategy_name}")
                    finally:
                        conn.close()

    return eval_results


# Initialize defaults on import
seed_validated_paper_configs()
