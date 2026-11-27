"""Pure TP/SL mode: reporting, backtest exit modes, paper lifecycle.

(a) get_paper_metrics pure aggregates + exitReasonBreakdown (mocked pool,
    no database needed).
(b) Backtest pure/dynamic/both via simulate() directly and via the
    POST /api/backtest/run handler (mocked market data, no database).
(c) Paper engine pure lifecycle: _insert_position stores the pure policy
    (mocked pool) and, when a database is available, a full sync cycle
    never trails or times out a pure position (DB-guarded, skipped
    without signal_bot_test).
"""
import json
import os
import unittest
from unittest.mock import patch

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.simulate import simulate
import paper.engine as pe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sim_params(**overrides):
    params = {
        "BACKTEST_FEE_PCT": 0.001,
        "BACKTEST_SLIPPAGE_PCT": 0.0005,
        "BACKTEST_MAX_HOLD_BARS": 200,
        "USE_TRAILING_STOP": True,
        "TRAIL_ACTIVATION_R": 1.0,
        "TRAIL_DISTANCE_ATR_MULT": 1.5,
        "market": "spot",
        "symbol": "BTCUSDT",
    }
    params.update(overrides)
    return params


def _trail_vs_sl_frame():
    """Signal bar 1, entry bar 2 at 100, SL 98, TP 104 (RR 2).

    Bar 3 rallies to 103.5 (+1.5R, trailing would arm in dynamic mode);
    bar 4 opens 102, dips to 96: dynamic exits TRAIL at the ratcheted
    stop, pure exits SL at 98.
    """
    rows = [
        (0, 100, 101, 99, 100),
        (1, 100, 101, 99, 100),
        (2, 100, 101, 99, 100),
        (3, 101, 103.5, 100.5, 103),
        (4, 102, 102.5, 96, 97),
    ]
    # Pad to 60 bars (the /api/backtest/run route requires >= 50 candles).
    # No further signals fire after bar 1, so the padding cannot add trades.
    rows += [(i, 97, 97.5, 96.5, 97) for i in range(5, 60)]
    return pd.DataFrame([
        {"open_time": 1_700_000_000_000 + i * 3_600_000, "open": o, "high": h,
         "low": lo, "close": c, "volume": 10.0, "atr": 1.0}
        for i, (__, o, h, lo, c) in enumerate(rows)
    ])


def _flat_frame(bars=300, price=100.0):
    return pd.DataFrame([
        {"open_time": 1_700_000_000_000 + i * 3_600_000, "open": price,
         "high": price + 0.5, "low": price - 0.5, "close": price,
         "volume": 10.0, "atr": 1.0}
        for i in range(bars)
    ])


def _long_once(row, prev_row, params):
    return int(row["open_time"]) == 1_700_000_000_000 + 3_600_000


def _never_short(row, prev_row, params):
    return False


def _stop_target(direction, entry_price, atr, row, params):
    if direction == "LONG":
        return entry_price - 2.0, entry_price + 4.0
    return entry_price + 2.0, entry_price - 4.0


# ---------------------------------------------------------------------------
# (a) get_paper_metrics — mocked pool
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, summary, strategy_rows, breakdown_rows):
        self.summary = summary
        self.strategy_rows = strategy_rows
        self.breakdown_rows = breakdown_rows
        self.queries = []
        self.params = []

    def execute(self, query, params=None):
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self):
        return self.summary

    def fetchall(self):
        last = self.queries[-1]
        if "GROUP BY t.exit_reason" in last:
            return self.breakdown_rows
        return self.strategy_rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, dictionary=False):
        return self._cursor

    def close(self):
        pass


class _FakePool:
    def __init__(self, cursor):
        self._cursor = cursor

    def get_connection(self):
        return _FakeConn(self._cursor)


def _metrics_fixture():
    summary = {
        "total_trades": 5, "wins": 3, "losses": 2, "sum_r": 1.0, "avg_r": 0.2,
        "net_pnl": 100.0,
        "pure_trades": 2, "pure_wins": 1, "pure_losses": 1, "pure_sum_r": 0.8,
        "pure_net_pnl": 40.0, "pure_win_sum_r": 2.0, "pure_loss_sum_r": -1.2,
    }
    strategy_rows = [{
        "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
        "strategy_name": "trend_ema_v1", "config_id": 10,
        "ml_generator_model_id": None, "ml_model_id": None,
        "total_trades": 5, "wins": 3, "losses": 2, "sum_r": 1.0, "avg_r": 0.2,
        "net_pnl": 100.0,
        "pure_trades": 2, "pure_wins": 1, "pure_losses": 1, "pure_sum_r": 0.8,
        "pure_net_pnl": 40.0, "pure_win_sum_r": 2.0, "pure_loss_sum_r": -1.2,
    }]
    breakdown_rows = [
        {"exit_reason": "TP", "trades": 1, "wins": 1, "losses": 0, "net_pnl": 60.0, "sum_r": 2.0},
        {"exit_reason": "SL", "trades": 1, "wins": 0, "losses": 1, "net_pnl": -20.0, "sum_r": -1.2},
        {"exit_reason": "TRAIL", "trades": 2, "wins": 2, "losses": 0, "net_pnl": 50.0, "sum_r": 1.5},
        {"exit_reason": "TIMEOUT", "trades": 1, "wins": 0, "losses": 1, "net_pnl": -10.0, "sum_r": -0.5},
        {"exit_reason": "MANUAL", "trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0, "sum_r": 0.0},
    ]
    return summary, strategy_rows, breakdown_rows


class TestPaperMetricsPure(unittest.TestCase):
    def _run_metrics(self, closed_since_ms=None):
        summary, strategy_rows, breakdown_rows = _metrics_fixture()
        cursor = _FakeCursor(summary, strategy_rows, breakdown_rows)
        with patch("paper.engine.get_pool", return_value=_FakePool(cursor)):
            metrics = pe.get_paper_metrics(closed_since_ms=closed_since_ms)
        return metrics, cursor

    def test_pure_aggregates_count_only_tp_sl_and_totals_unchanged(self):
        metrics, cursor = self._run_metrics()
        # Existing totals are unchanged (TRAIL wins still count there).
        self.assertEqual(metrics["totalTrades"], 5)
        self.assertEqual(metrics["wins"], 3)
        self.assertEqual(metrics["losses"], 2)
        self.assertEqual(metrics["netPnl"], 100.0)
        # Pure aggregates filter to TP/SL only.
        self.assertEqual(metrics["pureTrades"], 2)
        self.assertEqual(metrics["pureWins"], 1)
        self.assertEqual(metrics["pureLosses"], 1)
        self.assertEqual(metrics["pureWinRatePct"], 50.0)
        self.assertEqual(metrics["pureNetPnl"], 40.0)
        self.assertEqual(metrics["pureSumR"], 0.8)
        self.assertEqual(metrics["pureCumulativeR"], 0.8)
        self.assertEqual(metrics["pureExpectancyR"], 0.4)
        self.assertEqual(metrics["avgWinR"], 2.0)
        self.assertEqual(metrics["avgLossR"], -1.2)
        # Per-strategy rows carry the same pure fields.
        row = metrics["byStrategy"][0]
        self.assertEqual(row["pureTrades"], 2)
        self.assertEqual(row["pureWinRatePct"], 50.0)
        self.assertEqual(row["pureNetPnl"], 40.0)
        symbol_row = metrics["byStrategySymbol"][0]
        self.assertEqual(symbol_row["pureTrades"], 2)
        self.assertEqual(symbol_row["pureWinRatePct"], 50.0)
        # Breakdown lists every canonical reason, including zero rows.
        breakdown = {r["exitReason"]: r for r in metrics["exitReasonBreakdown"]}
        self.assertEqual(breakdown["TP"]["trades"], 1)
        self.assertEqual(breakdown["SL"]["trades"], 1)
        self.assertEqual(breakdown["TRAIL"]["trades"], 2)
        self.assertEqual(breakdown["TIMEOUT"]["trades"], 1)
        self.assertEqual(breakdown["MANUAL"]["trades"], 0)
        self.assertEqual(breakdown["TRAIL"]["sumR"], 1.5)

    def test_closed_since_filter_applies_to_pure_and_breakdown_queries(self):
        metrics, cursor = self._run_metrics(closed_since_ms=12345)
        self.assertEqual(len(cursor.queries), 3)
        for query, params in zip(cursor.queries, cursor.params):
            self.assertIn("t.exit_time >= %s", query)
            self.assertEqual(params, (12345,))
        self.assertEqual(metrics["pureTrades"], 2)


# ---------------------------------------------------------------------------
# (b) Backtest metrics + simulate pure/dynamic
# ---------------------------------------------------------------------------

class TestBacktestPureMetrics(unittest.TestCase):
    def test_compute_metrics_pure_excludes_trail_timeout_manual(self):
        trades = [
            {"r_multiple": 2.0, "net_pnl": 20.0, "exit_reason": "TP"},
            {"r_multiple": -1.0, "net_pnl": -10.0, "exit_reason": "SL"},
            {"r_multiple": 0.5, "net_pnl": 5.0, "exit_reason": "TRAIL"},
            {"r_multiple": -0.2, "net_pnl": -2.0, "exit_reason": "TIMEOUT"},
            {"r_multiple": 0.3, "net_pnl": 3.0, "exit_reason": "MANUAL"},
            {"r_multiple": 0.1, "net_pnl": 1.0, "exit_reason": "END_OF_DATA"},
        ]
        m = compute_metrics(trades)
        self.assertEqual(m["total_trades"], 6)
        self.assertEqual(m["pure_trades"], 2)
        self.assertEqual(m["pure_wins"], 1)
        self.assertEqual(m["pure_losses"], 1)
        self.assertEqual(m["pure_win_rate_pct"], 50.0)
        self.assertEqual(m["pure_net_pnl"], 10.0)
        self.assertEqual(m["pure_sum_r"], 1.0)
        self.assertEqual(m["pure_expectancy_r"], 0.5)
        self.assertEqual(m["pure_avg_win_r"], 2.0)
        self.assertEqual(m["pure_avg_loss_r"], -1.0)
        breakdown = {r["exit_reason"]: r for r in m["exit_reason_breakdown"]}
        self.assertEqual(breakdown["TRAIL"]["trades"], 1)
        self.assertEqual(breakdown["END_OF_DATA"]["trades"], 1)
        self.assertEqual(breakdown["MANUAL"]["trades"], 1)

    def test_compute_metrics_empty_keeps_pure_keys(self):
        m = compute_metrics([])
        self.assertEqual(m["pure_trades"], 0)
        self.assertIsNone(m["pure_win_rate_pct"])
        self.assertEqual(m["exit_reason_breakdown"][0]["exit_reason"], "TP")

    def test_simulate_dynamic_trails_but_pure_exits_sl(self):
        frame = _trail_vs_sl_frame()
        dynamic = simulate(frame, _sim_params(), _long_once, _never_short, _stop_target)
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0]["exit_reason"], "TRAIL")
        self.assertNotIn("custom_exit_policy", dynamic[0])

        pure = simulate(frame, _sim_params(_pure_tp_sl=True), _long_once, _never_short, _stop_target)
        self.assertEqual(len(pure), 1)
        self.assertEqual(pure[0]["exit_reason"], "SL")
        policy = pure[0]["custom_exit_policy"]
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["activation_r"], 9999.0)
        self.assertEqual(policy["max_hold_bars"], 0)

    def test_simulate_pure_never_times_out(self):
        frame = _flat_frame(300)
        dynamic = simulate(frame, _sim_params(), _long_once, _never_short, _stop_target)
        self.assertEqual([t["exit_reason"] for t in dynamic], ["TIMEOUT"])
        pure = simulate(frame, _sim_params(_pure_tp_sl=True), _long_once, _never_short, _stop_target)
        self.assertEqual([t["exit_reason"] for t in pure], ["END_OF_DATA"])
        self.assertEqual(pure[0]["holding_bars"], 298)

    def test_simulate_pure_flag_via_pure_tp_sl_param(self):
        frame = _trail_vs_sl_frame()
        pure = simulate(frame, _sim_params(PURE_TP_SL=True), _long_once, _never_short, _stop_target)
        self.assertEqual(pure[0]["exit_reason"], "SL")


class TestBacktestRouteExitMode(unittest.TestCase):
    def _run(self, exit_mode="dynamic", **kwargs):
        from web.routes import backtest
        req_kwargs = {"symbol": "BTCUSDT", "strategy": "test_pure_strategy",
                      "save_to_db": False,
                      # Dynamic mode only trails when the caller enables it,
                      # exactly like the pre-existing default behaviour.
                      "params": {"USE_TRAILING_STOP": True}}
        req_kwargs.update(kwargs)
        if exit_mode is not None:
            req_kwargs["exit_mode"] = exit_mode
        request = backtest.BacktestRunRequest(**req_kwargs)
        fake_strategy = lambda frame, params: simulate(
            frame, params, _long_once, _never_short, _stop_target)
        with patch.dict(backtest.STRATEGIES, {"test_pure_strategy": fake_strategy}), \
             patch.object(backtest, "fetch_ohlcv_with_features_df",
                          return_value=_trail_vs_sl_frame()):
            return backtest.execute_backtest(request)

    def test_default_exit_mode_is_dynamic(self):
        result = self._run(exit_mode=None)
        self.assertEqual(result["exitMode"], "dynamic")
        self.assertEqual(result["trades"][0]["exitReason"], "TRAIL")
        self.assertNotIn("results", result)

    def test_pure_run_has_no_trail_or_timeout_exits(self):
        result = self._run(exit_mode="pure")
        self.assertEqual(result["exitMode"], "pure")
        self.assertEqual(result["trades"][0]["exitReason"], "SL")
        self.assertEqual(result["metrics"]["pure_trades"], 1)

    def test_both_returns_both_result_sets(self):
        result = self._run(exit_mode="both")
        self.assertEqual(result["exitMode"], "both")
        self.assertIn("results", result)
        self.assertEqual(result["results"]["dynamic"]["trades"][0]["exitReason"], "TRAIL")
        self.assertEqual(result["results"]["pure"]["trades"][0]["exitReason"], "SL")
        self.assertEqual(result["results"]["pure"]["metrics"]["pure_trades"], 1)
        self.assertEqual(result["results"]["dynamic"]["metrics"]["pure_trades"], 0)
        # Top level stays dynamic for old clients.
        self.assertEqual(result["trades"][0]["exitReason"], "TRAIL")

    def test_invalid_exit_mode_is_400(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            self._run(exit_mode="sideways")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("exit_mode", raised.exception.detail)


# ---------------------------------------------------------------------------
# (c) Paper engine: _insert_position pure policy (mocked pool, no DB)
# ---------------------------------------------------------------------------

class _InsertCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    @property
    def lastrowid(self):
        return 7

    def close(self):
        pass


class _InsertConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestInsertPositionPure(unittest.TestCase):
    def _insert(self, cfg_params, trade_policy=None):
        cursor = _InsertCursor()
        conn = _InsertConn(cursor)
        pool = type("Pool", (), {"get_connection": lambda self: conn})()
        cfg = {"id": 1, "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
               "strategy_name": "trend_ema_v1", "allocated_capital": 5000.0,
               "risk_per_trade_pct": 1.0, "strategy_params": cfg_params}
        trade = {"direction": "LONG", "entry_price": 50000.0, "stop_loss": 48000.0,
                 "take_profit": 54000.0, "atr_at_signal": 500.0}
        if trade_policy is not None:
            trade["custom_exit_policy"] = trade_policy
        with patch("paper.engine.get_pool", return_value=pool), \
             patch("paper.circuit_breaker.check_exposure_limit", return_value=(True, "")):
            position_id = pe._insert_position(cfg, trade, 50000.0, 1760000000000)
        self.assertEqual(position_id, 7)
        sql, params = cursor.executed[0]
        return params

    def test_pure_config_forces_disabled_policy_and_no_timeout(self):
        params = self._insert({"PURE_TP_SL": True})
        # INSERT values order: symbol, market, timeframe, strategy,
        # direction, entry_time, entry_price, current_price, stop_loss,
        # take_profit, current_stop, atr, stop_distance, max_hold_bars,
        # ..., custom_exit_policy, trail_activation_r, trail_distance_atr_mult
        max_hold = params[13]
        policy = json.loads(params[-3])
        trail_act = params[-2]
        self.assertEqual(max_hold, 0)
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["max_hold_bars"], 0)
        self.assertEqual(trail_act, 9999.0)

    def test_pure_config_overrides_trailing_policy(self):
        params = self._insert({"PURE_TP_SL": True}, trade_policy={
            "enabled": True, "activation_r": 1.0, "distance_atr": 1.5,
            "max_hold_bars": 200, "entry_open": 50000.0, "atr": 500.0})
        self.assertEqual(params[13], 0)
        self.assertFalse(json.loads(params[-3])["enabled"])
        self.assertEqual(params[-2], 9999.0)

    def test_dynamic_config_keeps_default_timeout_and_trailing(self):
        params = self._insert({})
        self.assertEqual(params[13], 200)
        self.assertEqual(params[-2], 1.0)
        self.assertIsNone(params[-3])

    def test_max_hold_zero_accepted_but_negative_rejected(self):
        with self.assertRaises(ValueError):
            self._insert({}, trade_policy={
                "enabled": True, "activation_r": 1.0, "distance_atr": 1.5,
                "max_hold_bars": -1, "entry_open": 50000.0, "atr": 500.0})


class TestPaperExitModeLabel(unittest.TestCase):
    def test_label_from_order_policy(self):
        pure_order = {"custom_exit_policy": {"enabled": False, "max_hold_bars": 0}}
        self.assertEqual(pe._paper_exit_mode_label(pure_order), "Pure TP/SL")
        dynamic_order = {"custom_exit_policy": {"enabled": True, "max_hold_bars": 200}}
        self.assertEqual(pe._paper_exit_mode_label(dynamic_order), "Dynamic")
        self.assertEqual(pe._paper_exit_mode_label({}), "Dynamic")
        self.assertEqual(pe._paper_exit_mode_label(pure_order, exit_mode="dynamic"), "Dynamic")


# ---------------------------------------------------------------------------
# (c) Full pure lifecycle against signal_bot_test (skipped without a DB)
# ---------------------------------------------------------------------------

class TestPurePaperLifecycleDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("MYSQL_DATABASE") != "signal_bot_test":
            raise unittest.SkipTest("Set MYSQL_DATABASE=signal_bot_test to run against an isolated test database.")
        import db.db as db
        from mysql.connector import Error as DatabaseError
        settings = patch.dict(db.MYSQL_CONFIG, host="127.0.0.1",
                              port=int(os.getenv("MYSQL_PORT", "3306")),
                              database="signal_bot_test")
        settings.start()
        cls.addClassCleanup(settings.stop)
        pool_override = patch.object(db, "_pool", None)
        pool_override.start()
        cls.addClassCleanup(pool_override.stop)
        try:
            cls.pool = db.get_pool()
            conn = cls.pool.get_connection()
        except DatabaseError as e:
            if e.errno not in {2002, 2003}:
                raise
            raise unittest.SkipTest(f"Isolated test database unavailable: {e}")
        try:
            cur = conn.cursor()
            cur.execute("SELECT DATABASE()")
            if cur.fetchone()[0] != "signal_bot_test":
                raise AssertionError("Database isolation guard failed")
            cur.close()
        finally:
            conn.close()

    def setUp(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            for table in ("paper_trades", "paper_orders", "paper_positions",
                          "paper_configs", "funding_rate"):
                cur.execute(f"DELETE FROM {table}")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _make_df(self, bars):
        rows = []
        for b in bars:
            rows.append({
                "open_time": float(b["open_time"]), "open": float(b["open"]),
                "high": float(b["high"]), "low": float(b["low"]),
                "close": float(b["close"]), "volume": 100.0, "is_closed": 1,
                "rsi": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_hist": 0.0,
                "atr": float(b.get("atr", 500.0)), "atr_pct": 1.0,
                "volume_sma": 100.0, "volume_ratio": 1.0, "support": 45000.0,
                "resistance": 55000.0, "swing_high": 55000.0, "swing_low": 45000.0,
                "fib_0": 45000.0, "fib_236": 47000.0, "fib_382": 49000.0,
                "fib_5": 50000.0, "fib_618": 51000.0, "fib_786": 53000.0,
                "fib_1": 55000.0,
            })
        return pd.DataFrame(rows)

    def test_pure_position_never_trails_or_times_out_then_hits_tp(self):
        """test_22 template, pure mode: +1.1R bar must not arm trailing,
        250 flat bars must not time out (max_hold 200), TP bar closes TP."""
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO paper_configs (symbol, market, timeframe, strategy_name,
                       is_active, allocated_capital, risk_per_trade_pct, strategy_params)
                   VALUES ('BTCUSDT', 'spot', '1h', 'trend_ema_v1', 1, 5000.0, 1.0, %s)""",
                (json.dumps({"PURE_TP_SL": True}),))
            conn.commit()
            cfg_id = cur.lastrowid
            cur.close()
        finally:
            conn.close()
        cfg = next(c for c in pe.get_paper_configs() if c["id"] == cfg_id)
        self.assertTrue(cfg["strategy_params"]["PURE_TP_SL"])

        trade = {"direction": "LONG", "entry_price": 50000.0, "stop_loss": 48000.0,
                 "take_profit": 54000.0, "atr_at_signal": 500.0,
                 "custom_exit_policy": {"enabled": False, "activation_r": 9999.0,
                                        "distance_atr": 1.5, "max_hold_bars": 0,
                                        "entry_open": 50000.0, "atr": 500.0}}
        with patch("paper.circuit_breaker.check_exposure_limit", return_value=(True, "")):
            pos_id = pe._insert_position(cfg, trade, 50000.0, 1760000000000)
        self.assertIsNotNone(pos_id)

        def _bars(specs):
            return self._make_df([{"open_time": t, "open": o, "high": h,
                                   "low": lo, "close": c, "atr": 500.0}
                                  for t, o, h, lo, c in specs])

        # Bar 1: +1.1R (close 52200) — dynamic would arm trailing here.
        df1 = _bars([(1760003600000, 50500, 52500, 50200, 52200)])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df1), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": lambda df, p: []}):
            res = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res["positions_closed"], 0)
        pos = pe.get_active_positions()[0]
        self.assertFalse(pos["trailing_active"])
        self.assertEqual(pos["trail_activation_r"], 9999.0)
        self.assertEqual(pos["max_hold_bars"], 0)

        # 250 flat bars at 51000 — past the 200-bar dynamic timeout.
        flat = [(1760003600000 + i * 3600000, 51000, 51200, 50800, 51000)
                for i in range(1, 251)]
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=_bars(flat)), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": lambda df, p: []}):
            res = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res["positions_closed"], 0)
        pos = pe.get_active_positions()[0]
        self.assertGreater(pos["holding_bars"], 200)

        # TP bar.
        df_tp = _bars([(1760003600000 + i * 3600000, 51000, 51200, 50800, 51000)
                       for i in range(1, 251)] +
                      [(1760003600000 + 251 * 3600000, 53000, 54500, 52900, 54200)])
        with patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df_tp), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": lambda df, p: []}):
            res = pe.sync_and_evaluate_paper_trading()
        self.assertEqual(res["positions_closed"], 1)
        trade_row, = pe.get_paper_trades()
        self.assertEqual(trade_row["exit_reason"], "TP")
        metrics = pe.get_paper_metrics()
        self.assertEqual(metrics["pureTrades"], 1)
        self.assertEqual(metrics["pureWins"], 1)


if __name__ == "__main__":
    unittest.main()
