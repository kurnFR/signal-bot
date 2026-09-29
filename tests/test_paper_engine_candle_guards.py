"""
Regression tests for three bugs fixed in paper/engine.py:

1. _insert_position() wrote size.risk_amount (a dollar amount) into the
   last_update_time column (a candle timestamp) due to a positional
   mismatch in the INSERT values tuple. Harmless on its own, but it
   defeated the guard added in (2), which depends on that column.

2. sync_and_evaluate_paper_trading()'s exit-management branch had no
   per-candle idempotency guard. It's meant to be driven by a continuous
   polling loop (run_paper_engine.py, ~60s), not just manual button
   clicks -- so without this guard, the same latest closed candle gets
   processed on every poll. For a 1h strategy that's ~60 "bars" applied
   per real hour: holding_bars races ~60x too fast, so max_hold_bars
   time-stops trigger in ~1 real hour instead of ~max_hold_bars hours.

3. No guard against re-opening a position immediately after it closes,
   while its entry candle is still the latest closed bar. The backtest
   evaluates SL/TP starting at the entry bar itself; if that bar's range
   also triggers a close, a continuous poller sees the same still-fresh
   entry signal on its very next cycle and reopens -- open/stop/reopen,
   repeatedly, for the whole candle.

These are exercised against sync_and_evaluate_paper_trading() and
_insert_position() directly, with all DB access mocked -- no live
database required.
"""
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

import paper.engine as pe

CANDLE_A = 1_758_297_600_000  # some arbitrary aligned open_time, ms
CANDLE_B = CANDLE_A + 3_600_000  # one hour later


def _ohlcv_df(close, high, low, atr, open_time):
    """A minimal OHLCV+indicators frame shaped like
    fetch_ohlcv_with_features_df()'s real output. sync_and_evaluate_paper_trading()
    requires at least 50 rows (a warmup/indicator-stability floor), so this
    pads with earlier placeholder bars -- only the LAST row (the "latest
    closed candle") is what the exit/entry logic actually reads."""
    rows = [{
        "open_time": open_time - (50 - i) * 3_600_000,
        "close": close, "high": high, "low": low, "atr": atr,
    } for i in range(49)]
    rows.append({"open_time": open_time, "close": close, "high": high, "low": low, "atr": atr})
    return pd.DataFrame(rows)


def _make_cfg(**overrides):
    cfg = {
        "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
        "strategy_name": "trend_ema_v1", "is_active": 1,
        "allocated_capital": 5000.0, "risk_per_trade_pct": 1.0,
        "ml_model_id": None,
    }
    cfg.update(overrides)
    return cfg


def _make_open_position(last_update_time, **overrides):
    pos = {
        "id": 101, "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
        "strategy_name": "trend_ema_v1", "direction": "LONG",
        "entry_price": 60000.0, "initial_risk": 500.0, "current_stop": 59500.0,
        "take_profit": 65000.0, "trailing_active": 0, "trail_activation_r": 1.0,
        "trail_distance_atr_mult": 1.5, "holding_bars": 0, "max_hold_bars": 100,
        "last_update_time": last_update_time,
    }
    pos.update(overrides)
    return pos


class _FakeCursor:
    """Records every UPDATE issued against paper_positions and mutates a
    shared position dict in place, so a second sync call in the same test
    sees the persisted result of the first -- a faithful single-process
    stand-in for a real DB round trip."""
    def __init__(self, shared_position, log):
        self._pos = shared_position
        self._log = log

    def execute(self, sql, params=None):
        self._log.append((sql.strip().split("\n", 1)[0], params))
        if "UPDATE paper_positions" in sql and "current_price" in sql:
            (current_price, current_stop, trailing_active, unrealized_pct,
             unrealized_r, holding_bars, last_update_time, pos_id) = params
            if self._pos is not None and self._pos["id"] == pos_id:
                self._pos["current_stop"] = current_stop
                self._pos["trailing_active"] = trailing_active
                self._pos["holding_bars"] = holding_bars
                self._pos["last_update_time"] = last_update_time

    def fetchone(self):
        return None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, shared_position, log):
        self._pos, self._log = shared_position, log

    def cursor(self, dictionary=False):
        return _FakeCursor(self._pos, self._log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakePool:
    def __init__(self, shared_position, log):
        self._pos, self._log = shared_position, log

    def get_connection(self):
        return _FakeConn(self._pos, self._log)


class TestInsertPositionLastUpdateTime(unittest.TestCase):
    """Bug 1: last_update_time was silently receiving risk_amount."""

    def test_last_update_time_is_one_ms_before_entry_candle_not_risk_amount(self):
        captured = {}

        class CapturingCursor(_FakeCursor):
            lastrowid = 999
            def execute(self, sql, params=None):
                if "INSERT INTO paper_positions" in sql:
                    captured["sql"], captured["params"] = sql, params
                super().execute(sql, params) if "UPDATE" in sql else None

        class CapturingConn(_FakeConn):
            def cursor(self, dictionary=False):
                return CapturingCursor(None, [])

        class CapturingPool:
            def get_connection(self):
                return CapturingConn(None, [])

        cfg = _make_cfg()
        trade = {
            "direction": "LONG", "entry_price": 64000.0, "stop_loss": 63000.0,
            "take_profit": 66000.0, "atr_at_signal": 500.0,
        }
        with patch("paper.engine.get_pool", return_value=CapturingPool()):
            pe._insert_position(cfg, trade, 64010.0, CANDLE_A)

        # The VALUES tuple mixes literal SQL values (trailing_active=0,
        # trail_activation_r=1.0, ...) with %s placeholders, so the column
        # name list and the params tuple are NOT 1:1 -- parsing the SQL
        # text to find the index is fragile. Instead assert against the
        # fixed positional index in the params tuple as currently written
        # in paper/engine.py's _insert_position (see the VALUES tuple
        # there): last_update_time is params[13].
        self.assertEqual(captured["params"][13], CANDLE_A - 1)
        # And explicitly NOT the old (buggy) value -- a dollar risk amount
        # (1% of $5000 / (64000-63000) risk-based sizing = 1000.0 units'
        # worth of risk), nowhere near a millisecond timestamp.
        self.assertNotEqual(captured["params"][13], 1000.0)


class TestPerCandleIdempotency(unittest.TestCase):
    """Bug 2: the same latest closed candle must only be applied once."""

    def _run_sync_once(self, cfg, position, df):
        log = []
        pool = _FakePool(position, log)
        with patch("paper.engine.get_paper_configs", return_value=[cfg]), \
             patch("paper.engine.get_active_positions", return_value=[position]), \
             patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df), \
             patch("paper.engine.get_pool", return_value=pool):
            result = pe.sync_and_evaluate_paper_trading()
        return result, log

    def test_second_poll_on_same_candle_does_not_reapply(self):
        cfg = _make_cfg()
        # Position was opened on the PREVIOUS candle -- this poll cycle is
        # the first time CANDLE_A's data is being applied to it.
        position = _make_open_position(last_update_time=CANDLE_A - 3_600_000)
        # Bar stays comfortably inside SL/TP -- no exit this bar, just a
        # normal "advance holding_bars and persist" update.
        df = _ohlcv_df(close=60800.0, high=60900.0, low=60700.0, atr=400.0, open_time=CANDLE_A)

        result1, log1 = self._run_sync_once(cfg, position, df)
        self.assertEqual(result1["positions_updated"], 1)
        self.assertEqual(position["holding_bars"], 1)
        self.assertEqual(position["last_update_time"], CANDLE_A)

        # Second poll, same candle (fetch_ohlcv_with_features_df returns the
        # identical latest bar -- exactly what a 60s poller sees for most of
        # an hour). get_active_positions now returns the ALREADY-UPDATED
        # position (as a real DB round trip would), so the guard should fire.
        result2, log2 = self._run_sync_once(cfg, position, df)
        self.assertEqual(result2["positions_updated"], 0)
        self.assertEqual(position["holding_bars"], 1, "holding_bars must not advance twice for one candle")

    def test_new_candle_after_guard_still_applies_normally(self):
        cfg = _make_cfg()
        position = _make_open_position(last_update_time=CANDLE_A)  # already applied
        df_next = _ohlcv_df(close=61000.0, high=61100.0, low=60900.0, atr=400.0, open_time=CANDLE_B)

        result, _ = self._run_sync_once(cfg, position, df_next)
        self.assertEqual(result["positions_updated"], 1)
        self.assertEqual(position["holding_bars"], 1)
        self.assertEqual(position["last_update_time"], CANDLE_B)


class TestSameCandleReEntryGuard(unittest.TestCase):
    """Bug 3: a signal must not reopen a position it already opened (and
    closed) on this same candle."""

    def test_entry_skipped_when_already_traded_on_this_candle(self):
        cfg = _make_cfg()
        df = _ohlcv_df(close=64000.0, high=64100.0, low=63900.0, atr=500.0, open_time=CANDLE_A)
        fake_strategy = MagicMock(return_value=[{
            "direction": "LONG", "entry_price": 64000.0, "stop_loss": 63000.0,
            "take_profit": 66000.0, "entry_time": CANDLE_A,
        }])

        with patch("paper.engine.get_paper_configs", return_value=[cfg]), \
             patch("paper.engine.get_active_positions", return_value=[]), \
             patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": fake_strategy}), \
             patch("paper.engine._signal_already_traded", return_value=True) as already_traded, \
             patch("paper.engine._insert_position") as insert_position:
            pe.sync_and_evaluate_paper_trading()

        already_traded.assert_called_once()
        insert_position.assert_not_called()

    def test_entry_proceeds_when_not_already_traded(self):
        cfg = _make_cfg()
        df = _ohlcv_df(close=64000.0, high=64100.0, low=63900.0, atr=500.0, open_time=CANDLE_A)
        fake_strategy = MagicMock(return_value=[{
            "direction": "LONG", "entry_price": 64000.0, "stop_loss": 63000.0,
            "take_profit": 66000.0, "entry_time": CANDLE_A,
        }])

        with patch("paper.engine.get_paper_configs", return_value=[cfg]), \
             patch("paper.engine.get_active_positions", return_value=[]), \
             patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df), \
             patch("paper.engine.STRATEGIES", {"trend_ema_v1": fake_strategy}), \
             patch("paper.engine._signal_already_traded", return_value=False), \
             patch("paper.circuit_breaker.check_circuit_breakers", return_value=(True, "")), \
             patch("paper.engine._insert_position", return_value=555) as insert_position:
            result = pe.sync_and_evaluate_paper_trading()

        insert_position.assert_called_once()
        self.assertEqual(result["new_positions_opened"], 1)


if __name__ == "__main__":
    unittest.main()
