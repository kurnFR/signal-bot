"""Comprehensive Deterministic Integration Tests for Paper Position Lifecycle.

Executes strictly against the isolated test database `signal_bot_test`.
Never mutates production database `crypto_signals`.
Covers all 20 scenarios required by Work Order 71918.
"""
import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
import unittest
import pandas as pd
from mysql.connector import Error as DatabaseError
from unittest.mock import patch

import db.db as db
import paper.engine as pe
from backtest.accounting import apply_exit_slippage, calculate_trade_accounting
from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT


class TestPaperPositionLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This suite replaces data in several tables. Require a disposable local database.
        if os.getenv("MYSQL_DATABASE") != "signal_bot_test":
            raise unittest.SkipTest("Set MYSQL_DATABASE=signal_bot_test to run against an isolated test database.")
        settings = patch.dict(db.MYSQL_CONFIG, host="127.0.0.1", port=3306, database="signal_bot_test")
        settings.start()
        cls.addClassCleanup(settings.stop)
        pool_override = patch.object(db, "_pool", None)
        pool_override.start()
        cls.addClassCleanup(pool_override.stop)

        # 2. Safely attempt database connection
        try:
            cls.pool = db.get_pool()
            conn = cls.pool.get_connection()
        except DatabaseError as e:
            if e.errno not in {2002, 2003}:
                raise
            raise unittest.SkipTest(
                f"Isolated test database 'signal_bot_test' is unavailable or unreachable: {e}. "
                "Please provision and configure signal_bot_test before running integration tests."
            )

        # 3. Verify database identity - FAIL immediately on mismatch, never skip
        try:
            cur = conn.cursor()
            cur.execute("SELECT DATABASE(), CURRENT_USER()")
            cls.db_name, cls.db_user = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if cls.db_name != "signal_bot_test":
            raise AssertionError(
                f"Database isolation guard failed: connected to '{cls.db_name}', "
                "strictly expected 'signal_bot_test'. Integration tests must never run against production."
            )

        # 4. Verify required tables exist - clear setup failure if missing
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'signal_bot_test'
                """
            )
            existing_tables = {row[0].lower() for row in cur.fetchall()}
            cur.close()
        finally:
            conn.close()

        required_tables = {"paper_trades", "paper_orders", "paper_positions", "paper_configs", "funding_rate"}
        missing_tables = required_tables - existing_tables
        if missing_tables:
            raise RuntimeError(
                f"Required schema tables missing in signal_bot_test: {missing_tables}. "
                "Run database schema migrations on signal_bot_test."
            )

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

    def _exercise_chat_strategy(self, enabled):
        # The worker module is already loaded. Only the separate web process saves the draft.
        hour = 3_600_000
        entry = 1795003200000
        bars = [dict(open_time=entry - n * hour, open=100, high=101, low=99, close=100,
                     atr=1) for n in range(60, 0, -1)]
        bars.extend([dict(open_time=entry, open=100, high=108, low=99, close=107, atr=4),
                     dict(open_time=entry + hour, open=107, high=108, low=102, close=103, atr=4)])
        frame = self._make_df(bars)
        frame.loc[59, "rsi"] = 25
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom_strategies.json"
            fixture = Path(tmp) / "candles.json"
            frame.to_json(fixture, orient="records")
            script = dedent("""
                import json, sys, pandas as pd
                from unittest.mock import patch
                from fastapi import FastAPI
                from fastapi.testclient import TestClient
                from web.routes import custom_strategy as web, backtest as bt
                app = FastAPI()
                app.include_router(bt.router)
                app.include_router(web.router)
                client = TestClient(app)
                web.CUSTOM_FILE = sys.argv[1]
                web.LLM_PROVIDER = "offline"
                web.OPENROUTER_API_KEY = web.ANTHROPIC_API_KEY = ""
                draft = client.post("/api/custom-strategy/generate-ai", json={"prompt": "RSI below 35, stop 2 ATR, RR 3"}).json()
                rule = web.CustomStrategyRequest(**draft["strategy"])
                rule.useTrailingStop = bool(int(sys.argv[3]))
                rule.trailActivationR, rule.trailDistanceAtrMult = 2, 2.5
                name = client.post("/api/custom-strategy/create", json=rule.model_dump()).json()["name"]
                frame = pd.read_json(sys.argv[2], orient="records", convert_dates=False)
                with patch.object(bt, "fetch_ohlcv_with_features_df", return_value=frame):
                    result = client.post("/api/backtest/run", json=dict(strategy=name, symbol="BTCUSDT",
                        market="spot", timeframe="1h", data_segment="full", save_to_db=False)).json()
                assert result["metrics"]["profit_factor"] is None
                assert len(result["trades"]) == 1
                print(json.dumps([name, result["trades"][0]]))
            """)
            expected = subprocess.check_output(
                [sys.executable, "-c", script, str(path), str(fixture), str(int(enabled))], text=True)
            name, reference = json.loads(expected)
            pe.set_paper_config("BTCUSDT", "spot", "1h", name, True, telegram_enabled=enabled)
            with (patch("backtest.custom_strategy.CUSTOM_FILE", str(path)),
                  patch.object(pe, "fetch_ohlcv_with_features_df", return_value=frame.iloc[:60]) as candles,
                  patch.object(pe, "fetch_latest_open_candle", return_value=frame.iloc[60].to_dict()) as forming,
                  patch("time.time", return_value=entry / 1000 + 5),
                  patch("notifications.telegram_notifier.send_telegram_message") as transport):
                # Leave a durable pending order, then delete the catalog definition before recovery.
                with patch.object(pe, "fill_pending_order", return_value={"status": "pending"}):
                    pe.sync_and_evaluate_paper_trading()
                path.write_text("[]")
                result = pe.sync_and_evaluate_paper_trading()
                self.assertEqual(result["new_positions_opened"], 1)
                position, = pe.get_active_positions()
                self.assertEqual(position["trail_activation_r"], 2)
                self.assertEqual(position["trail_distance_atr_mult"], 2.5)
                self.assertEqual(transport.call_count, int(enabled))
                candles.return_value = frame
                forming.return_value = None
                self.assertEqual(pe.sync_and_evaluate_paper_trading()["positions_closed"], 1)
                trade, = pe.get_paper_trades()
                self.assertEqual(trade["exit_reason"], "TRAIL" if enabled else "TP")
                self.assertEqual(trade["exit_reason"], reference["exitReason"])
                self.assertAlmostEqual(trade["exit_price"], reference["exitPrice"], delta=0.000051)
                self.assertAlmostEqual(trade["net_return_pct"], reference["netReturnPct"], delta=0.00051)
                pe.sync_and_evaluate_paper_trading()
                self.assertEqual(transport.call_count, 2 * int(enabled))

    def test_chat_strategy_trailing_and_telegram_enabled(self):
        self._exercise_chat_strategy(True)

    def test_chat_strategy_fixed_target_and_telegram_disabled(self):
        self._exercise_chat_strategy(False)

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
        self.assertAlmostEqual(float(t["exit_price"]), apply_exit_slippage(54000.0, t["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(trades[0]["exit_price"]), apply_exit_slippage(46000.0, trades[0]["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(trades[0]["exit_price"]), apply_exit_slippage(48000.0, trades[0]["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(trades[0]["exit_price"]), apply_exit_slippage(53000.0, trades[0]["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(t["exit_price"]), apply_exit_slippage(50800.0, t["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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

    def test_telegram_performance_report_reflects_committed_paper_trailing_exit(self):
        from paper.retailbot2 import TelegramAlert

        cfg_id = self._create_config(strategy_name="BB_REV_INV")
        pos_id = self._create_position(
            config_id=cfg_id, strategy_name="BB_REV_INV", direction="SHORT",
            stop_loss=52000.0, take_profit=46000.0,
        )
        with self.pool.get_connection() as conn, conn.cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
            pos = cur.fetchone()

        retail_stats = {"BB_REV_INV_inverse": {"total": 2, "wins": 1, "losses": 1, "net_pnl": 50}}
        before = TelegramAlert.format_stats(retail_stats, 10000, 10050)
        self.assertIn("No closed trades included in reporting", before)

        exit_time = pos["entry_time"] + 10 * 3_600_000
        self.assertTrue(pe.close_position_in_db(pos, 47000.0, exit_time, "TRAIL", holding_bars=10))
        with self.pool.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT net_pnl FROM paper_trades WHERE position_id=%s", (pos_id,))
            net_pnl, = cur.fetchone()
        self.assertGreater(net_pnl, 0)
        after = TelegramAlert.format_stats(retail_stats, 10000, 10050)
        self.assertIn(f"Net P&amp;L: <code>${net_pnl:+,.2f}</code> · Closed: 1", after)
        self.assertIn(f"<b>BB_REV_INV</b>: T=1 Net=${net_pnl:+,.2f}", after)
        self.assertIn("BB_REV_INV_inverse</b>: T=2 W=1 L=1 WR=50% Net=$+50", after)

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
        self.assertAlmostEqual(float(t["exit_price"]), apply_exit_slippage(84000.0, t["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(trade["exit_price"]), apply_exit_slippage(46000.0, trade["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
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
        self.assertAlmostEqual(float(trade["exit_price"]), apply_exit_slippage(51000.0, trade["direction"], BACKTEST_SLIPPAGE_PCT), places=2)

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
        self.assertAlmostEqual(float(t["exit_price"]), apply_exit_slippage(72000.0, t["direction"], BACKTEST_SLIPPAGE_PCT), places=2)

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

        # Patch once around both threads. Overlapping patches can restore the
        # real data fetch while the lock-owning thread is still evaluating.
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(pe.sync_and_evaluate_paper_trading)
                f2 = executor.submit(pe.sync_and_evaluate_paper_trading)
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

    def test_29_pairs_ratio_v1_skips_single_leg_funding(self):
        """29. pairs_ratio_v1 position on futures skips single-leg funding calculation."""
        # Insert funding rate event for the working symbol
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('BNBUSDT', 1500, 0.0010, 600.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        cfg_id = self._create_config(symbol="BNBUSDT", market="futures", strategy_name="pairs_ratio_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BNBUSDT", market="futures", strategy_name="pairs_ratio_v1",
            quantity=1.0, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        with self.assertLogs("paper.engine", level="WARNING") as cm:
            funding_cost = pe._funding_cost_for_position(pos, exit_time=2000)
            self.assertEqual(funding_cost, 0.0)
            self.assertTrue(any("unsupported_pairs_ratio_two_leg" in log for log in cm.output))

        # Close position in DB and verify funding_pnl is 0.0
        res = pe.close_position_in_db(pos, exit_price=650.0, exit_time=2000, exit_reason="TP")
        self.assertTrue(res)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT funding_pnl FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        cur.close()
        conn.close()
        self.assertAlmostEqual(float(t["funding_pnl"]), 0.0, places=4)

    def test_30_futures_supported_strategy_accrues_funding(self):
        """30. Supported single-symbol Futures strategies continue to accrue funding."""
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('BTCUSDT', 1500, 0.0005, 50000.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        cfg_id = self._create_config(symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=0.1, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        # Expected funding cost: 0.1 * 50000.0 * 0.0005 = 2.5
        funding_cost = pe._funding_cost_for_position(pos, exit_time=2000)
        self.assertAlmostEqual(funding_cost, 2.5, places=4)

    def test_31_spot_position_funding_unaffected(self):
        """31. Spot positions always have zero funding cost regardless of funding_rate records."""
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

        cfg_id = self._create_config(symbol="BTCUSDT", market="spot", strategy_name="ema_crossover")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BTCUSDT", market="spot", strategy_name="ema_crossover",
            quantity=0.1, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        funding_cost = pe._funding_cost_for_position(pos, exit_time=2000)
        self.assertEqual(funding_cost, 0.0)

    def test_32_unrelated_strategies_not_accidentally_excluded(self):
        """32. Guard strictly targets pairs_ratio_v1; unrelated strategies are not accidentally excluded."""
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('ETHUSDT', 1500, 0.0008, 3000.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        for strat in ["ema_crossover", "rsi_bb_reversal", "orderblock_v1", "breakout_volume"]:
            pos = {
                "id": 999,
                "symbol": "ETHUSDT",
                "market": "futures",
                "strategy_name": strat,
                "direction": "LONG",
                "quantity": 1.0,
                "entry_time": 1000,
            }
            # Expected funding cost: 1.0 * 3000.0 * 0.0008 = 2.4
            cost = pe._funding_cost_for_position(pos, exit_time=2000)
            self.assertAlmostEqual(cost, 2.4, places=4, msg=f"Strategy {strat} was unexpectedly excluded")

    def test_33_supported_futures_valid_empty_funding_closes_with_zero_funding(self):
        """33. Supported Futures with valid empty funding events closes with zero funding cost."""
        cfg_id = self._create_config(symbol="SOLUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="SOLUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=2.0, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        # No funding_rate events exist in DB for SOLUSDT; query returns [] cleanly
        closed = pe.close_position_in_db(pos, exit_price=150.0, exit_time=2000, exit_reason="TP")
        self.assertTrue(closed)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, funding_pnl FROM paper_positions WHERE id=%s", (pos_id,))
        p = cur.fetchone()
        self.assertEqual(p["status"], "CLOSED")
        self.assertEqual(float(p["funding_pnl"]), 0.0)

        cur.execute("SELECT funding_pnl FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        self.assertEqual(float(t["funding_pnl"]), 0.0)
        cur.close()
        conn.close()

    def test_34_supported_futures_funding_exception_does_not_persist_trade(self):
        """34. Supported Futures with funding query exception raises PaperFundingRetrievalError and preserves position OPEN."""
        cfg_id = self._create_config(symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=0.1, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        with patch("db.db.fetch_settled_funding_events", side_effect=RuntimeError("Simulated funding query timeout")):
            with self.assertRaises(pe.PaperFundingRetrievalError) as cm:
                pe.close_position_in_db(pos, exit_price=54000.0, exit_time=2000, exit_reason="TP")
            self.assertIn("Simulated funding query timeout", str(cm.exception))

        # Position must remain strictly OPEN in MariaDB with NO trade record in paper_trades
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, funding_pnl FROM paper_positions WHERE id=%s", (pos_id,))
        p = cur.fetchone()
        self.assertEqual(p["status"], "OPEN")
        self.assertEqual(float(p["funding_pnl"]), 0.0)

        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 0)
        cur.close()
        conn.close()

    def test_35_position_remains_open_and_retries_successfully(self):
        """35. Position preserved as OPEN after funding query failure retries and closes exactly once."""
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('ADAUSDT', 1500, 0.0010, 1.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        cfg_id = self._create_config(symbol="ADAUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="ADAUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=1000.0, entry_time=1000, last_update_time=1000
        )

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_positions WHERE id=%s", (pos_id,))
        pos = cur.fetchone()
        cur.close()
        conn.close()

        # Attempt 1: funding retrieval failure
        with patch("db.db.fetch_settled_funding_events", side_effect=RuntimeError("Transient MariaDB connection loss")):
            with self.assertRaises(pe.PaperFundingRetrievalError):
                pe.close_position_in_db(pos, exit_price=1.2, exit_time=2000, exit_reason="TP")

        # Verify state is still pristine OPEN
        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 0)
        cur.close()
        conn.close()

        # Attempt 2: retry succeeds with authentic funding events from real DB
        # Expected funding cost: 1000.0 * 1.0 * 0.0010 = 1.0 -> funding_pnl = -1.0
        success = pe.close_position_in_db(pos, exit_price=1.2, exit_time=2000, exit_reason="TP")
        self.assertTrue(success)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, funding_pnl FROM paper_positions WHERE id=%s", (pos_id,))
        p = cur.fetchone()
        self.assertEqual(p["status"], "CLOSED")
        self.assertAlmostEqual(float(p["funding_pnl"]), -1.0, places=4)

        cur.execute("SELECT count(*) as cnt, funding_pnl FROM paper_trades WHERE position_id=%s", (pos_id,))
        t = cur.fetchone()
        self.assertEqual(t["cnt"], 1)
        self.assertAlmostEqual(float(t["funding_pnl"]), -1.0, places=4)
        cur.close()
        conn.close()

        # Attempt 3: duplicate closure call is strictly idempotent (CAS guard)
        second_call = pe.close_position_in_db(pos, exit_price=1.2, exit_time=2000, exit_reason="TP")
        self.assertFalse(second_call)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 1)
        cur.close()
        conn.close()

    def test_36_paper_engine_cycle_continues_running_on_funding_error(self):
        """36. Engine cycle catches funding error per-position, preserves open state, and continues evaluating other positions."""
        cfg_futures = self._create_config(symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_futures = self._create_position(
            config_id=cfg_futures, symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1",
            entry_price=50000.0, stop_loss=48000.0, take_profit=54000.0, last_update_time=1000
        )

        cfg_spot = self._create_config(symbol="ETHUSDT", market="spot", strategy_name="ema_crossover")
        pos_spot = self._create_position(
            config_id=cfg_spot, symbol="ETHUSDT", market="spot", strategy_name="ema_crossover",
            entry_price=3000.0, stop_loss=2800.0, take_profit=3400.0, last_update_time=1000
        )

        df_btc = self._make_df([
            {"open_time": 2000, "open": 51000.0, "high": 54500.0, "low": 50800.0, "close": 54200.0}
        ])
        df_eth = self._make_df([
            {"open_time": 2000, "open": 3100.0, "high": 3450.0, "low": 3050.0, "close": 3420.0}
        ])

        def fake_fetch_df(sym, mkt, tf, **kwargs):
            return df_btc if sym == "BTCUSDT" else df_eth

        def fake_fetch_events(symbol, start_time, end_time, **kwargs):
            if symbol == "BTCUSDT":
                raise RuntimeError("Simulated MariaDB funding_rate table lock error")
            return []

        with patch("paper.engine.fetch_ohlcv_with_features_df", side_effect=fake_fetch_df), \
             patch("db.db.fetch_settled_funding_events", side_effect=fake_fetch_events):
            res = pe.sync_and_evaluate_paper_trading()

        # Engine completed the cycle without crashing! Spot position closed; Futures position kept OPEN
        self.assertEqual(res["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        # BTC futures position preserved as OPEN and last_update_time was NOT advanced
        cur.execute("SELECT status, last_update_time FROM paper_positions WHERE id=%s", (pos_futures,))
        p_btc = cur.fetchone()
        self.assertEqual(p_btc["status"], "OPEN")
        self.assertEqual(p_btc["last_update_time"], 1000)

        # ETH spot position closed normally
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_spot,))
        p_eth = cur.fetchone()
        self.assertEqual(p_eth["status"], "CLOSED")
        cur.close()
        conn.close()

    def test_37_exit_event_preserved_across_cycle_retry(self):
        """37. Intended exit candle is preserved without inventing prices/timestamps and evaluated on cycle retry."""
        conn = self.pool.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO funding_rate (symbol, event_time, funding_rate, mark_price)
            VALUES ('BTCUSDT', 1500, 0.0005, 52000.0)
            """
        )
        conn.commit()
        cur.close()
        conn.close()

        cfg_id = self._create_config(symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=0.1, entry_time=1000, entry_price=50000.0, stop_loss=48000.0, take_profit=54000.0, last_update_time=1000
        )

        # Two bars: bar 1 (no exit), bar 2 (hits TP at 54000)
        df = self._make_df([
            {"open_time": 2000, "open": 50500.0, "high": 51500.0, "low": 50200.0, "close": 51000.0},
            {"open_time": 3000, "open": 51200.0, "high": 54500.0, "low": 51000.0, "close": 54200.0},
        ])

        # Cycle 1: funding retrieval fails
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df), \
             patch("db.db.fetch_settled_funding_events", side_effect=RuntimeError("Simulated outage")):
            res1 = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res1["positions_closed"], 0)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, last_update_time FROM paper_positions WHERE id=%s", (pos_id,))
        p1 = cur.fetchone()
        self.assertEqual(p1["status"], "OPEN")
        # last_update_time MUST NOT have advanced past the exit candle (remains 1000)
        self.assertEqual(p1["last_update_time"], 1000)
        cur.close()
        conn.close()

        # Cycle 2: funding retrieval restored (uses authentic event from MariaDB)
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df):
            res2 = pe.sync_and_evaluate_paper_trading()

        self.assertEqual(res2["positions_closed"], 1)

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, current_price, funding_pnl FROM paper_positions WHERE id=%s", (pos_id,))
        p2 = cur.fetchone()
        self.assertEqual(p2["status"], "CLOSED")
        self.assertEqual(float(p2["current_price"]), 54000.0)

        cur.execute("SELECT * FROM paper_trades WHERE position_id=%s", (pos_id,))
        trades = cur.fetchall()
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["exit_reason"], "TP")
        self.assertAlmostEqual(float(t["exit_price"]), apply_exit_slippage(54000.0, t["direction"], BACKTEST_SLIPPAGE_PCT), places=6)
        self.assertEqual(t["exit_time"], 3000)
        # Expected funding cost: 0.1 * 52000.0 * 0.0005 = 2.6 -> funding_pnl = -2.6
        self.assertAlmostEqual(float(t["funding_pnl"]), -2.6, places=4)
        cur.close()
        conn.close()

    def test_38_manual_close_raises_on_funding_error_preserving_open_state(self):
        """38. manual_close_position surfaces funding error and preserves position as OPEN."""
        cfg_id = self._create_config(symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1")
        pos_id = self._create_position(
            config_id=cfg_id, symbol="BTCUSDT", market="futures", strategy_name="trend_alignment_v1",
            quantity=0.1, entry_time=1000, last_update_time=1000
        )

        with patch("db.db.fetch_settled_funding_events", side_effect=RuntimeError("Simulated dedicated DB failure")):
            with self.assertRaises(RuntimeError) as cm:
                pe.manual_close_position(pos_id)
            self.assertIn("funding retrieval failure", str(cm.exception))

        conn = self.pool.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status FROM paper_positions WHERE id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["status"], "OPEN")
        cur.execute("SELECT count(*) as cnt FROM paper_trades WHERE position_id=%s", (pos_id,))
        self.assertEqual(cur.fetchone()["cnt"], 0)
        cur.close()
        conn.close()


if __name__ == "__main__":
    unittest.main()
