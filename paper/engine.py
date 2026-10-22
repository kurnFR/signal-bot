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
import pandas as pd
import os
from typing import List, Dict, Any, Optional

from db.db import get_pool, fetch_ohlcv_with_features_df, fetch_latest_open_candle
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
from paper.orders import create_pending_order, fill_pending_order, list_orders, transition_order

logger = logging.getLogger("paper.engine")

# Strategies whose entries are opened by their own dedicated module rather
# than the STRATEGIES-driven signal loop below -- deliberately excluded from
# STRATEGIES (and from backtest/strategies/__init__.py's registry) because
# they aren't backtestable against historical bars the normal way. A
# paper_configs row with one of these strategy_names still gets exit
# management (SL/TP/trailing/circuit-breakers) from the loop in
# sync_and_evaluate_paper_trading() -- it just never auto-opens a position
# from price action alone. See docs/NEWS_AI.md §2.

OVERLAY_STRATEGIES = {"news_ai_overlay"}

PAPER_ENTRY_MAX_DELAY_MS = max(1, int(os.getenv("PAPER_ENTRY_MAX_DELAY_SECONDS", "120"))) * 1000

_TIMEFRAME_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def _append_next_open_candle(closed_df, open_candle, timeframe):
    """Append the current candle only as an execution bar, never as a signal bar.

    Indicators on the last closed row remain unchanged. The forming candle's
    feature values are copied from the previous closed row because the
    simulator only uses its OHLCV/open_time for the next-open entry; signal
    conditions still run only on closed rows.
    """
    if not open_candle or timeframe not in _TIMEFRAME_MS or closed_df.empty:
        return None
    signal_time = int(closed_df.iloc[-1]["open_time"])
    entry_time = int(open_candle["open_time"])
    interval_ms = _TIMEFRAME_MS[timeframe]
    if entry_time != signal_time + interval_ms:
        return None
    # A collector can leave an old row marked is_closed=0 after it stops.
    # Never treat that stale row as a current next-open execution opportunity.
    now_ms = int(time.time() * 1000)
    max_delay_ms = min(PAPER_ENTRY_MAX_DELAY_MS, interval_ms)
    if (now_ms < entry_time
            or now_ms - entry_time > max_delay_ms
            or now_ms >= entry_time + interval_ms):
        return None

    import pandas as pd
    row = closed_df.iloc[-1].copy()
    row["open_time"] = entry_time
    for column in ("open", "high", "low", "close", "volume"):
        row[column] = float(open_candle[column])
    if "is_closed" in row.index:
        row["is_closed"] = 0
    return pd.concat([closed_df, pd.DataFrame([row])], ignore_index=True)


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
                   allocated_capital, risk_per_trade_pct, ml_model_id, created_at
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
            r["ml_model_id"] = r.get("ml_model_id")
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
    ml_model_id: Optional[str] = None,
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
                 allocated_capital, risk_per_trade_pct, ml_model_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                is_active = VALUES(is_active),
                allocated_capital = VALUES(allocated_capital),
                risk_per_trade_pct = VALUES(risk_per_trade_pct),
                ml_model_id = VALUES(ml_model_id)
            """,
            (
                symbol,
                market,
                timeframe,
                strategy_name,
                1 if is_active else 0,
                allocated_capital,
                risk_per_trade_pct,
                ml_model_id,
            ),
        )
        conn.commit()
        cur.close()
        return {
            "status": "success",
            "symbol": symbol,
            "strategy": strategy_name,
            "is_active": is_active,
            "ml_model_id": ml_model_id,
        }
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
                net_return_pct, r_multiple, holding_bars, ml_model_id, ml_probability
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                pos["id"], pos["symbol"], pos["market"], pos["timeframe"], pos["strategy_name"],
                direction, pos["entry_time"], entry_price, int(exit_time), float(exit_price), exit_reason,
                stop_loss, pos["take_profit"], pos["allocated_capital"], pos["risk_per_trade_pct"],
                accounting["risk_amount"], pos["stop_distance"], quantity, pos["notional_value"],
                accounting["gross_pnl"], pos["entry_fee"], accounting["fees"] - float(pos["entry_fee"]),
                -funding_cost, accounting["net_pnl"], accounting["return_pct_on_entry_notional"],
                accounting["r_multiple"], pos["holding_bars"], pos.get("ml_model_id"), pos.get("ml_probability"),
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


def _insert_position(
    cfg: Dict[str, Any],
    trade: Dict[str, Any],
    current_close: float,
    candle_open_time: int,
    ml_model_id: Optional[str] = None,
    ml_probability: Optional[float] = None,
    connection=None,
) -> Optional[int]:
    """Returns the new paper_positions row id on success, or None if the
    insert was skipped as a duplicate (the unique open-position key already
    covers this symbol/market/timeframe/strategy). Callers that only care
    whether a position was opened (not the id) can still use this in a
    boolean context -- 0 is never a valid auto-increment id."""
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
    # Portfolio-level cap: applies to every entry path (strategies and news overlay).
    from paper.circuit_breaker import check_exposure_limit
    exposure_ok, exposure_msg = check_exposure_limit(size.notional)
    if not exposure_ok:
        logger.warning("Paper entry for %s/%s blocked: %s", cfg["symbol"], cfg["strategy_name"], exposure_msg)
        return None
    entry_fee = raw_entry * size.quantity * BACKTEST_FEE_PCT
    atr = float(trade.get("atr_at_signal", 0.0) or 0.0)

    # last_update_time = open_time of the last candle whose data has been
    # applied to this position (holding_bars, SL/TP, trailing). At insert
    # NO candle has been applied yet -- the entry bar's own high/low still
    # has to be checked, matching backtest/simulate.py which starts its
    # exit scan AT the entry bar (j = entry_idx). So store "just before the
    # entry candle", not the entry candle itself. (This slot previously
    # received size.risk_amount by a positional misalignment in the values
    # tuple -- a dollar amount in a timestamp column. Harmless while nothing
    # read the column; the per-candle guard below now depends on it.)
    last_applied_candle = int(candle_open_time) - 1

    owns_connection = connection is None
    conn = connection or get_pool().get_connection()
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
                quantity, notional_value, entry_fee, exit_fee, funding_pnl,
                ml_model_id, ml_probability
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,1.0,1.5,%s,%s,0.0,0.0,
                0,200,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,0.0,0.0,%s,%s
            )
            """,
            (
                cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"], direction,
                int(candle_open_time), raw_entry, current_close, stop_loss, take_profit, stop_loss,
                atr, size.stop_distance, last_applied_candle, cfg["allocated_capital"],
                cfg["risk_per_trade_pct"], size.risk_amount, size.stop_distance,
                size.quantity, size.notional, entry_fee, ml_model_id, ml_probability,
            ),
        )
        if owns_connection:
            conn.commit()
        new_id = cur.lastrowid
        cur.close()
        return new_id
    except Exception as exc:
        if owns_connection:
            conn.rollback()
        # The P0 migration adds a unique active-position key. A duplicate is
        # expected when concurrent schedulers observe the same signal.
        if "uq_paper_open_position" in str(exc) or "Duplicate entry" in str(exc):
            logger.info("Ignored duplicate paper position for %s/%s", cfg["symbol"], cfg["strategy_name"])
            return None
        raise
    finally:
        if owns_connection:
            conn.close()


# Public alias -- ai/news_execution.py (and any other overlay-strategy
# module) inserts positions through this rather than the underscore-prefixed
# name, since it's a legitimate cross-module entry point, not an internal
# detail of this file.
insert_paper_position = _insert_position


# Candles where the ML gate already rejected this config's signal. The
# rejection is deterministic for a given closed candle (same features ->
# same probability), so under a continuous polling loop there's no point
# reloading the joblib model and re-scoring it every cycle -- and it
# would log a fresh "REJECTED" line every minute for the whole candle.
# In-process only (resets on restart, which just means one re-score).
_ML_REJECTED_CANDLE: Dict[tuple, int] = {}


def _signal_already_traded(cfg: Dict[str, Any], entry_candle: int) -> bool:
    """True if a trade for this exact config already entered on this candle.

    A signal is only actionable while its entry bar is the latest closed
    candle -- for a whole hour on 1h, a whole day on 1d -- and this function
    now runs every ~60s. Without this check, a position that opens and is
    stopped out on the same candle (the entry bar's own range is evaluated,
    as in the backtest) would be re-opened by the very next cycle, because
    the strategy still reports that same signal on the latest bar: open ->
    stop -> re-open, repeatedly, for the whole candle. Open positions are
    covered by the unique open-position index; this covers CLOSED ones.
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM paper_trades
            WHERE symbol=%s AND market=%s AND timeframe=%s AND strategy_name=%s
              AND entry_time=%s
            LIMIT 1
            """,
            (cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"], int(entry_candle)),
        )
        found = cur.fetchone() is not None
        cur.close()
        return found
    finally:
        conn.close()




def _send_paper_entry_alert(order: Dict[str, Any], ml_threshold: Optional[float] = None) -> None:
    """Send the same entry alert for immediate fills and restart-recovered fills."""
    try:
        from notifications.telegram_notifier import send_signal_alert
        entry_price = float(order["requested_entry_price"])
        stop_loss = float(order["stop_loss"])
        send_signal_alert(
            symbol=order["symbol"], market=order["market"], timeframe=order["timeframe"],
            strategy_name=order["strategy_name"], direction=order["direction"],
            entry_price=entry_price, stop_loss=stop_loss,
            take_profit=float(order["take_profit"]),
            initial_risk=abs(entry_price - stop_loss),
            atr=float(order.get("atr_at_signal", 0) or 0),
            ml_probability=order.get("ml_probability"),
            ml_threshold=ml_threshold,
            ml_model_id=order.get("ml_model_id"),
        )
    except Exception as tel_err:
        logger.warning("Telegram entry alert failed for paper order #%s: %s", order.get("id"), tel_err)


def _process_pending_paper_orders(configs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Recover pending orders and fill only during their intended next-open window."""
    config_by_id = {int(cfg["id"]): cfg for cfg in configs if cfg.get("id") is not None}
    result = {"filled": 0, "expired": 0, "rejected": 0}
    if not config_by_id:
        return result
    try:
        pending = list_orders(status="PENDING", limit=200)
    except Exception:
        logger.exception("Unable to load pending paper orders; skipping entry recovery")
        return result

    now_ms = int(time.time() * 1000)
    for order in pending:
        cfg = config_by_id.get(int(order["config_id"]))
        if not cfg or not cfg.get("is_active"):
            try:
                transition_order(int(order["id"]), "CANCELLED", reason="Paper config inactive or missing")
            except Exception:
                logger.exception("Unable to cancel pending paper order #%s", order.get("id"))
            continue
        if (cfg["symbol"] != order["symbol"] or cfg["market"] != order["market"]
                or cfg["timeframe"] != order["timeframe"]
                or cfg["strategy_name"] != order["strategy_name"]
                or (cfg.get("ml_model_id") or None) != (order.get("ml_model_id") or None)):
            try:
                transition_order(int(order["id"]), "CANCELLED", reason="Paper config changed after signal")
            except Exception:
                logger.exception("Unable to cancel changed-config order #%s", order.get("id"))
            continue

        entry_time = int(order["entry_time"])
        interval_ms = _TIMEFRAME_MS.get(order["timeframe"])
        if not interval_ms:
            transition_order(int(order["id"]), "REJECTED", reason="Unsupported timeframe")
            result["rejected"] += 1
            continue
        max_delay_ms = min(PAPER_ENTRY_MAX_DELAY_MS, interval_ms)
        try:
            open_candle = fetch_latest_open_candle(order["symbol"], order["market"], order["timeframe"])
        except Exception:
            logger.exception("Unable to fetch entry candle for pending order #%s", order.get("id"))
            open_candle = None

        candle_time = int(open_candle["open_time"]) if open_candle and open_candle.get("open_time") is not None else None
        if candle_time is not None and candle_time > entry_time:
            try:
                transition_order(int(order["id"]), "EXPIRED", reason="Intended entry candle was missed")
                result["expired"] += 1
            except Exception:
                logger.exception("Unable to expire pending paper order #%s", order.get("id"))
            continue
        if now_ms < entry_time:
            continue
        if now_ms - entry_time > max_delay_ms or now_ms >= entry_time + interval_ms:
            try:
                transition_order(int(order["id"]), "EXPIRED", reason="Entry window elapsed")
                result["expired"] += 1
            except Exception:
                logger.exception("Unable to expire pending paper order #%s", order.get("id"))
            continue
        if candle_time != entry_time or not open_candle:
            continue

        trade = {
            "direction": order["direction"],
            "entry_price": float(order["requested_entry_price"]),
            "stop_loss": float(order["stop_loss"]),
            "take_profit": float(order["take_profit"]),
            "atr_at_signal": float(order["atr_at_signal"]),
        }
        try:
            from paper.circuit_breaker import check_circuit_breakers
            allowed, breaker_msg = check_circuit_breakers()
            if not allowed:
                logger.info("Pending paper order #%s waiting: %s", order.get("id"), breaker_msg)
                continue
            fill = fill_pending_order(
                int(order["id"]),
                float(order["requested_entry_price"]),
                lambda conn: _insert_position(
                    cfg, trade, float(open_candle["close"]), entry_time,
                    ml_model_id=order.get("ml_model_id"),
                    ml_probability=order.get("ml_probability"),
                    connection=conn,
                ),
            )
            if fill["status"] == "filled":
                result["filled"] += 1
                _send_paper_entry_alert(order)
            elif fill["status"] == "rejected":
                result["rejected"] += 1
            elif fill["status"] == "expired":
                result["expired"] += 1
        except Exception:
            logger.exception("Unable to recover/fill pending paper order #%s", order.get("id"))
    return result

def sync_and_evaluate_paper_trading() -> Dict[str, Any]:
    """Evaluate active configs and open positions with canonical accounting."""
    configs = get_paper_configs()
    order_recovery = _process_pending_paper_orders(configs)
    active_cfgs = [c for c in configs if c["is_active"]]
    positions = get_active_positions()
    open_pos_map = {(p["symbol"], p["market"], p["timeframe"], p["strategy_name"]): p for p in positions}
    result = {"evaluated_configs": len(active_cfgs), "positions_updated": 0,
              "new_positions_opened": order_recovery["filled"], "positions_closed": 0,
              "orders_filled": order_recovery["filled"], "orders_expired": order_recovery["expired"],
              "orders_rejected": order_recovery["rejected"]}

    for cfg in active_cfgs:
        symbol, market, timeframe, strategy_name = cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"]
        key = (symbol, market, timeframe, strategy_name)
        is_overlay_strategy = strategy_name in OVERLAY_STRATEGIES
        if strategy_name not in STRATEGIES and not is_overlay_strategy:
            continue
        include_funding = strategy_name in FUNDING_STRATEGIES
        # Incremental evaluation: fetch latest 300 bars for O(1) live polling speed
        df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=include_funding, limit=300)
        if len(df) < 50:
            continue

        latest_bar = df.iloc[-1]
        current_close = float(latest_bar["close"])
        candle_open_time = int(latest_bar["open_time"])

        if key in open_pos_map:
            pos = open_pos_map[key]

            # Per-candle idempotency. This function is now driven by a
            # continuous loop (run_paper_engine.py, ~60s), not just manual
            # button clicks -- so the same latest closed candle is seen many
            # times. Everything below (holding_bars += 1, SL/TP/trailing
            # against this bar's range) must be applied once per candle, or
            # a 1h strategy would count ~60 "bars" per hour and time-stop
            # ~60x too early. last_update_time is the open_time of the last
            # candle already applied to this position (see _insert_position).
            if int(pos.get("last_update_time") or 0) >= candle_open_time:
                continue

            direction = pos["direction"]
            entry_price = float(pos["entry_price"])
            initial_risk = float(pos["initial_risk"])
            current_stop = float(pos["current_stop"])
            take_profit = float(pos["take_profit"])
            trailing_active = bool(pos["trailing_active"])
            trail_act_r = float(pos["trail_activation_r"])
            trail_dist_mult = float(pos["trail_distance_atr_mult"])

            holding_bars = int(pos["holding_bars"]) + 1

            if direction == "LONG":
                unrealized_pct = (current_close - entry_price) / entry_price
                unrealized_r = (current_close - entry_price) / initial_risk if initial_risk > 0 else 0.0
            else:
                unrealized_pct = (entry_price - current_close) / entry_price
                unrealized_r = (entry_price - current_close) / initial_risk if initial_risk > 0 else 0.0

            # Robust ATR validation: do not silently swallow NaN. Must run
            # AFTER unrealized_r is computed above -- it's referenced in the
            # warning log below (this ordering bug previously caused a
            # NameError -> uncaught 500 on every /api/paper/sync call that
            # touched a position with invalid/NaN ATR on its latest bar).
            raw_atr = latest_bar.get("atr")
            if pd.isna(raw_atr) or float(raw_atr or 0.0) <= 0:
                atr = 0.0
                if unrealized_r >= trail_act_r:
                    logger.warning(
                        "Paper position #%s: trailing stop threshold reached (unrealized_r=%.2f >= %.2f) "
                        "but ATR is invalid/NaN (%s); cannot trail stop on this bar",
                        pos["id"], unrealized_r, trail_act_r, raw_atr
                    )
            else:
                atr = float(raw_atr)

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
            if is_overlay_strategy:
                # Overlay strategies (e.g. news_ai_overlay) don't generate
                # entries from price action here -- their entries are
                # inserted directly by their own module (ai/news_execution.py
                # for news_ai_overlay), using insert_paper_position(). This
                # branch's only job for them is the exit-management above,
                # which already ran. Deliberately NOT in STRATEGIES / not
                # backtestable the normal way -- see docs/NEWS_AI.md §2.
                continue
            # Signal decisions use only the latest CLOSED candle. A paper
            # entry is allowed only on the immediately following, still-open
            # candle; never backfill an entry at the open of a candle that has
            # already closed while this process was waiting/polling.
            if _ML_REJECTED_CANDLE.get(key) == candle_open_time:
                continue
            open_candle = fetch_latest_open_candle(symbol, market, timeframe)
            signal_df = _append_next_open_candle(df, open_candle, timeframe)
            if signal_df is None:
                logger.debug(
                    "Skipping entry evaluation for %s/%s: no immediately following open candle",
                    symbol, strategy_name,
                )
                continue
            entry_candle_time = int(open_candle["open_time"])
            if _signal_already_traded(cfg, entry_candle_time):
                continue

            # Check risk circuit breakers before discovering / opening new trades
            from paper.circuit_breaker import check_circuit_breakers
            allowed, breaker_msg = check_circuit_breakers()
            if not allowed:
                logger.info("Skipping signal evaluation for %s: %s", symbol, breaker_msg)
                continue

            run_params = dict(BASE_PARAMS, symbol=symbol, market=market, timeframe=timeframe)
            if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
                run_params["htf_df"] = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True, limit=300)
            if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
                run_params["pair_df"] = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True, limit=300)
                run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

            sim_trades = STRATEGIES[strategy_name](signal_df, run_params)
            if sim_trades:
                latest_trade = sim_trades[-1]
                # Accept only a trade signaled on the latest closed candle
                # and entered at the current candle's open. This prevents the
                # previous retroactive fill at a candle open after its range
                # was already known.
                if (latest_trade.get("signal_open_time") == candle_open_time
                        and latest_trade.get("entry_time") == entry_candle_time):
                    # ML Signal Filtering Gate (if model attached to this paper config)
                    ml_model_id = cfg.get("ml_model_id")
                    ml_probability = None
                    ml_threshold = None

                    if ml_model_id:
                        try:
                            import json
                            from pathlib import Path
                            import joblib
                            from ml.artifacts import ModelArtifact
                            from ml.strategy import MLSignalFilter

                            manifest_path = Path("ml_models") / f"{ml_model_id}.json"
                            model_path = Path("ml_models") / f"{ml_model_id}.joblib"
                            if manifest_path.exists() and model_path.exists():
                                with open(manifest_path, "r", encoding="utf-8") as mf:
                                    manifest = json.load(mf)
                                artifact = ModelArtifact(**manifest)
                                model = joblib.load(model_path)
                                ml_filter = MLSignalFilter(artifact=artifact, model=model, threshold=artifact.threshold)

                                # Evaluate features on current candle
                                feature_row = pd.DataFrame([latest_bar.to_dict()])
                                probs = ml_filter.predict_probability(feature_row)
                                ml_probability = float(probs[0])
                                ml_threshold = float(ml_filter.threshold)

                                if ml_probability < ml_threshold:
                                    _ML_REJECTED_CANDLE[key] = candle_open_time
                                    logger.info(
                                        "Paper trading signal %s (%s) REJECTED by ML model %s: prob=%.4f < threshold=%.4f",
                                        symbol, latest_trade["direction"], ml_model_id, ml_probability, ml_threshold
                                    )
                                    continue
                                logger.info(
                                    "Paper trading signal %s (%s) ACCEPTED by ML model %s: prob=%.4f >= threshold=%.4f",
                                    symbol, latest_trade["direction"], ml_model_id, ml_probability, ml_threshold
                                )
                            else:
                                # Fail closed: a configured safety filter that cannot run must not be skipped.
                                logger.error("ML model files for %s not found in ml_models/; skipping entry (fail closed)", ml_model_id)
                                continue
                        except Exception as ml_err:
                            logger.error("Error evaluating ML signal filter %s: %s; skipping entry (fail closed)", ml_model_id, ml_err)
                            continue

                    # Persist only after the ML gate succeeds. If the process stops
                    # after persistence, the next sync recovers this pending order.
                    try:
                        order = create_pending_order({
                            "config_id": cfg["id"], "symbol": symbol, "market": market,
                            "timeframe": timeframe, "strategy_name": strategy_name,
                            "signal_open_time": candle_open_time, "entry_time": entry_candle_time,
                            "direction": latest_trade["direction"],
                            "requested_entry_price": float(latest_trade["entry_price"]),
                            "stop_loss": float(latest_trade["stop_loss"]),
                            "take_profit": float(latest_trade["take_profit"]),
                            "atr_at_signal": float(latest_trade.get("atr_at_signal", latest_bar.get("atr", 0)) or 0),
                            "ml_model_id": ml_model_id, "ml_probability": ml_probability,
                        })
                    except Exception:
                        logger.exception("Unable to persist paper order for %s/%s", symbol, strategy_name)
                        continue

                    # Recheck the shared emergency stop immediately before the
                    # transactional fill; it may have changed after signal evaluation.
                    from paper.circuit_breaker import check_circuit_breakers
                    fill_allowed, fill_block_reason = check_circuit_breakers()
                    if not fill_allowed:
                        logger.warning("Paper order #%s remains pending: %s", order["id"], fill_block_reason)
                        continue
                    # Use the durable row as the source of truth if this is an
                    # idempotent retry, rather than recalculated strategy output.
                    if (order.get("status") != "PENDING"
                            or int(order.get("entry_time") or 0) != entry_candle_time
                            or order.get("symbol") != symbol
                            or order.get("market") != market
                            or order.get("timeframe") != timeframe
                            or order.get("strategy_name") != strategy_name):
                        continue
                    durable_trade = {
                        "direction": order["direction"],
                        "entry_price": float(order["requested_entry_price"]),
                        "stop_loss": float(order["stop_loss"]),
                        "take_profit": float(order["take_profit"]),
                        "atr_at_signal": float(order["atr_at_signal"]),
                    }
                    durable_ml_model_id = order.get("ml_model_id")
                    durable_ml_probability = order.get("ml_probability")
                    order_result = fill_pending_order(
                        int(order["id"]),
                        float(order["requested_entry_price"]),
                        lambda conn: _insert_position(
                            cfg, durable_trade, float(open_candle["close"]), entry_candle_time,
                            ml_model_id=durable_ml_model_id, ml_probability=durable_ml_probability,
                            connection=conn,
                        ),
                    )
                    if order_result["status"] == "expired":
                        result["orders_expired"] += 1
                    if order_result["status"] == "rejected":
                        result["orders_rejected"] += 1
                    if order_result["status"] == "filled":
                        result["new_positions_opened"] += 1
                        result["orders_filled"] += 1
                        logger.info("Filled paper order #%s for %s (%s) via %s", order["id"], symbol, latest_trade["direction"], strategy_name)
                        _send_paper_entry_alert(order, ml_threshold=ml_threshold)


    return result
