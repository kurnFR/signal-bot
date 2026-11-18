"""Comprehensive Deterministic Integration Tests for Paper Position Lifecycle.

Executes strictly against the isolated test database `signal_bot_test`.
Never mutates production database `crypto_signals`.
Covers all 20 scenarios required by Work Order 71918.
"""
import os
import unittest
import importlib
import pandas as pd
from unittest.mock import patch

# Point configuration to isolated test database
os.environ["MYSQL_DATABASE"] = "signal_bot_test"
import config
importlib.reload(config)
import db.db as db
db._pool = None

import paper.engine as pe
from backtest.accounting import calculate_trade_accounting
from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT


class TestPaperPositionLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reset pool to use signal_bot_test
        db._pool = None
        cls.pool = db.get_pool()
        conn = cls.pool.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT DATABASE(), CURRENT_USER()")
        cls.db_name, cls.db_user = cur.fetchone()
        cur.close()
        conn.close()
        assert cls.db_name == "signal_bot_test", f"Safety assertion failed: connected to {cls.db_name}"

    def setUp(self):
        # Clean test tables in signal_bot_test before each test
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM paper_trades")
            cur.execute("DELETE FROM paper_orders")
            cur.execute("DELETE FROM paper_positions")
            cur.execute("DELETE FROM paper_configs")
            cur.execute("DELETE FROM funding_rate")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _create_config(self, symbol="BTCUSDT", market="spot", timeframe="1h", strategy_name="trend_ema_v1", is_active=1):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO paper_configs (symbol, market, timeframe, strategy_name, is_active, allocated_capital, risk_per_trade_pct)
                VALUES (%s, %s, %s, %s, %s, 5000.0, 1.0)
                """,
                (symbol, market, timeframe, strategy_name, is_active)
            )
            conn.commit()
            cfg_id = cur.lastrowid
            cur.close()
            return cfg_id
        finally:
            conn.close()

    def _create_position(self, config_id=None, symbol="BTCUSDT", market="spot", timeframe="1h",
                         strategy_name="trend_ema_v1", direction="LONG", entry_price=50000.0,
                         stop_loss=48000.0, take_profit=54000.0, current_price=50000.0,
                         entry_time=1760000000000, last_update_time=1760000000000,
                         quantity=0.025, notional_value=1250.0, max_hold_bars=200, holding_bars=1,
                         atr_at_signal=500.0):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO paper_positions (
                    config_id, symbol, market, timeframe, strategy_name, direction,
                    entry_time, entry_price, current_price, stop_loss, take_profit,
                    current_stop, trailing_active, atr_at_signal, initial_risk, allocated_capital,
                    risk_per_trade_pct, risk_amount, stop_distance, quantity,
                    notional_value, entry_fee, holding_bars, max_hold_bars,
                    last_update_time, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, 0, %s, %s, 5000.0,
                    1.0, 50.0, %s, %s,
                    %s, %s, %s, %s,
                    %s, 'OPEN'
                )
                """,
                (
                    config_id, symbol, market, timeframe, strategy_name, direction,
                    entry_time, entry_price, current_price, stop_loss, take_profit,
                    stop_loss, atr_at_signal, abs(entry_price - stop_loss), abs(entry_price - stop_loss),
                    quantity, notional_value, notional_value * BACKTEST_FEE_PCT,
                    holding_bars, max_hold_bars, last_update_time
                )
            )
            conn.commit()
            pos_id = cur.lastrowid
            cur.close()
            return pos_id
        finally:
            conn.close()

    def _make_df(self, bars):
        """Constructs an OHLCV DataFrame matching fetch_ohlcv_with_features_df output schema."""
        rows = []
        for b in bars:
            row = {
                "open_time": float(b["open_time"]),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": float(b.get("volume", 100.0)),
                "is_closed": 1,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "macd_hist": 0.0,
                "atr": float(b.get("atr", 500.0)),
                "atr_pct": 1.0,
                "volume_sma": 100.0,
                "volume_ratio": 1.0,
                "support": 45000.0,
                "resistance": 55000.0,
                "swing_high": 55000.0,
                "swing_low": 45000.0,
                "fib_0": 45000.0,
                "fib_236": 47000.0,
                "fib_382": 49000.0,
                "fib_5": 50000.0,
                "fib_618": 51000.0,
                "fib_786": 53000.0,
                "fib_1": 55000.0,
            }
            rows.append(row)
        return pd.DataFrame(rows)

    def test_01_normal_long_entry_and_exit(self):
        """1. Normal long entry and take-profit exit."""
        cfg_id = self._create_config(direction_name := "trend_ema_v1")
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=54000.0, last_update_time=1000)
        
        # Next bar reaches take profit (54500)
        df = self._make_df([
            {"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50800.0, "close": 54200.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        self.assertEqual(pos["status"], "CLOSED")
        self.assertEqual(float(pos["current_price"]), 54000.0)

        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trades = cur.fetchall()
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["exit_reason"], "TP")
        self.assertEqual(float(t["exit_price"]), 54000.0)
        self.assertEqual(t["exit_time"], 2000)
        self.assertGreater(float(t["net_pnl"]), 0.0)
        self.assertEqual(t["holding_bars"], 2)
        cur.close()
        conn.close()

    def test_02_normal_short_entry_and_exit(self):
        """2. Normal short entry and take-profit exit."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, direction="SHORT", entry_price=50000.0, stop_loss=52000.0, take_profit=46000.0, last_update_time=1000)
        
        df = self._make_df([
            {"open_time": 2000, "open": 49000.0, "high": 49500.0, "low": 45500.0, "close": 46200.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trades = cur.fetchall()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertEqual(float(trades[0]["exit_price"]), 46000.0)
        self.assertGreater(float(trades[0]["net_pnl"]), 0.0)
        cur.close()
        conn.close()

    def test_03_stop_loss_exit(self):
        """3. Stop-loss exit triggers at exact stop price with loss realized."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=55000.0, last_update_time=1000)
        
        df = self._make_df([
            {"open_time": 2000, "open": 49500.0, "high": 49800.0, "low": 47500.0, "close": 47800.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trades = cur.fetchall()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "SL")
        self.assertEqual(float(trades[0]["exit_price"]), 48000.0)
        self.assertLess(float(trades[0]["net_pnl"]), 0.0)
        cur.close()
        conn.close()

    def test_04_take_profit_exit(self):
        """4. Take-profit exit triggers with positive PnL."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=47000.0, take_profit=53000.0, last_update_time=1000)
        
        df = self._make_df([
            {"open_time": 2000, "open": 51000.0, "high": 53500.0, "low": 50900.0, "close": 53200.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trades = cur.fetchall()
        self.assertEqual(trades[0]["exit_reason"], "TP")
        self.assertEqual(float(trades[0]["exit_price"]), 53000.0)
        cur.close()
        conn.close()

    def test_05_exit_without_a_new_entry_signal(self):
        """5. Position exits independently of whether a new entry signal exists."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=54000.0, last_update_time=1000)
        
        df = self._make_df([
            {"open_time": 2000, "open": 49000.0, "high": 49200.0, "low": 47900.0, "close": 48100.0}
        ])

        # Even with STRATEGIES producing no signals, exit evaluation executes
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": lambda df, p: []}):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

    def test_06_position_evaluation_across_multiple_consecutive_cycles(self):
        """6. Position evaluation advances holding bars per candle and remains idempotent within candle."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=55000.0, last_update_time=1000, holding_bars=1)
        
        # Cycle 1: Bar 2000 stays inside range
        df1 = self._make_df([{"open_time": 2000, "open": 50100.0, "high": 50500.0, "low": 49800.0, "close": 50200.0}])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df1):
            res1 = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res1["positions_updated"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        p1 = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(p1["holding_bars"], 2)
        self.assertEqual(p1["last_update_time"], 2000)

        # Cycle 2: Same bar 2000 returned again (idempotent, 0 updates)
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df1):
            res2 = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res2["positions_updated"], 0)
        self.assertEqual(res2["positions_closed"], 0)

        # Cycle 3: Bar 3000 breaches stop loss
        df2 = self._make_df([
            {"open_time": 2000, "open": 50100.0, "high": 50500.0, "low": 49800.0, "close": 50200.0},
            {"open_time": 3000, "open": 50000.0, "high": 50200.0, "low": 47500.0, "close": 47800.0},
        ])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df2):
            res3 = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res3["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trade = cur.fetchone()
        self.assertIsNotNone(trade)
        self.assertEqual(trade["holding_bars"], 3)
        self.assertEqual(trade["exit_reason"], "SL")
        cur.close()
        conn.close()

    def test_07_position_evaluation_when_entry_configuration_is_disabled(self):
        """7. Inactive configuration (is_active=0) does NOT prevent existing position from closing."""
        cfg_id = self._create_config(is_active=0)
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=55000.0, last_update_time=1000)
        
        df = self._make_df([{"open_time": 2000, "open": 49000.0, "high": 49200.0, "low": 47000.0, "close": 47200.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "CLOSED")
        cur.close()
        conn.close()

    def test_08_position_evaluation_when_strategy_is_demoted(self):
        """8. Strategy not in active registry is still managed safely to exit."""
        # Config has demoted strategy name
        cfg_id = self._create_config(strategy_name="demoted_strategy_v0")
        pos_id = self._create_position(config_id=cfg_id, strategy_name="demoted_strategy_v0", direction="LONG", entry_price=50000.0, stop_loss=48000.0, take_profit=54000.0, last_update_time=1000)

        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50900.0, "close": 54100.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "CLOSED")
        cur.close()
        conn.close()

    def test_09_maximum_holding_period_timeout(self):
        """9. Maximum holding period exits with reason TIMEOUT at candle close."""
        cfg_id = self._create_config()
        # Max hold bars is 3, currently holding 2 bars
        pos_id = self._create_position(config_id=cfg_id, direction="LONG", entry_price=50000.0, stop_loss=45000.0, take_profit=60000.0, max_hold_bars=3, holding_bars=2, last_update_time=1000)
        
        # Next bar (bar 3) stays inside SL/TP range
        df = self._make_df([{"open_time": 2000, "open": 50500.0, "high": 51000.0, "low": 50200.0, "close": 50800.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        self.assertEqual(t["exit_reason"], "TIMEOUT")
        self.assertEqual(float(t["exit_price"]), 50800.0)
        self.assertEqual(t["holding_bars"], 3)
        cur.close()
        conn.close()

    def test_10_missing_market_data(self):
        """10. Missing market data does not fabricate exit or crash."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=pd.DataFrame()):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 0)
        self.assertEqual(res["positions_updated"], 0)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        cur.close()
        conn.close()

    def test_11_stale_market_data(self):
        """11. Stale market data (bars <= last_update_time) does not advance bars or fabricate exits."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=2000, holding_bars=3)

        df = self._make_df([{"open_time": 1000, "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0},
                            {"open_time": 2000, "open": 50050.0, "high": 50200.0, "low": 49800.0, "close": 50100.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 0)
        self.assertEqual(res["positions_updated"], 0)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT holding_bars, status FROM paper_positions WHERE id=%s", (pos_id,))
        p = cur.fetchone()
        self.assertEqual(p["holding_bars"], 3)
        self.assertEqual(p["status"], "OPEN")
        cur.close()
        conn.close()

    def test_12_forming_versus_finalized_candle_handling(self):
        """12. Exit evaluation strictly queries closed_only=True."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        calls = []
        def mock_fetch(sym, mkt, tf, closed_only=True, **kwargs):
            calls.append(closed_only)
            return self._make_df([{"open_time": 2000, "open": 50000.0, "high": 50100.0, "low": 49900.0, "close": 50050.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", side_effect=mock_fetch):
            pe.sync_and_evaluate_paper_trading()

        self.assertTrue(all(calls), "Every fetch call must enforce closed_only=True")

    def test_13_duplicate_exit_evaluation(self):
        """13. Evaluating an already closed position is a clean no-op."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 55000.0, "low": 50900.0, "close": 54500.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res1 = pe.sync_and_evaluate_paper_trading()
            res2 = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res1["positions_closed"], 1)
        self.assertEqual(res2["positions_closed"], 0)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 1)
        cur.close()
        conn.close()

    def test_14_duplicate_exit_order_prevention(self):
        """14. Closing a position never creates redundant orders; updates atomic status."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        df = self._make_df([{"open_time": 2000, "open": 49000.0, "high": 49200.0, "low": 47000.0, "close": 47200.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            pe.sync_and_evaluate_paper_trading()

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_orders WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 0, "No duplicate or exit orders placed in entry order ledger")
        cur.close()
        conn.close()

    def test_15_duplicate_trade_prevention(self):
        """15. Direct call to close_position_in_db is strictly idempotent via CAS update."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        first_close = pe.close_position_in_db(pos, 54000.0, 2000, "TP", holding_bars=2)
        second_close = pe.close_position_in_db(pos, 54000.0, 2000, "TP", holding_bars=2)

        self.assertTrue(first_close)
        self.assertFalse(second_close)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 1)
        cur.close()
        conn.close()

    def test_16_database_failure_during_exit_persistence(self):
        """16. Database failure during trade insert rolls back and keeps position OPEN."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        # Simulate exception during INSERT INTO paper_trades
        real_get_conn = self.pool.get_connection
        class FaultyCursor:
            def __init__(self, c):
                self._c = c
                self.rowcount = 1
            def execute(self, sql, params=None):
                if "INSERT INTO paper_trades" in sql:
                    raise RuntimeError("Simulated MariaDB crash during trade persistence")
                return self._c.execute(sql, params)
            def fetchall(self):
                return self._c.fetchall()
            def fetchone(self):
                return self._c.fetchone()
            def __getattr__(self, name):
                return getattr(self._c, name)
            def close(self):
                self._c.close()
        class FaultyConn:
            def __init__(self, c):
                self._c = c
            def cursor(self, *a, **kw):
                return FaultyCursor(self._c.cursor(*a, **kw))
            def commit(self):
                self._c.commit()
            def rollback(self):
                self._c.rollback()
            def close(self):
                self._c.close()

        with patch.object(self.pool, "get_connection", side_effect=lambda: FaultyConn(real_get_conn())):
            with self.assertRaisesRegex(RuntimeError, "Simulated MariaDB crash"):
                pe.close_position_in_db(pos, 54000.0, 2000, "TP")

        # Position must still be OPEN
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 0)
        cur.close()
        conn.close()

    def test_17_process_interruption_during_exit(self):
        """17. Process interruption before commit leaves position intact for next recovery."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        # Uncommitted connection simulates process death before commit
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE paper_positions SET current_price=52000 WHERE id=%s", (pos_id,))
        # Process interrupted: connection closes without commit
        conn.close()

        # Next cycle recovers cleanly
        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50900.0, "close": 54200.0}])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

    def test_18_recovery_after_restart(self):
        """18. Multi-candle replay after restart catches historical intrabar breach accurately."""
        cfg_id = self._create_config()
        # Position was last updated at 1000
        pos_id = self._create_position(config_id=cfg_id, direction="SHORT", entry_price=80000.0, stop_loss=84000.0, take_profit=70000.0, last_update_time=1000, holding_bars=1)

        # 3 bars elapsed while offline:
        # Bar 2000: normal
        # Bar 3000: price spikes to 85000 (breaches stop loss!)
        # Bar 4000: price drops to 68000 (below take profit!)
        df = self._make_df([
            {"open_time": 2000, "open": 81000.0, "high": 82000.0, "low": 80500.0, "close": 81500.0},
            {"open_time": 3000, "open": 82000.0, "high": 85000.0, "low": 81800.0, "close": 84500.0},
            {"open_time": 4000, "open": 72000.0, "high": 73000.0, "low": 68000.0, "close": 69000.0},
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        # MUST exit on bar 3000 with SL, NOT on bar 4000 with TP!
        self.assertEqual(t["exit_reason"], "SL")
        self.assertEqual(float(t["exit_price"]), 84000.0)
        self.assertEqual(t["exit_time"], 3000)
        self.assertEqual(t["holding_bars"], 3)
        cur.close()
        conn.close()

    def test_19_long_short_pnl_reconciliation(self):
        """19. Math parity: LONG and SHORT net PnL reconcile with canonical financial formula."""
        # Test LONG accounting with slippage
        long_acct = calculate_trade_accounting(
            direction="LONG", quantity=0.1, entry_price=50000.0, exit_price=55000.0,
            fee_pct=BACKTEST_FEE_PCT, entry_slippage_pct=0.0, exit_slippage_pct=BACKTEST_SLIPPAGE_PCT,
            stop_price=48000.0, funding_cost=0.0
        )
        expected_exit_exec = 55000.0 * (1 - BACKTEST_SLIPPAGE_PCT)
        expected_gross_pnl = (expected_exit_exec - 50000.0) * 0.1
        self.assertAlmostEqual(long_acct["gross_pnl"], expected_gross_pnl, places=4)
        expected_long_fees = (50000.0 * 0.1 + expected_exit_exec * 0.1) * BACKTEST_FEE_PCT
        self.assertAlmostEqual(long_acct["net_pnl"], expected_gross_pnl - expected_long_fees, places=4)

        # Test SHORT accounting with slippage
        short_acct = calculate_trade_accounting(
            direction="SHORT", quantity=0.1, entry_price=50000.0, exit_price=45000.0,
            fee_pct=BACKTEST_FEE_PCT, entry_slippage_pct=0.0, exit_slippage_pct=BACKTEST_SLIPPAGE_PCT,
            stop_price=52000.0, funding_cost=0.0
        )
        expected_short_exit_exec = 45000.0 * (1 + BACKTEST_SLIPPAGE_PCT)
        expected_short_gross = (50000.0 - expected_short_exit_exec) * 0.1
        self.assertAlmostEqual(short_acct["gross_pnl"], expected_short_gross, places=4)
        expected_short_fees = (50000.0 * 0.1 + expected_short_exit_exec * 0.1) * BACKTEST_FEE_PCT
        self.assertAlmostEqual(short_acct["net_pnl"], expected_short_gross - expected_short_fees, places=4)

    def test_20_fees_and_funding_reconciliation(self):
        """20. Futures funding rate events reconcile accurately into funding_pnl and net_pnl."""
        # Insert simulated funding rate event
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('BTCUSDT', 1500, 0.0010, 50000.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        cfg_id = self._create_config(market="futures")
        pos_id = self._create_position(config_id=cfg_id, market="futures", quantity=0.1, entry_time=1000, last_update_time=1000)

        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50800.0, "close": 54200.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            pe.sync_and_evaluate_paper_trading()

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        cur.close()
        conn.close()

        # Funding cost = 0.1 * 50000.0 * 0.0010 = 5.0
        # funding_pnl stored on trade is -funding_cost = -5.0
        self.assertAlmostEqual(float(t["funding_pnl"]), -5.0, places=4)

    def test_21_gap_through_stop_loss(self):
        """21. Adverse gap through stop loss fills at bar open price rather than theoretical SL."""
        cfg_id = self._create_config()
        pos_id = self._create_position(
            config_id=cfg_id, direction="LONG", entry_price=50000.0,
            stop_loss=48000.0, take_profit=56000.0, last_update_time=1000, holding_bars=1,
            quantity=0.1
        )

        # Bar opens at 46000 (gapped below stop_loss 48000)
        df = self._make_df([
            {"open_time": 2000, "open": 46000.0, "high": 46500.0, "low": 45500.0, "close": 45800.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trade = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(trade["exit_reason"], "SL")
        self.assertEqual(float(trade["exit_price"]), 46000.0)
        # Canonical accounting applies BACKTEST_SLIPPAGE_PCT (0.05%) to exit execution
        # Exit fill = 46000 * (1 - 0.0005) = 45977.0 -> gross = (45977 - 50000) * 0.1 = -402.30
        expected_gross = -402.3
        self.assertAlmostEqual(float(trade["gross_pnl"]), expected_gross, places=1)

    def test_22_trailing_stop_activation_and_closure(self):
        """22. Trailing stop activates at 1.0R, ratchets stop, and closes on adverse cross."""
        cfg_id = self._create_config()
        # initial_risk = 2000.0 (entry 50000, SL 48000). At unrealized +2000 (price 52000), R >= 1.0
        pos_id = self._create_position(
            config_id=cfg_id, direction="LONG", entry_price=50000.0,
            stop_loss=48000.0, take_profit=60000.0, last_update_time=1000, holding_bars=1,
            quantity=0.1
        )

        # Bar 1: reaches 52500, closes at 52200 with atr=800.
        # Trailing activates! New stop = 52200 - 1.5 * 800 = 51000.0
        df1 = self._make_df([
            {"open_time": 2000, "open": 50500.0, "high": 52500.0, "low": 50200.0, "close": 52200.0, "atr": 800.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df1):
            res1 = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res1["positions_closed"], 0)
        self.assertEqual(res1["positions_updated"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, trailing_active, current_stop FROM paper_positions WHERE id=%s", (pos_id,))
        p1 = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(p1["status"], "OPEN")
        self.assertEqual(p1["trailing_active"], 1)
        self.assertAlmostEqual(float(p1["current_stop"]), 51000.0, places=2)

        # Bar 2: pulls back, low crosses 51000.0 trailing stop
        df2 = self._make_df([
            {"open_time": 2000, "open": 50500.0, "high": 52500.0, "low": 50200.0, "close": 52200.0, "atr": 800.0},
            {"open_time": 3000, "open": 52000.0, "high": 52500.0, "low": 50500.0, "close": 50800.0, "atr": 800.0},
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df2):
            res2 = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res2["positions_closed"], 1)

        conn2 = self.pool.get_connection()
        cur2 = conn2.cursor(dictionary=True)
        cur2.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trade = cur2.fetchone()
        cur2.close()
        conn2.close()

        self.assertEqual(trade["exit_reason"], "TRAIL")
        self.assertAlmostEqual(float(trade["exit_price"]), 51000.0, places=2)

    def test_23_news_ai_position_lifecycle(self):
        """23. News AI created position is managed and closed via central engine loop."""
        cfg_id = self._create_config(strategy_name="news_ai_overlay")
        trade_spec = {
            "direction": "SHORT",
            "entry_price": 80000.0,
            "stop_loss": 84000.0,
            "take_profit": 72000.0,
            "atr_at_signal": 2000.0,
            "current_close": 80000.0,
            "candle_open_time": 1000,
        }
        cfg_dict = {
            "id": cfg_id, "symbol": "BTCUSDT", "market": "spot", "timeframe": "1d",
            "strategy_name": "news_ai_overlay", "allocated_capital": 5000.0,
            "risk_per_trade_pct": 0.5,
        }
        pos_id = pe.insert_paper_position(cfg_dict, trade_spec, 80000.0, 1000)
        self.assertIsNotNone(pos_id)

        # Price drops to 71000 (breaches TP 72000)
        df = self._make_df([
            {"open_time": 2000, "open": 78000.0, "high": 79000.0, "low": 71000.0, "close": 71500.0}
        ])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        cur.close()
        conn.close()

        self.assertEqual(t["strategy_name"], "news_ai_overlay")
        self.assertEqual(t["exit_reason"], "TP")
        self.assertAlmostEqual(float(t["exit_price"]), 72000.0, places=2)

    def test_24_advisory_lock_prevents_simultaneous_engine_execution(self):
        """24. Dedicated connection advisory lock skips overlapping engine invocation cleanly."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        # Acquire lock on dedicated connection
        lock = pe.PaperEngineAdvisoryLock()
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.is_owned())

        # While lock is held, calling sync_and_evaluate_paper_trading must skip
        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50900.0, "close": 54200.0}])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertTrue(res.get("skipped_locked"))
        self.assertEqual(res["evaluated_configs"], 0)
        self.assertEqual(res["positions_updated"], 0)
        self.assertEqual(res["positions_closed"], 0)

        # Position must remain intact and OPEN
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        cur.close()
        conn.close()

        # Release lock
        self.assertTrue(lock.release())
        self.assertFalse(lock.is_owned())

        # Now invocation must succeed
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res2 = pe.sync_and_evaluate_paper_trading()

        self.assertFalse(res2.get("skipped_locked"))
        self.assertEqual(res2["positions_closed"], 1)

    def test_25_advisory_lock_released_on_unhandled_exception(self):
        """25. Advisory lock is released in guaranteed cleanup even if evaluation raises."""
        with patch("paper.engine._sync_and_evaluate_paper_trading_impl", side_effect=RuntimeError("Engine failure")):
            with self.assertRaisesRegex(RuntimeError, "Engine failure"):
                pe.sync_and_evaluate_paper_trading()

        # Another instance must be able to acquire the lock immediately
        lock2 = pe.PaperEngineAdvisoryLock()
        self.assertTrue(lock2.acquire())
        self.assertTrue(lock2.release())

    def test_26_concurrent_threads_cannot_process_same_position(self):
        """26. Simultaneous invocations from concurrent threads cannot double-process positions."""
        import concurrent.futures

        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50900.0, "close": 54200.0}])

        def run_sync():
            with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
                return pe.sync_and_evaluate_paper_trading()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(run_sync)
            f2 = executor.submit(run_sync)
            r1 = f1.result()
            r2 = f2.result()

        # Exactly one should process, or if serialized, only one can close the position
        total_closed = r1["positions_closed"] + r2["positions_closed"]
        self.assertEqual(total_closed, 1)

        # Ensure exactly one trade record was inserted
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 1)
        cur.close()
        conn.close()

    def test_27_duplicate_closure_prevention_with_existing_trade(self):
        """27. Duplicate closure call on position with existing trade skips trade insert."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        # First close succeeds
        res1 = pe.close_position_in_db(pos, 54000.0, 2000, "TP")
        self.assertTrue(res1)

        # Second close with in-memory status updated returns False
        res2 = pe.close_position_in_db(pos, 54000.0, 2000, "TP")
        self.assertFalse(res2)

        # Force pos dict to claim OPEN, but database has status CLOSED
        pos["status"] = "OPEN"
        res3 = pe.close_position_in_db(pos, 54000.0, 2000, "TP")
        self.assertFalse(res3)

        # Verify exactly one trade in paper_trades
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 1)
        cur.close()
        conn.close()

    def test_28_strategy_disabled_or_removed_after_entry(self):
        """28. Positions opened under configs subsequently disabled or deleted are still closed safely."""
        cfg_id = self._create_config()
        pos_id = self._create_position(config_id=cfg_id, last_update_time=1000)

        # Deactivate config
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE paper_configs SET is_active=0 WHERE id=%s", (cfg_id,))
        conn.commit()
        cur.close()
        conn.close()

        df = self._make_df([{"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50900.0, "close": 54200.0}])

        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "CLOSED")
        cur.close()
        conn.close()


if __name__ == "__main__":
    unittest.main()
