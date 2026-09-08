"""
Phase B: Live Paper Trading / Shadow Execution Engine.

P0 accounting contract:
- Position sizing uses allocated capital and risk_per_trade_pct.
- Entry/exit fees, slippage, funding and R are calculated through
  backtest.accounting so paper and backtest share one financial formula.
- Database uniqueness is treated as an idempotent duplicate-sync outcome.

Execution lifecycle remains the next P0.5 integration step: this module still
uses the existing strategy runner for signal discovery. It must not be treated
as live execution.
"""
import time
import logging
from typing import List, Dict, Any

from db.db import get_pool, fetch_ohlcv_with_features_df
from backtest.strategies import (
    STRATEGIES,
    FUNDING_STRATEGIES,
    TREND_ALIGNMENT_STRATEGIES,
    PAIRS_STRATEGIES,
)
from backtest.strategies.trend_alignment import HTF_TIMEFRAME
from backtest.params import BASE_PARAMS
from backtest.accounting import calculate_position_size, calculate_trade_accounting
from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT, PAIRS_BASE_SYMBOL

logger = logging.getLogger("paper.engine")


def seed_validated_paper_configs():
    """Initialize validated paper configs when the table is empty.

    This is an explicit maintenance operation; it is intentionally NOT called
    at module import because importing the engine must not mutate the database.
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS cnt FROM paper_configs")
        if cur.fetchone()["cnt"] == 0:
            defaults = [
                ("ETHUSDT", "spot", "1d", "confluence_ensemble_v1", 1, 5000.0, 1.0),
                ("BNBUSDT", "futures", "1d", "pairs_ratio_v1", 1, 5000.0, 1.0),
            ]
            for row in defaults:
                cur.execute(
                    """
                    INSERT INTO paper_configs
                        (symbol, market, timeframe, strategy_name, is_active,
                         allocated_capital, risk_per_trade_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE is_active = VALUES(is_active)
                    """,
                    row,
                )
            conn.commit()
            logger.info("Initialized validated paper trading configs")
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
    symbol: str,
    market: str,
    timeframe: str,
    strategy_name: str,
    is_active: bool,
    allocated_capital: float = 5000.0,
    risk_per_trade_pct: float = 1.0,
) -> Dict[str, Any]:
    if allocated_capital <= 0:
        raise ValueError("allocated_capital must be positive")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            INSERT INTO paper_configs
                (symbol, market, timeframe, strategy_name, is_active,
                 allocated_capital, risk_per_trade_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                is_active = VALUES(is_active),
                allocated_capital = VALUES(allocated_capital),
                risk_per_trade_pct = VALUES(risk_per_trade_pct)
            """,
            (
                symbol,
                market,
                timeframe,
                strategy_name,
                1 if is_active else 0,
                allocated_capital,
                risk_per_trade_pct,
            ),
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
                trail_activation_r, trail_distance_atr_mult, initial_risk,
                allocated_capital, risk_per_trade_pct, risk_amount,
                stop_distance, quantity, notional_value, entry_fee,
                unrealized_pnl_pct, unrealized_r, holding_bars,
                max_hold_bars, status, last_update_time
            FROM paper_positions
            WHERE status = 'OPEN'
            ORDER BY id DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        numeric = (
            "entry_price", "current_price", "stop_loss", "take_profit",
            "current_stop", "initial_risk", "allocated_capital",
            "risk_per_trade_pct", "risk_amount", "stop_distance",
            "quantity", "notional_value", "entry_fee", "unrealized_pnl_pct",
            "unrealized_r", "trail_activation_r", "trail_distance_atr_mult",
        )
        for r in rows:
            for key in numeric:
                if r.get(key) is not None:
                    r[key] = float(r[key])
            r["trailing_active"] = bool(r["trailing_active"])
            r["entry_time_wib"] = str(r["entry_time_wib"]) if r["entry_time_wib"] else ""
        return rows
    finally:
        conn.close()


def get_paper_trades(limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                id, position_id, symbol, market, timeframe, strategy_name,
                direction, entry_time, entry_time_wib, entry_price,
                exit_time, exit_time_wib, exit_price, exit_reason,
                stop_loss, take_profit, quantity, notional_value,
                gross_pnl, entry_fee, exit_fee, funding_pnl, net_pnl,
                allocated_capital, risk_per_trade_pct, risk_amount,
                net_return_pct, r_multiple, holding_bars, created_at
            FROM paper_trades
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for key in (
                "entry_price", "exit_price", "stop_loss", "take_profit", "quantity",
                "notional_value", "gross_pnl", "entry_fee", "exit_fee", "funding_pnl",
                "net_pnl", "allocated_capital", "risk_per_trade_pct", "risk_amount",
                "net_return_pct", "r_multiple",
            ):
                if r.get(key) is not None:
                    r[key] = float(r[key])
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
            SELECT COUNT(*) AS total_trades,
                   SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN r_multiple <= 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(r_multiple) AS sum_r,
                   AVG(r_multiple) AS avg_r,
                   SUM(net_pnl) AS net_pnl
            FROM paper_trades
            """
        )
        summary = cur.fetchone()
        cur.close()
        total = int(summary["total_trades"] or 0)
        wins = int(summary["wins"] or 0)
        losses = int(summary["losses"] or 0)
        return {
            "totalTrades": total,
            "wins": wins,
            "losses": losses,
            "winRatePct": round(wins / total * 100, 2) if total else 0.0,
            "cumulativeR": round(float(summary["sum_r"] or 0.0), 4),
            "expectancyR": round(float(summary["avg_r"] or 0.0), 4),
            "netPnl": round(float(summary["net_pnl"] or 0.0), 8),
        }
    finally:
        conn.close()


def _funding_cost_for_position(pos: Dict[str, Any], exit_time: int) -> float:
    """Return signed funding cost for the position over its holding interval."""
    if pos["market"] != "futures":
        return 0.0
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT mark_price, funding_rate
            FROM funding_rate
            WHERE symbol = %s
              AND event_time > %s
              AND event_time <= %s
              AND mark_price IS NOT NULL
            ORDER BY event_time ASC
            """,
            (pos["symbol"], int(pos["entry_time"]), int(exit_time)),
        )
        events = [(float(r["mark_price"]), float(r["funding_rate"])) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()

    if not events:
        return 0.0
    # Canonical helper returns LONG funding cost. SHORT receives the inverse.
    from backtest.accounting import calculate_funding_cost
    long_cost = calculate_funding_cost(float(pos["quantity"]), events)
    return long_cost if pos["direction"] == "LONG" else -long_cost


def close_position_in_db(pos: Dict[str, Any], exit_price: float, exit_time: int, exit_reason: str):
    """Close one position using canonical accounting and persist the ledger."""
    if pos.get("status") != "OPEN":
        return False

    direction = pos["direction"]
    quantity = float(pos["quantity"])
    entry_price = float(pos["entry_price"])
    stop_loss = float(pos["stop_loss"])
    funding_cost = _funding_cost_for_position(pos, exit_time)
    accounting = calculate_trade_accounting(
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=float(exit_price),
        fee_pct=BACKTEST_FEE_PCT,
        entry_slippage_pct=0.0,
        exit_slippage_pct=BACKTEST_SLIPPAGE_PCT,
        stop_price=stop_loss,
        funding_cost=funding_cost,
    )

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        # Compare-and-set status makes repeated sync calls idempotent.
        cur.execute(
            """
            UPDATE paper_positions
            SET status = 'CLOSED', current_price = %s, last_update_time = %s,
                funding_pnl = %s
            WHERE id = %s AND status = 'OPEN'
            """,
            (float(exit_price), int(exit_time), -funding_cost, pos["id"]),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False

        cur.execute(
            """
            INSERT INTO paper_trades (
                position_id, symbol, market, timeframe, strategy_name, direction,
                entry_time, entry_price, exit_time, exit_price, exit_reason,
                stop_loss, take_profit, allocated_capital, risk_per_trade_pct,
                risk_amount, stop_distance, quantity, notional_value,
                gross_pnl, entry_fee, exit_fee, funding_pnl, net_pnl,
                net_return_pct, r_multiple, holding_bars
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                pos["id"], pos["symbol"], pos["market"], pos["timeframe"], pos["strategy_name"],
                direction, pos["entry_time"], entry_price, int(exit_time), float(exit_price), exit_reason,
                stop_loss, pos["take_profit"], pos["allocated_capital"], pos["risk_per_trade_pct"],
                accounting["risk_amount"], pos["stop_distance"], quantity, pos["notional_value"],
                accounting["gross_pnl"], pos["entry_fee"], accounting["fees"] - float(pos["entry_fee"]),
                -funding_cost, accounting["net_pnl"], accounting["return_pct_on_entry_notional"],
                accounting["r_multiple"], pos["holding_bars"],
            ),
        )
        conn.commit()
        cur.close()
        logger.info(
            "Closed paper position #%s (%s %s) via %s: %+.2fR net_pnl=%+.4f funding=%+.4f",
            pos["id"], pos["symbol"], direction, exit_reason,
            accounting["r_multiple"] or 0.0, accounting["net_pnl"], -funding_cost,
        )
    finally:
        conn.close()

    try:
        from notifications.telegram_notifier import send_trade_close_alert
        send_trade_close_alert(
            symbol=pos["symbol"], strategy_name=pos["strategy_name"], direction=direction,
            entry_price=entry_price, exit_price=float(exit_price), exit_reason=exit_reason,
            net_return_pct=accounting["return_pct_on_entry_notional"],
            r_multiple=accounting["r_multiple"] or 0.0, holding_bars=pos.get("holding_bars", 0),
        )
    except Exception as tel_err:
        logger.warning("Telegram trade close alert failed: %s", tel_err)
    return True


def manual_close_position(position_id: int) -> Dict[str, Any]:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id = %s AND status = 'OPEN'", (position_id,))
        pos = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not pos:
        raise ValueError(f"Open position #{position_id} not found")
    now_ms = int(time.time() * 1000)
    return {
        "status": "success" if close_position_in_db(pos, float(pos["current_price"]), now_ms, "MANUAL") else "already_closed",
        "message": f"Position #{position_id} manually closed",
    }


def _insert_position(cfg: Dict[str, Any], trade: Dict[str, Any], current_close: float, candle_open_time: int) -> bool:
    direction = trade["direction"]
    raw_entry = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    take_profit = float(trade["take_profit"])
    size = calculate_position_size(
        equity=float(cfg["allocated_capital"]),
        risk_pct=float(cfg["risk_per_trade_pct"]),
        entry_price=raw_entry,
        stop_price=stop_loss,
    )
    entry_fee = raw_entry * size.quantity * BACKTEST_FEE_PCT
    atr = float(trade.get("atr_at_signal", 0.0) or 0.0)

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
                holding_bars, max_hold_bars, last_update_time, status,
                allocated_capital, risk_per_trade_pct, risk_amount, stop_distance,
                quantity, notional_value, entry_fee, exit_fee, funding_pnl
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,1.0,1.5,%s,%s,0.0,0.0,
                0,200,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,0.0,0.0
            )
            """,
            (
                cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"], direction,
                int(candle_open_time), raw_entry, current_close, stop_loss, take_profit, stop_loss,
                atr, size.stop_distance, size.risk_amount, cfg["allocated_capital"],
                cfg["risk_per_trade_pct"], size.risk_amount, size.stop_distance,
                size.quantity, size.notional, entry_fee,
            ),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as exc:
        conn.rollback()
        # The P0 migration adds a unique active-position key. A duplicate is
        # expected when concurrent schedulers observe the same signal.
        if "uq_paper_open_position" in str(exc) or "Duplicate entry" in str(exc):
            logger.info("Ignored duplicate paper position for %s/%s", cfg["symbol"], cfg["strategy_name"])
            return False
        raise
    finally:
        conn.close()


def sync_and_evaluate_paper_trading() -> Dict[str, Any]:
    """Evaluate active configs and open positions with canonical accounting."""
    configs = get_paper_configs()
    active_cfgs = [c for c in configs if c["is_active"]]
    positions = get_active_positions()
    open_pos_map = {(p["symbol"], p["market"], p["timeframe"], p["strategy_name"]): p for p in positions}
    result = {"evaluated_configs": len(active_cfgs), "positions_updated": 0, "new_positions_opened": 0, "positions_closed": 0}

    for cfg in active_cfgs:
        symbol, market, timeframe, strategy_name = cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"]
        key = (symbol, market, timeframe, strategy_name)
        if strategy_name not in STRATEGIES:
            continue
        include_funding = strategy_name in FUNDING_STRATEGIES
        df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=include_funding)
        if len(df) < 50:
            continue

        latest_bar = df.iloc[-1]
        current_close = float(latest_bar["close"])
        candle_open_time = int(latest_bar["open_time"])

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
            atr = float(latest_bar.get("atr", 0) or 0)
            holding_bars = int(pos["holding_bars"]) + 1

            if direction == "LONG":
                unrealized_pct = (current_close - entry_price) / entry_price
                unrealized_r = (current_close - entry_price) / initial_risk if initial_risk > 0 else 0.0
            else:
                unrealized_pct = (entry_price - current_close) / entry_price
                unrealized_r = (entry_price - current_close) / initial_risk if initial_risk > 0 else 0.0

            exit_reason = None
            exit_price = None
            if direction == "LONG":
                if float(latest_bar["low"]) <= current_stop:
                    exit_reason, exit_price = ("TRAIL" if trailing_active else "SL"), current_stop
                elif not trailing_active and float(latest_bar["high"]) >= take_profit:
                    exit_reason, exit_price = "TP", take_profit
                elif holding_bars >= int(pos["max_hold_bars"]):
                    exit_reason, exit_price = "TIMEOUT", current_close
                elif unrealized_r >= trail_act_r and atr > 0:
                    trailing_active = True
                    current_stop = max(current_stop, current_close - trail_dist_mult * atr)
            else:
                if float(latest_bar["high"]) >= current_stop:
                    exit_reason, exit_price = ("TRAIL" if trailing_active else "SL"), current_stop
                elif not trailing_active and float(latest_bar["low"]) <= take_profit:
                    exit_reason, exit_price = "TP", take_profit
                elif holding_bars >= int(pos["max_hold_bars"]):
                    exit_reason, exit_price = "TIMEOUT", current_close
                elif unrealized_r >= trail_act_r and atr > 0:
                    trailing_active = True
                    current_stop = min(current_stop, current_close + trail_dist_mult * atr)

            if exit_reason:
                if close_position_in_db(pos, float(exit_price), candle_open_time, exit_reason):
                    result["positions_closed"] += 1
            else:
                conn = get_pool().get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE paper_positions
                        SET current_price=%s, current_stop=%s, trailing_active=%s,
                            unrealized_pnl_pct=%s, unrealized_r=%s, holding_bars=%s,
                            last_update_time=%s
                        WHERE id=%s AND status='OPEN'
                        """,
                        (current_close, current_stop, 1 if trailing_active else 0,
                         unrealized_pct, unrealized_r, holding_bars, candle_open_time, pos["id"]),
                    )
                    conn.commit()
                    cur.close()
                    result["positions_updated"] += 1
                finally:
                    conn.close()
        else:
            run_params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)
            if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
                run_params["htf_df"] = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True)
            if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
                run_params["pair_df"] = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True)
                run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

            sim_trades = STRATEGIES[strategy_name](df, run_params)
            if sim_trades:
                latest_trade = sim_trades[-1]
                # Current behavior is intentionally limited to trades whose
                # entry is represented by the latest closed candle. A later P0.5
                # change will introduce SIGNAL -> PENDING_ENTRY -> ACTIVE so a
                # signal is never retroactively filled after its bar opened.
                if latest_trade.get("entry_time") == candle_open_time:
                    if _insert_position(cfg, latest_trade, current_close, candle_open_time):
                        result["new_positions_opened"] += 1
                        logger.info("Opened paper position for %s (%s) via %s", symbol, latest_trade["direction"], strategy_name)
                        try:
                            from notifications.telegram_notifier import send_signal_alert
                            send_signal_alert(
                                symbol=symbol, market=market, timeframe=timeframe,
                                strategy_name=strategy_name, direction=latest_trade["direction"],
                                entry_price=float(latest_trade["entry_price"]),
                                stop_loss=float(latest_trade["stop_loss"]),
                                take_profit=float(latest_trade["take_profit"]),
                                initial_risk=abs(float(latest_trade["entry_price"]) - float(latest_trade["stop_loss"])),
                                atr=float(latest_bar.get("atr", 0) or 0),
                            )
                        except Exception as tel_err:
                            logger.warning("Telegram entry signal alert failed: %s", tel_err)

    return result
