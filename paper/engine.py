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
import json
import logging
import re
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
from backtest.custom_strategy import paper_strategy_registry
from backtest.simulate import trailing_bar
from backtest.params import BASE_PARAMS
from backtest.accounting import calculate_position_size, calculate_trade_accounting, resolve_stop_target_exit
from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT, PAIRS_BASE_SYMBOL
from paper.orders import create_pending_order, fill_pending_order, list_orders, transition_order

logger = logging.getLogger("paper.engine")

PAPER_ENGINE_LOCK_NAME = "paper_engine_lock"
PAPER_ENGINE_LOCK_TIMEOUT_SECONDS = 0


def get_dedicated_connection(config_dict: Optional[Dict[str, Any]] = None):
    """
    Establish an unpooled, dedicated MariaDB connection.
    Used strictly for advisory lock ownership to guarantee that the connection
    is never returned to a pool or shared across threads while holding the lock.
    """
    import mysql.connector
    from config import MYSQL_CONFIG

    cfg = dict(config_dict or MYSQL_CONFIG)
    for k in ("pool_name", "pool_size", "pool_reset_session"):
        cfg.pop(k, None)
    return mysql.connector.connect(
        init_command="SET time_zone = '+00:00'",
        **cfg
    )


class PaperEngineAdvisoryLock:
    """
    Dedicated database connection advisory lock manager for the paper trading engine.
    Ensures that only one paper-engine execution runs at any time across systemd, CLI,
    cron, or web evaluation triggers.
    """
    def __init__(
        self,
        lock_name: str = PAPER_ENGINE_LOCK_NAME,
        timeout: int = PAPER_ENGINE_LOCK_TIMEOUT_SECONDS,
        config_dict: Optional[Dict[str, Any]] = None,
    ):
        self.lock_name = lock_name
        self.timeout = int(timeout)
        self.config_dict = config_dict
        self.conn = None
        self.acquired = False

    def acquire(self) -> bool:
        """
        Attempt to acquire the MariaDB advisory lock on a dedicated connection.
        Returns True if acquired, False otherwise. Fully drains cursor results.
        """
        if self.acquired:
            return True
        cur = None
        try:
            self.conn = get_dedicated_connection(self.config_dict)
            cur = self.conn.cursor()
            cur.execute("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
            rows = cur.fetchall()
            if rows and len(rows) > 0 and rows[0][0] == 1:
                self.acquired = True
                logger.debug("Acquired paper engine advisory lock '%s'", self.lock_name)
                return True
            else:
                self.acquired = False
                logger.debug(
                    "Could not acquire paper engine advisory lock '%s' (timeout=%s)",
                    self.lock_name, self.timeout
                )
                if self.conn:
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                    self.conn = None
                return False
        except Exception as exc:
            logger.warning("Error acquiring paper engine advisory lock '%s': %s", self.lock_name, exc)
            self.acquired = False
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
            return False
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass

    def is_owned(self) -> bool:
        """Verify that this connection currently owns the advisory lock."""
        if not self.conn or not self.acquired:
            return False
        cur = None
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID()", (self.lock_name,))
            rows = cur.fetchall()
            return bool(rows and len(rows) > 0 and rows[0][0] == 1)
        except Exception as exc:
            logger.warning("Error checking ownership of advisory lock '%s': %s", self.lock_name, exc)
            return False
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass

    def release(self) -> bool:
        """
        Release the advisory lock and close the dedicated connection.
        Guaranteed to clean up connection resources.
        """
        released = False
        if self.conn:
            cur = None
            try:
                if self.acquired:
                    cur = self.conn.cursor()
                    cur.execute("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
                    rows = cur.fetchall()
                    if rows and len(rows) > 0 and rows[0][0] == 1:
                        released = True
                        logger.debug("Released paper engine advisory lock '%s'", self.lock_name)
                    else:
                        logger.warning(
                            "Advisory lock '%s' was not held by this connection on release",
                            self.lock_name
                        )
            except Exception as exc:
                logger.warning("Error releasing advisory lock '%s': %s", self.lock_name, exc)
            finally:
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:
                        pass
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                self.acquired = False
        return released

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def _load_verified_ml_model(ml_model_id: str, models_dir: str = "ml_models"):
    """Load an eligible paper model only after same-descriptor hash verification."""
    from ml.artifacts import (
        load_eligible_paper_artifact,
        read_model_manifest_file,
        resolve_model_manifest_path,
    )
    from ml.model_store import ModelStore

    manifest_path = resolve_model_manifest_path(ml_model_id, models_dir)
    manifest = read_model_manifest_file(manifest_path)
    artifact = load_eligible_paper_artifact(manifest, ml_model_id)
    model = ModelStore(models_dir).load(artifact, artifact.feature_columns, manifest=manifest)
    return artifact, model

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
    if "close_time" in row.index:
        row["close_time"] = entry_time + interval_ms - 1
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
                   allocated_capital, risk_per_trade_pct, ml_model_id,
                   ml_generator_model_id, telegram_enabled, report_enabled, created_at
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
            r["ml_generator_model_id"] = r.get("ml_generator_model_id")
            r["telegram_enabled"] = bool(r.get("telegram_enabled", False))
            r["report_enabled"] = bool(r.get("report_enabled", True))
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
    telegram_enabled: Optional[bool] = None,
    report_enabled: Optional[bool] = None,
    ml_generator_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    if allocated_capital <= 0:
        raise ValueError("allocated_capital must be positive")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")

    # Keep every persisted identifier within the paper_configs column widths.
    for field_name, value, max_length in (
        ("symbol", symbol, 20),
        ("market", market, 7),
        ("timeframe", timeframe, 10),
        ("strategy_name", strategy_name, 50),
    ):
        if not isinstance(value, str) or len(value) > max_length:
            raise ValueError(f"{field_name} must be a string of at most {max_length} characters")
    for field_name, value in (
        ("ml_model_id", ml_model_id),
        ("ml_generator_model_id", ml_generator_model_id),
    ):
        if value is not None and (
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value)
            or ".." in value
        ):
            raise ValueError(f"{field_name} must be a safe model identifier")
    if ml_model_id and ml_generator_model_id:
        raise ValueError("a paper config cannot use both an ML filter and a generator model")
    if strategy_name == "ml_signal_generator_v1" and not ml_generator_model_id:
        raise ValueError("ml_signal_generator_v1 requires ml_generator_model_id")
    if strategy_name != "ml_signal_generator_v1" and ml_generator_model_id:
        raise ValueError("ml_generator_model_id is only valid for ml_signal_generator_v1")

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        if telegram_enabled is None and report_enabled is None:
            # Legacy callers may update activation/capital without changing the
            # separately configured alert or report visibility preferences.
            cur.execute(
                """
                INSERT INTO paper_configs
                    (symbol, market, timeframe, strategy_name, is_active,
                     allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_active = VALUES(is_active),
                    allocated_capital = VALUES(allocated_capital),
                    risk_per_trade_pct = VALUES(risk_per_trade_pct),
                    ml_model_id = VALUES(ml_model_id),
                    ml_generator_model_id = VALUES(ml_generator_model_id)
                """,
                (symbol, market, timeframe, strategy_name, 1 if is_active else 0,
                 allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id),
            )
        elif report_enabled is None:
            cur.execute(
                """
                INSERT INTO paper_configs
                    (symbol, market, timeframe, strategy_name, is_active,
                     allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id, telegram_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_active = VALUES(is_active),
                    allocated_capital = VALUES(allocated_capital),
                    risk_per_trade_pct = VALUES(risk_per_trade_pct),
                    ml_model_id = VALUES(ml_model_id),
                    ml_generator_model_id = VALUES(ml_generator_model_id),
                    telegram_enabled = VALUES(telegram_enabled)
                """,
                (symbol, market, timeframe, strategy_name, 1 if is_active else 0,
                 allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id, bool(telegram_enabled)),
            )
        elif telegram_enabled is None:
            cur.execute(
                """
                INSERT INTO paper_configs
                    (symbol, market, timeframe, strategy_name, is_active,
                     allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id, report_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_active = VALUES(is_active),
                    allocated_capital = VALUES(allocated_capital),
                    risk_per_trade_pct = VALUES(risk_per_trade_pct),
                    ml_model_id = VALUES(ml_model_id),
                    ml_generator_model_id = VALUES(ml_generator_model_id),
                    report_enabled = VALUES(report_enabled)
                """,
                (symbol, market, timeframe, strategy_name, 1 if is_active else 0,
                 allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id, bool(report_enabled)),
            )
        else:
            cur.execute(
                """
                INSERT INTO paper_configs
                    (symbol, market, timeframe, strategy_name, is_active,
                     allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id,
                     telegram_enabled, report_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_active = VALUES(is_active),
                    allocated_capital = VALUES(allocated_capital),
                    risk_per_trade_pct = VALUES(risk_per_trade_pct),
                    ml_model_id = VALUES(ml_model_id),
                    ml_generator_model_id = VALUES(ml_generator_model_id),
                    telegram_enabled = VALUES(telegram_enabled),
                    report_enabled = VALUES(report_enabled)
                """,
                (symbol, market, timeframe, strategy_name, 1 if is_active else 0,
                 allocated_capital, risk_per_trade_pct, ml_model_id, ml_generator_model_id,
                 bool(telegram_enabled), bool(report_enabled)),
            )
        # Resolve the persisted row after the upsert so promotion audit records
        # can link to the actual paper config, including on duplicate-key updates.
        cur.execute(
            """
            SELECT id FROM paper_configs
            WHERE symbol=%s AND market=%s AND timeframe=%s AND strategy_name=%s
              AND ml_model_id <=> %s AND ml_generator_model_id <=> %s
            """,
            (symbol, market, timeframe, strategy_name, ml_model_id, ml_generator_model_id),
        )
        config_row = cur.fetchone()
        config_id = config_row.get("id") if isinstance(config_row, dict) else None
        conn.commit()
        cur.close()
        return {
            "status": "success",
            "id": config_id,
            "symbol": symbol,
            "strategy": strategy_name,
            "is_active": is_active,
            "ml_model_id": ml_model_id,
            "ml_generator_model_id": ml_generator_model_id,
        }
    finally:
        conn.close()


def delete_paper_config(config_id: int) -> bool:
    """Delete a paper configuration by ID, ensuring no open position exists for it."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, symbol, strategy_name FROM paper_positions WHERE config_id = %s AND status = 'OPEN' LIMIT 1",
            (config_id,),
        )
        open_pos = cur.fetchone()
        if open_pos:
            raise ValueError(
                f"Cannot delete paper config #{config_id}: position #{open_pos['id']} "
                f"({open_pos['symbol']} / {open_pos['strategy_name']}) is currently OPEN. Close the position first."
            )
        cur.execute("DELETE FROM paper_configs WHERE id = %s", (config_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        cur.close()
        return deleted
    finally:
        conn.close()


def batch_update_paper_configs(strategy_name: str, action: str) -> int:
    """Batch update active, telegram, or report status across all pairs for a strategy."""
    action = action.strip().lower()
    sql_map = {
        "activate_all": "UPDATE paper_configs SET is_active = 1 WHERE strategy_name = %s",
        "pause_all": "UPDATE paper_configs SET is_active = 0 WHERE strategy_name = %s",
        "telegram_all_on": "UPDATE paper_configs SET telegram_enabled = 1 WHERE strategy_name = %s",
        "telegram_all_off": "UPDATE paper_configs SET telegram_enabled = 0 WHERE strategy_name = %s",
        "report_all_on": "UPDATE paper_configs SET report_enabled = 1 WHERE strategy_name = %s",
        "report_all_off": "UPDATE paper_configs SET report_enabled = 0 WHERE strategy_name = %s",
    }
    if action not in sql_map:
        raise ValueError(f"Unknown batch action: {action}")
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql_map[action], (strategy_name,))
        updated_count = cur.rowcount
        conn.commit()
        cur.close()
        return updated_count
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
                trail_activation_r, trail_distance_atr_mult, initial_risk, custom_exit_policy,
                allocated_capital, risk_per_trade_pct, risk_amount,
                stop_distance, quantity, notional_value, entry_fee, funding_pnl,
                unrealized_pnl_pct, unrealized_r, holding_bars,
                max_hold_bars, status, last_update_time, config_id,
                ml_model_id, ml_generator_model_id, ml_probability
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
            "quantity", "notional_value", "entry_fee", "funding_pnl", "unrealized_pnl_pct",
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
                t.id, t.position_id, t.symbol, t.market, t.timeframe, t.strategy_name,
                t.direction, t.entry_time, t.entry_time_wib, t.entry_price,
                t.exit_time, t.exit_time_wib, t.exit_price, t.exit_reason,
                t.stop_loss, t.take_profit, t.quantity, t.notional_value,
                t.gross_pnl, t.entry_fee, t.exit_fee, t.funding_pnl, t.net_pnl,
                t.allocated_capital, t.risk_per_trade_pct, t.risk_amount,
                t.net_return_pct, t.r_multiple, t.holding_bars, t.created_at,
                t.config_id, t.ml_generator_model_id, t.ml_model_id, t.ml_probability
            FROM paper_trades t
            LEFT JOIN paper_configs c ON c.id = t.config_id
            WHERE COALESCE(c.report_enabled, TRUE) = TRUE
            ORDER BY t.id DESC
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
                   SUM(CASE WHEN t.r_multiple > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN t.r_multiple < 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(t.r_multiple) AS sum_r,
                   AVG(t.r_multiple) AS avg_r,
                   SUM(t.net_pnl) AS net_pnl
            FROM paper_trades t
            LEFT JOIN paper_configs c ON c.id = t.config_id
            WHERE COALESCE(c.report_enabled, TRUE) = TRUE
            """
        )
        summary = cur.fetchone()
        cur.execute(
            """
            SELECT t.strategy_name, t.config_id, t.ml_generator_model_id, t.ml_model_id,
                   COUNT(*) AS total_trades,
                   SUM(CASE WHEN t.r_multiple > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN t.r_multiple < 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(t.r_multiple) AS sum_r,
                   AVG(t.r_multiple) AS avg_r,
                   SUM(t.net_pnl) AS net_pnl
            FROM paper_trades t
            LEFT JOIN paper_configs c ON c.id = t.config_id
            WHERE COALESCE(c.report_enabled, TRUE) = TRUE
            GROUP BY t.strategy_name, t.config_id, t.ml_generator_model_id, t.ml_model_id
            ORDER BY t.strategy_name, t.config_id
            """
        )
        strategy_rows = cur.fetchall()
        cur.close()
        total = int(summary["total_trades"] or 0)
        wins = int(summary["wins"] or 0)
        losses = int(summary["losses"] or 0)
        by_strategy = []
        for row in strategy_rows:
            strategy_total = int(row["total_trades"] or 0)
            strategy_wins = int(row["wins"] or 0)
            by_strategy.append({
                "strategy": row["strategy_name"],
                "configId": row.get("config_id"),
                "ml_generator_model_id": row.get("ml_generator_model_id"),
                "ml_model_id": row.get("ml_model_id"),
                "totalTrades": strategy_total,
                "wins": strategy_wins,
                "losses": int(row["losses"] or 0),
                "winRatePct": round(strategy_wins / strategy_total * 100, 2) if strategy_total else 0.0,
                "cumulativeR": round(float(row["sum_r"] or 0.0), 4),
                "expectancyR": round(float(row["avg_r"] or 0.0), 4),
                "netPnl": round(float(row["net_pnl"] or 0.0), 8),
            })
        return {
            "totalTrades": total,
            "wins": wins,
            "losses": losses,
            "winRatePct": round(wins / total * 100, 2) if total else 0.0,
            "cumulativeR": round(float(summary["sum_r"] or 0.0), 4),
            "expectancyR": round(float(summary["avg_r"] or 0.0), 4),
            "netPnl": round(float(summary["net_pnl"] or 0.0), 8),
            "byStrategy": by_strategy,
        }
    finally:
        conn.close()


class PaperFundingRetrievalError(RuntimeError):
    """Raised when settled funding events cannot be retrieved for a paper position."""
    pass


def _funding_cost_for_position(pos: Dict[str, Any], exit_time: int) -> float:
    """Return signed funding cost for the position over its holding interval.

    Raises:
        PaperFundingRetrievalError: if settled funding events cannot be retrieved
            or validated for a supported Futures position.
    """
    if str(pos.get("market") or "").strip().lower() != "futures":
        return 0.0

    strategy_name = str(pos.get("strategy_name") or "")
    if strategy_name == "pairs_ratio_v1" or bool(pos.get("is_pairs_ratio")):
        logger.warning(
            "Paper position #%s (%s/%s): pairs_ratio trades a synthetic ratio without an "
            "executable two-leg sizing model; skipping single-leg funding accrual "
            "(unsupported_pairs_ratio_two_leg).",
            pos.get("id"), pos.get("symbol"), strategy_name,
        )
        return 0.0

    try:
        from db.db import fetch_settled_funding_events
        raw_events = fetch_settled_funding_events(
            symbol=pos["symbol"],
            start_time=int(pos["entry_time"]),
            end_time=int(exit_time),
        )
        events = [(float(ev[1]), float(ev[2])) for ev in raw_events]
    except Exception as exc:
        logger.error(
            "Failed to retrieve settled funding events for position #%s (%s): %s",
            pos.get("id"), pos.get("symbol"), exc,
        )
        raise PaperFundingRetrievalError(
            f"Failed to retrieve settled funding events for position #{pos.get('id')} ({pos.get('symbol')}): {exc}"
        ) from exc

    if not events:
        return 0.0
    # Canonical helper returns LONG funding cost. SHORT receives the inverse.
    from backtest.accounting import calculate_funding_cost
    long_cost = calculate_funding_cost(float(pos["quantity"]), events)
    return long_cost if pos["direction"] == "LONG" else -long_cost


def close_position_in_db(
    pos: Dict[str, Any],
    exit_price: float,
    exit_time: int,
    exit_reason: str,
    holding_bars: Optional[int] = None,
    funding_end_time: Optional[int] = None,
) -> bool:
    """Close one position using canonical accounting and persist the ledger."""
    if pos.get("status") != "OPEN":
        return False

    final_holding_bars = int(holding_bars if holding_bars is not None else pos.get("holding_bars", 0))

    direction = pos["direction"]
    quantity = float(pos["quantity"])
    entry_price = float(pos["entry_price"])
    stop_loss = float(pos["stop_loss"])
    funding_cost = _funding_cost_for_position(pos, exit_time if funding_end_time is None else funding_end_time)
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
    cur = None
    try:
        cur = conn.cursor()
        # Compare-and-set status makes repeated sync calls idempotent.
        cur.execute(
            """
            UPDATE paper_positions
            SET status = 'CLOSED', current_price = %s, last_update_time = %s,
                funding_pnl = %s, holding_bars = %s
            WHERE id = %s AND status = 'OPEN'
            """,
            (float(exit_price), int(exit_time), -funding_cost, final_holding_bars, pos["id"]),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False

        # Guard against duplicate paper_trades record for this position_id
        cur.execute(
            "SELECT 1 FROM paper_trades WHERE position_id = %s LIMIT 1",
            (pos["id"],),
        )
        existing_trade = cur.fetchall()
        if existing_trade and len(existing_trade) > 0:
            logger.warning(
                "Paper position #%s already has a trade record in paper_trades; skipping duplicate trade insert",
                pos["id"],
            )
            conn.commit()
            pos["status"] = "CLOSED"
            pos["holding_bars"] = final_holding_bars
            pos["last_update_time"] = int(exit_time)
            pos["current_price"] = float(exit_price)
            return True

        cur.execute(
            """
            INSERT INTO paper_trades (
                position_id, symbol, market, timeframe, strategy_name, direction,
                entry_time, entry_price, exit_time, exit_price, exit_reason,
                stop_loss, take_profit, allocated_capital, risk_per_trade_pct,
                risk_amount, stop_distance, quantity, notional_value,
                gross_pnl, entry_fee, exit_fee, funding_pnl, net_pnl,
                net_return_pct, r_multiple, holding_bars, ml_model_id, ml_probability,
                config_id, ml_generator_model_id
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                pos["id"], pos["symbol"], pos["market"], pos["timeframe"], pos["strategy_name"],
                direction, pos["entry_time"], entry_price, int(exit_time), accounting["exit_exec_price"], exit_reason,
                stop_loss, pos["take_profit"], pos["allocated_capital"], pos["risk_per_trade_pct"],
                accounting["risk_amount"], pos["stop_distance"], quantity, pos["notional_value"],
                accounting["gross_pnl"], pos["entry_fee"], accounting["fees"] - float(pos["entry_fee"]),
                -funding_cost, accounting["net_pnl"], accounting["return_pct_on_entry_notional"],
                accounting["r_multiple"], final_holding_bars, pos.get("ml_model_id"), pos.get("ml_probability"),
                pos.get("config_id"), pos.get("ml_generator_model_id"),
            ),
        )
        conn.commit()
        pos["status"] = "CLOSED"
        pos["holding_bars"] = final_holding_bars
        pos["last_update_time"] = int(exit_time)
        pos["current_price"] = float(exit_price)
        logger.info(
            "Closed paper position #%s (%s %s) via %s: %+.2fR net_pnl=%+.4f funding=%+.4f",
            pos["id"], pos["symbol"], direction, exit_reason,
            accounting["r_multiple"] or 0.0, accounting["net_pnl"], -funding_cost,
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if cur is not None:
                cur.close()
        finally:
            conn.close()

    # Gated by telegram_enabled: only send trade closure alert if enabled for this strategy
    send_close_alert = False
    try:
        conn_cfg = get_pool().get_connection()
        try:
            cur_cfg = conn_cfg.cursor(dictionary=True)
            if pos.get("config_id") is not None:
                cur_cfg.execute(
                    "SELECT telegram_enabled FROM paper_configs WHERE id=%s LIMIT 1",
                    (int(pos["config_id"]),),
                )
            else:
                cur_cfg.execute(
                    """
                    SELECT telegram_enabled FROM paper_configs
                    WHERE symbol=%s AND market=%s AND timeframe=%s AND strategy_name=%s
                    LIMIT 1
                    """,
                    (pos["symbol"], pos["market"], pos["timeframe"], pos["strategy_name"]),
                )
            cfg_row = cur_cfg.fetchone()
            cur_cfg.close()
            if cfg_row and cfg_row.get("telegram_enabled"):
                send_close_alert = True
        finally:
            conn_cfg.close()
    except Exception as exc:
        logger.warning("Could not check telegram_enabled for close alert: %s", exc)

    if send_close_alert:
        try:
            from notifications.telegram_notifier import send_trade_close_alert
            send_trade_close_alert(
                symbol=pos["symbol"], strategy_name=pos["strategy_name"], direction=direction,
                entry_price=entry_price, exit_price=accounting["exit_exec_price"], exit_reason=exit_reason,
                net_return_pct=accounting["return_pct_on_entry_notional"],
                r_multiple=accounting["r_multiple"] or 0.0, holding_bars=pos.get("holding_bars", 0),
                ml_generator_model_id=pos.get("ml_generator_model_id"),
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
    try:
        closed = close_position_in_db(pos, float(pos["current_price"]), now_ms, "MANUAL")
    except PaperFundingRetrievalError as exc:
        logger.error("Failed to manually close position #%s due to funding retrieval error: %s", position_id, exc)
        raise RuntimeError(f"Cannot close position #{position_id} due to funding retrieval failure: {exc}") from exc
    return {
        "status": "success" if closed else "already_closed",
        "message": f"Position #{position_id} manually closed",
    }


def _insert_position(
    cfg: Dict[str, Any],
    trade: Dict[str, Any],
    current_close: float,
    candle_open_time: int,
    ml_model_id: Optional[str] = None,
    ml_probability: Optional[float] = None,
    ml_generator_model_id: Optional[str] = None,
    connection=None,
) -> Optional[int]:
    """Returns the new paper_positions row id on success, or None if the
    insert was skipped as a duplicate (the unique open-position key already
    covers this symbol/market/timeframe/strategy). Callers that only care
    whether a position was opened (not the id) can still use this in a
    boolean context -- 0 is never a valid auto-increment id."""
    policy = trade.get("custom_exit_policy")
    if isinstance(policy, str):
        policy = json.loads(policy)
    direction = trade["direction"]
    entry_fill = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    take_profit = float(trade["take_profit"])
    size = calculate_position_size(
        equity=float(cfg["allocated_capital"]),
        risk_pct=float(cfg["risk_per_trade_pct"]),
        entry_price=entry_fill,
        stop_price=stop_loss,
    )
    # Portfolio-level cap: applies to every entry path (strategies and news overlay).
    from paper.circuit_breaker import check_exposure_limit
    exposure_ok, exposure_msg = check_exposure_limit(size.notional)
    if not exposure_ok:
        logger.warning("Paper entry for %s/%s blocked: %s", cfg["symbol"], cfg["strategy_name"], exposure_msg)
        return None
    entry_fee = entry_fill * size.quantity * BACKTEST_FEE_PCT
    atr = float(trade.get("atr_at_signal", 0.0) or 0.0)
    raw_max_hold_bars = policy["max_hold_bars"] if policy else trade.get("max_hold_bars")
    if ml_generator_model_id:
        # Verify the model contract again at fill time. Pending orders can be
        # recovered after a process restart or server-cost configuration change.
        # The cached typed loader reuses the already-verified artifact when warm.
        import math
        from ml.generator_runtime import load_generator_predictor
        generator_artifact, _ = load_generator_predictor(
            ml_generator_model_id, require_eligible=True
        )
        execution = generator_artifact.label_config
        if not math.isclose(float(execution["fee_rate"]), BACKTEST_FEE_PCT, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("generator fee assumptions differ from current paper accounting")
        if not math.isclose(float(execution["slippage_rate"]), BACKTEST_SLIPPAGE_PCT, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("generator slippage assumptions differ from current paper accounting")
        raw_artifact_max_hold = execution.get("max_hold_bars", 200)
        if (
            isinstance(raw_artifact_max_hold, bool)
            or not isinstance(raw_artifact_max_hold, (int, float))
            or not math.isfinite(float(raw_artifact_max_hold))
            or int(raw_artifact_max_hold) != raw_artifact_max_hold
            or not 1 <= int(raw_artifact_max_hold) <= 1000
        ):
            raise ValueError("verified generator artifact has an invalid max-hold setting")
        artifact_max_hold = int(raw_artifact_max_hold)
        if raw_max_hold_bars is not None and int(raw_max_hold_bars) != artifact_max_hold:
            raise ValueError("generator max-hold metadata does not match its verified artifact")
        raw_max_hold_bars = artifact_max_hold
    if raw_max_hold_bars is None:
        raw_max_hold_bars = 200
    if isinstance(raw_max_hold_bars, bool):
        raise ValueError("max_hold_bars must be an integer between 1 and 1000")
    max_hold_bars = int(raw_max_hold_bars)
    if max_hold_bars < 1 or max_hold_bars > 1000 or max_hold_bars != raw_max_hold_bars:
        raise ValueError("max_hold_bars must be an integer between 1 and 1000")

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
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO paper_positions (
                symbol, market, timeframe, strategy_name, direction,
                entry_time, entry_price, current_price, stop_loss, take_profit,
                current_stop, trailing_active,
                atr_at_signal, initial_risk, unrealized_pnl_pct, unrealized_r,
                holding_bars, max_hold_bars, last_update_time, status,
                allocated_capital, risk_per_trade_pct, risk_amount, stop_distance,
                quantity, notional_value, entry_fee, exit_fee, funding_pnl,
                ml_model_id, ml_probability, config_id, ml_generator_model_id,
                custom_exit_policy, trail_activation_r, trail_distance_atr_mult
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,0.0,0.0,
                0,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,0.0,0.0,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"], direction,
                int(candle_open_time), entry_fill, current_close, stop_loss, take_profit, stop_loss,
                atr, size.stop_distance, max_hold_bars, last_applied_candle, cfg["allocated_capital"],
                cfg["risk_per_trade_pct"], size.risk_amount, size.stop_distance,
                size.quantity, size.notional, entry_fee, ml_model_id, ml_probability,
                cfg.get("id"), ml_generator_model_id or cfg.get("ml_generator_model_id"),
                json.dumps(policy) if policy else None,
                policy["activation_r"] if policy else 1.0, policy["distance_atr"] if policy else 1.5,
            ),
        )
        if owns_connection:
            conn.commit()
        return cur.lastrowid
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
        try:
            if cur is not None:
                cur.close()
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
        if cfg.get("id") is not None:
            cur.execute(
                "SELECT 1 FROM paper_trades WHERE config_id=%s AND entry_time=%s LIMIT 1",
                (int(cfg["id"]), int(entry_candle)),
            )
        else:
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
            ml_generator_model_id=order.get("ml_generator_model_id"),
        )
    except Exception as tel_err:
        logger.warning("Telegram entry alert failed for paper order #%s: %s", order.get("id"), tel_err)


def _trade_from_order(order):
    trade = {
        "direction": order["direction"],
        "entry_price": float(order["requested_entry_price"]),
        "stop_loss": float(order["stop_loss"]),
        "take_profit": float(order["take_profit"]),
        "atr_at_signal": float(order["atr_at_signal"]),
    }
    if order.get("custom_exit_policy") is not None:
        trade["custom_exit_policy"] = order["custom_exit_policy"]
    return trade


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
                or (cfg.get("ml_model_id") or None) != (order.get("ml_model_id") or None)
                or (cfg.get("ml_generator_model_id") or None) != (order.get("ml_generator_model_id") or None)):
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

        trade = _trade_from_order(order)
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
                    ml_generator_model_id=order.get("ml_generator_model_id"),
                    connection=conn,
                ),
            )
            if fill["status"] == "filled":
                result["filled"] += 1
                if cfg.get("telegram_enabled", False):
                    _send_paper_entry_alert(order)
            elif fill["status"] == "rejected":
                result["rejected"] += 1
            elif fill["status"] == "expired":
                result["expired"] += 1
        except Exception:
            logger.exception("Unable to recover/fill pending paper order #%s", order.get("id"))
    return result

def _sync_and_evaluate_paper_trading_impl() -> Dict[str, Any]:
    """Internal implementation of paper evaluation cycle."""
    configs = get_paper_configs()
    order_recovery = _process_pending_paper_orders(configs)
    active_cfgs = [c for c in configs if c["is_active"]]
    positions = get_active_positions()

    result = {"evaluated_configs": len(active_cfgs), "positions_updated": 0,
              "new_positions_opened": order_recovery["filled"], "positions_closed": 0,
              "orders_filled": order_recovery["filled"], "orders_expired": order_recovery["expired"],
              "orders_rejected": order_recovery["rejected"], "skipped_locked": False}

    series_cache: Dict[tuple, pd.DataFrame] = {}

    # =========================================================================
    # Phase 1: Position Lifecycle & Exit Management
    # Evaluates EVERY existing open position chronologically across all newly
    # closed candles. Operates independently of active_cfgs so positions opened
    # under disabled, demoted, or deleted configs are still managed safely.
    # =========================================================================
    remaining_open_positions: List[Dict[str, Any]] = []

    for pos in positions:
        pos_id = pos["id"]
        symbol = pos["symbol"]
        market = pos["market"]
        timeframe = pos["timeframe"]
        strategy_name = pos["strategy_name"]
        direction = pos["direction"]
        entry_price = float(pos["entry_price"])
        initial_risk = float(pos["initial_risk"])
        current_stop = float(pos["current_stop"])
        take_profit = float(pos["take_profit"])
        trailing_active = bool(pos["trailing_active"])
        policy = pos.get("custom_exit_policy")
        if isinstance(policy, str):
            policy = json.loads(policy)
        trail_act_r = float(pos["trail_activation_r"])
        trail_dist_mult = float(pos["trail_distance_atr_mult"])
        holding_bars = int(pos["holding_bars"])
        max_hold_bars = int(pos["max_hold_bars"])
        last_update_time = int(pos.get("last_update_time") or 0)

        include_funding = strategy_name in FUNDING_STRATEGIES
        cache_key = (symbol, market, timeframe, include_funding)
        if cache_key in series_cache:
            df = series_cache[cache_key].copy()
        else:
            df = fetch_ohlcv_with_features_df(
                symbol, market, timeframe, closed_only=True, include_funding=include_funding, limit=300
            )
            if not df.empty:
                series_cache[cache_key] = df
            df = df.copy()

        if df.empty or len(df) < 1:
            remaining_open_positions.append(pos)
            continue

        new_bars = df[df["open_time"] > last_update_time]
        if new_bars.empty:
            remaining_open_positions.append(pos)
            continue

        position_closed = False
        bars_evaluated = 0
        funding_error_occurred = False

        for _, bar in new_bars.iterrows():
            bar_open_time = int(bar["open_time"])
            bar_open = float(bar["open"])
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])
            holding_bars += 1
            bars_evaluated += 1

            if direction == "LONG":
                unrealized_pct = (bar_close - entry_price) / entry_price
                unrealized_r = (bar_close - entry_price) / initial_risk if initial_risk > 0 else 0.0
            else:
                unrealized_pct = (entry_price - bar_close) / entry_price
                unrealized_r = (entry_price - bar_close) / initial_risk if initial_risk > 0 else 0.0

            raw_atr = bar.get("atr")
            if pd.isna(raw_atr) or float(raw_atr or 0.0) <= 0:
                atr = 0.0
                if policy is None and unrealized_r >= trail_act_r:
                    logger.warning(
                        "Paper position #%s: trailing stop threshold reached (unrealized_r=%.2f >= %.2f) "
                        "but ATR is invalid/NaN (%s); cannot trail stop on this bar",
                        pos_id, unrealized_r, trail_act_r, raw_atr
                    )
            else:
                atr = float(raw_atr)

            exit_reason = None
            exit_price = None

            if policy and policy["enabled"]:
                entry_open = policy["entry_open"]
                exit_price, exit_reason, current_stop, trailing_active = trailing_bar(
                    direction, bar, current_stop, trailing_active, entry_open,
                    abs(entry_open - float(pos["stop_loss"])), trail_act_r,
                    trail_dist_mult * policy["atr"],
                )
            elif trailing_active:
                if direction == "LONG":
                    if bar_open <= current_stop:
                        exit_reason, exit_price = "TRAIL", bar_open
                    elif bar_low <= current_stop:
                        exit_reason, exit_price = "TRAIL", current_stop
                else:
                    if bar_open >= current_stop:
                        exit_reason, exit_price = "TRAIL", bar_open
                    elif bar_high >= current_stop:
                        exit_reason, exit_price = "TRAIL", current_stop
            else:
                resolved_exit = resolve_stop_target_exit(
                    direction,
                    bar_open,
                    bar_high,
                    bar_low,
                    current_stop,
                    take_profit,
                )
                if resolved_exit is not None:
                    exit_price, exit_code = resolved_exit
                    exit_reason = "SL" if exit_code == "SL" else "TP"

            if exit_reason is None and holding_bars >= max_hold_bars:
                exit_reason, exit_price = "TIMEOUT", bar_close
            elif policy is None and exit_reason is None and unrealized_r >= trail_act_r and atr > 0:
                if direction == "LONG":
                    trailing_active = True
                    current_stop = max(current_stop, bar_close - trail_dist_mult * atr)
                else:
                    trailing_active = True
                    current_stop = min(current_stop, bar_close + trail_dist_mult * atr)

            if exit_reason:
                try:
                    funding_end = bar.get("close_time")
                    if exit_reason != "TIMEOUT" and exit_price == bar_open:
                        funding_end = bar_open_time - 1
                    elif pd.isna(funding_end):
                        funding_end = bar_open_time + _TIMEFRAME_MS[timeframe] - 1
                    if close_position_in_db(pos, float(exit_price), bar_open_time, exit_reason,
                                            holding_bars=holding_bars, funding_end_time=int(funding_end)):
                        result["positions_closed"] += 1
                        position_closed = True
                except PaperFundingRetrievalError as exc:
                    logger.error(
                        "Paper position #%s (%s): funding retrieval failed during exit (%s) at candle %s: %s; "
                        "preserving position as OPEN at last_update_time %s for retry on next cycle.",
                        pos_id, symbol, exit_reason, bar_open_time, exc, last_update_time,
                    )
                    remaining_open_positions.append(pos)
                    funding_error_occurred = True
                    break
                break

        if funding_error_occurred:
            continue

        if not position_closed and bars_evaluated > 0:
            latest_evaluated_bar = new_bars.iloc[-1]
            latest_candle_time = int(latest_evaluated_bar["open_time"])
            latest_close = float(latest_evaluated_bar["close"])
            if direction == "LONG":
                final_unrealized_pct = (latest_close - entry_price) / entry_price
                final_unrealized_r = (latest_close - entry_price) / initial_risk if initial_risk > 0 else 0.0
            else:
                final_unrealized_pct = (entry_price - latest_close) / entry_price
                final_unrealized_r = (entry_price - latest_close) / initial_risk if initial_risk > 0 else 0.0

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
                    (latest_close, current_stop, 1 if trailing_active else 0,
                     final_unrealized_pct, final_unrealized_r, holding_bars,
                     latest_candle_time, pos_id),
                )
                conn.commit()
                cur.close()
                result["positions_updated"] += 1
                pos["holding_bars"] = holding_bars
                pos["last_update_time"] = latest_candle_time
                pos["current_price"] = latest_close
                pos["current_stop"] = current_stop
                pos["trailing_active"] = trailing_active
                remaining_open_positions.append(pos)
            finally:
                conn.close()

    # =========================================================================
    # Phase 2: Signal Discovery & Entry Evaluation
    # Evaluates new entry signals only for active configs without open positions.
    # =========================================================================
    open_config_ids = {int(p["config_id"]) for p in remaining_open_positions if p.get("config_id") is not None}
    open_tuples = {
        (p["symbol"], p["market"], p["timeframe"], p["strategy_name"])
        for p in remaining_open_positions
    }

    strategies = paper_strategy_registry(STRATEGIES)
    for cfg in active_cfgs:
        symbol, market, timeframe, strategy_name = cfg["symbol"], cfg["market"], cfg["timeframe"], cfg["strategy_name"]
        cfg_id = int(cfg["id"]) if cfg.get("id") is not None else None
        key = cfg_id if cfg_id is not None else (symbol, market, timeframe, strategy_name)

        if cfg_id is not None and cfg_id in open_config_ids:
            continue
        if (symbol, market, timeframe, strategy_name) in open_tuples:
            continue

        is_generator_strategy = strategy_name == "ml_signal_generator_v1"
        is_overlay_strategy = strategy_name in OVERLAY_STRATEGIES
        if strategy_name not in strategies and not is_overlay_strategy:
            logger.warning("Unknown paper strategy %s for config %s", strategy_name, cfg_id)
            continue
        if is_overlay_strategy:
            continue

        include_funding = strategy_name in FUNDING_STRATEGIES
        cache_key = (symbol, market, timeframe, include_funding)
        if cache_key in series_cache:
            df = series_cache[cache_key].copy()
        else:
            df = fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=include_funding, limit=300)
            if not df.empty:
                series_cache[cache_key] = df
            df = df.copy()
        if len(df) < 50:
            continue

        latest_bar = df.iloc[-1]
        candle_open_time = int(latest_bar["open_time"])

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
        if strategy_name == "ml_signal_generator_v1":
            generator_model_id = cfg.get("ml_generator_model_id")
            if not generator_model_id or cfg.get("ml_model_id"):
                logger.error("Generator paper config #%s has an invalid model association; skipping entry", cfg.get("id"))
                continue
            run_params["ml_generator_model_id"] = generator_model_id
            run_params["_require_generator_eligible"] = True
        if strategy_name in TREND_ALIGNMENT_STRATEGIES and timeframe != HTF_TIMEFRAME:
            htf_key = (symbol, market, HTF_TIMEFRAME, False)
            if htf_key in series_cache:
                run_params["htf_df"] = series_cache[htf_key].copy()
            else:
                htf_df = fetch_ohlcv_with_features_df(symbol, market, HTF_TIMEFRAME, closed_only=True, limit=300)
                if not htf_df.empty:
                    series_cache[htf_key] = htf_df
                run_params["htf_df"] = htf_df.copy()
        if strategy_name in PAIRS_STRATEGIES and symbol != PAIRS_BASE_SYMBOL:
            pair_key = (PAIRS_BASE_SYMBOL, market, timeframe, False)
            if pair_key in series_cache:
                run_params["pair_df"] = series_cache[pair_key].copy()
            else:
                pair_df = fetch_ohlcv_with_features_df(PAIRS_BASE_SYMBOL, market, timeframe, closed_only=True, limit=300)
                if not pair_df.empty:
                    series_cache[pair_key] = pair_df
                run_params["pair_df"] = pair_df.copy()
            run_params["pair_symbol"] = PAIRS_BASE_SYMBOL

        sim_trades = strategies[strategy_name](signal_df, run_params)
        if sim_trades:
            latest_trade = sim_trades[-1]
            # Accept only a trade signaled on the latest closed candle
            # and entered at the current candle's open. This prevents the
            # previous retroactive fill at a candle open after its range
            # was already known.
            if (latest_trade.get("signal_open_time") == candle_open_time
                    and latest_trade.get("entry_time") == entry_candle_time):
                # Existing ML filter remains a separate optional gate. The
                # standalone generator reports its own confidence metadata.
                ml_model_id = cfg.get("ml_model_id")
                ml_probability = (
                    latest_trade.get("ml_probability")
                    if strategy_name == "ml_signal_generator_v1" else None
                )
                ml_threshold = None

                if ml_model_id:
                    try:
                        from ml.strategy import MLSignalFilter

                        artifact, model = _load_verified_ml_model(ml_model_id)
                        ml_filter = MLSignalFilter.from_artifact(
                            artifact=artifact,
                            model=model,
                            base_strategy=strategies[strategy_name],
                        )

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
                        "custom_exit_policy": latest_trade.get("custom_exit_policy"),
                        "requested_entry_price": float(latest_trade["entry_price"]),
                        "stop_loss": float(latest_trade["stop_loss"]),
                        "take_profit": float(latest_trade["take_profit"]),
                        "atr_at_signal": float(latest_trade.get("atr_at_signal", latest_bar.get("atr", 0)) or 0),
                        "ml_model_id": ml_model_id,
                        "ml_generator_model_id": cfg.get("ml_generator_model_id"),
                        "ml_probability": ml_probability,
                        "telegram_enabled": bool(cfg.get("telegram_enabled", False)),
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
                        or order.get("strategy_name") != strategy_name
                        or (order.get("ml_model_id") or None) != (cfg.get("ml_model_id") or None)
                        or (order.get("ml_generator_model_id") or None) != (cfg.get("ml_generator_model_id") or None)):
                    continue
                durable_trade = _trade_from_order(order)
                durable_ml_model_id = order.get("ml_model_id")
                durable_ml_probability = order.get("ml_probability")
                durable_generator_model_id = order.get("ml_generator_model_id")
                order_result = fill_pending_order(
                    int(order["id"]),
                    float(order["requested_entry_price"]),
                    lambda conn: _insert_position(
                        cfg, durable_trade, float(open_candle["close"]), entry_candle_time,
                        ml_model_id=durable_ml_model_id, ml_probability=durable_ml_probability,
                        ml_generator_model_id=durable_generator_model_id, connection=conn,
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
                    if cfg.get("telegram_enabled", False):
                        _send_paper_entry_alert(order, ml_threshold=ml_threshold)


    return result


def sync_and_evaluate_paper_trading(
    lock_timeout: int = PAPER_ENGINE_LOCK_TIMEOUT_SECONDS,
    bypass_lock: bool = False,
) -> Dict[str, Any]:
    """
    Evaluate active configs and open positions with canonical accounting and
    concurrency protection.

    Acquires a dedicated MariaDB advisory lock (paper_engine_lock) before reading
    or mutating positions. If another engine process/thread holds the lock,
    exits safely without processing.
    """
    if not bypass_lock:
        lock = PaperEngineAdvisoryLock(timeout=lock_timeout)
        if not lock.acquire():
            logger.info(
                "Paper engine evaluation skipped: advisory lock '%s' is held by another process",
                PAPER_ENGINE_LOCK_NAME,
            )
            return {
                "evaluated_configs": 0,
                "positions_updated": 0,
                "new_positions_opened": 0,
                "positions_closed": 0,
                "orders_filled": 0,
                "orders_expired": 0,
                "orders_rejected": 0,
                "skipped_locked": True,
            }
        try:
            return _sync_and_evaluate_paper_trading_impl()
        finally:
            lock.release()
    else:
        return _sync_and_evaluate_paper_trading_impl()
