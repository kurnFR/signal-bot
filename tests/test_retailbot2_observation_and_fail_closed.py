"""Regression tests for RetailBot2 fail-closed dashboard control, observation-only mode,
outbox authorization gates, and graceful SIGTERM shutdown (Work Order 71717).
"""

import json
import logging
import os
import signal as sys_signal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import pytest

from paper.retailbot2 import (
    BotConfig,
    DatabaseManager,
    RetailDeathTrapBot,
    SignalMode,
    is_observation_only,
    is_outbox_enqueue_authorized,
    is_trading_authorized,
)


class MockCursor:
    def __init__(self, row=None, rows=None, lastrowid=1, execute_error=None):
        self.row = row
        self.rows = rows or []
        self.lastrowid = lastrowid
        self.execute_error = execute_error
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class MockConnection:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def make_test_db(conn=None, config=None):
    db = object.__new__(DatabaseManager)
    db.logger = logging.getLogger("test.retailbot2.db")
    db.config = config or BotConfig()
    db.get_conn = Mock(return_value=conn)
    return db


# ═══════════════════════════════════════════════════════════════════
# FIX A: FAIL-CLOSED DASHBOARD CONTROL TESTS
# ═══════════════════════════════════════════════════════════════════

def test_bot_control_fail_closed_when_db_down():
    """1. get_bot_control returns enabled=False and available=False when DB connection fails."""
    db = make_test_db(conn=None)
    result = db.get_bot_control()
    assert result == {"enabled": False, "overrides": {}, "available": False}


def test_bot_control_fail_closed_on_missing_row():
    """2. get_bot_control returns enabled=False and available=False when id=1 is missing."""
    cursor = MockCursor(row=None)
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn)
    result = db.get_bot_control()
    assert result == {"enabled": False, "overrides": {}, "available": False}
    assert cursor.closed is True
    assert conn.closed is True


def test_bot_control_fail_closed_on_malformed_overrides():
    """3. get_bot_control returns enabled=False and available=False on invalid JSON or non-dict payloads."""
    # Subtest 3a: JSONDecodeError
    cursor_invalid_json = MockCursor(row={"enabled": 1, "overrides_json": "{not-valid-json"})
    conn_a = MockConnection(cursor_invalid_json)
    db_a = make_test_db(conn=conn_a)
    result_a = db_a.get_bot_control()
    assert result_a == {"enabled": False, "overrides": {}, "available": False}

    # Subtest 3b: Valid JSON but not a dictionary (e.g. list)
    cursor_list_json = MockCursor(row={"enabled": 1, "overrides_json": "[1, 2, 3]"})
    conn_b = MockConnection(cursor_list_json)
    db_b = make_test_db(conn=conn_b)
    result_b = db_b.get_bot_control()
    assert result_b == {"enabled": False, "overrides": {}, "available": False}


def test_bot_control_refresh_failure_disables_trading():
    """4. _reload_control() disables trading_enabled when previously True and control read fails."""
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.reload")
    bot.config = BotConfig()
    bot.observation_only = False
    bot.trading_enabled = True
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)

    # Control read returns unavailable
    bot.db = Mock()
    bot.db.get_bot_control.return_value = {"enabled": False, "overrides": {}, "available": False}

    bot._reload_control(force=True)

    assert bot.trading_enabled is False
    bot.notify_operational.assert_called_once()
    assert "Trading PAUSED" in bot.notify_operational.call_args[0][0]


def test_bot_control_recovers_when_db_restored():
    """5. trading_enabled turns back True when database read succeeds again with enabled=1 and trading is authorized."""
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.recover")
    bot.config = BotConfig(allow_trading=True)
    bot.observation_only = False
    bot.trading_enabled = False
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)

    # Database recovered with enabled=1
    bot.db = Mock()
    bot.db.get_bot_control.return_value = {"enabled": True, "overrides": {}, "available": True}

    bot._reload_control(force=True)

    assert bot.trading_enabled is True
    bot.notify_operational.assert_called_once()
    assert "Trading ENABLED" in bot.notify_operational.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════
# FIX B: OBSERVATION-ONLY MODE TESTS
# ═══════════════════════════════════════════════════════════════════

def test_observation_mode_flag_and_config(monkeypatch):
    """6. Activated by RETAILBOT2_OBSERVATION_ONLY=1 or BotConfig.observation_only=True."""
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cfg_default = BotConfig(observation_only=False)
    assert is_observation_only(cfg_default) is False

    cfg_obs = BotConfig(observation_only=True)
    assert is_observation_only(cfg_obs) is True

    # Via environment variable
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    assert is_observation_only(None) is True
    assert is_observation_only(cfg_default) is True


def test_observation_mode_forces_trading_disabled(monkeypatch):
    """7. trading_enabled remains False in observation mode even if bot_control has enabled=1."""
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.obs_control")
    bot.config = BotConfig(observation_only=True)
    bot.observation_only = True
    bot.trading_enabled = False
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)
    bot.db = Mock()
    bot.db.get_bot_control.return_value = {"enabled": True, "overrides": {}, "available": True}

    bot._reload_control(force=True)

    assert bot.trading_enabled is False
    bot.db.get_bot_control.assert_not_called()


def test_observation_mode_prevents_scan_execution(monkeypatch):
    """8. Main loop does not call scan() in observation mode."""
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.obs_scan")
    bot.config = BotConfig(observation_only=True, exit_check_interval=1, heartbeat_interval=60)
    bot.observation_only = True
    bot.trading_enabled = False
    bot._shutdown_requested = False
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.equity_shadow = 10000.0
    bot.equity_inverse = 10000.0

    bot._reload_control = Mock()
    bot.scan = Mock()
    bot.notify_operational = Mock(return_value=False)

    def stop_bot():
        bot._shutdown_requested = True

    bot.check_exits = Mock(side_effect=stop_bot)

    bot.run()

    bot.scan.assert_not_called()
    assert bot.check_exits.call_count == 1


def test_observation_mode_prevents_trade_open_mutation(monkeypatch):
    """9. _open_trade() and DatabaseManager.save_trade() return None without DB writes."""
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.obs_open")
    bot.config = BotConfig(observation_only=True)
    bot.observation_only = True

    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 60000.0,
        "stop": 59000.0, "target": 62000.0, "atr": 500.0, "rr": 2.0,
    }

    # 9a: RetailDeathTrapBot._open_trade fails closed
    assert bot._open_trade(signal, "shadow") is None

    # 9b: DatabaseManager.save_trade fails closed without DB interaction
    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=bot.config)

    res = db.save_trade(signal, units=0.1, mode="shadow")
    assert res is None
    assert len(cursor.calls) == 0
    assert conn.commits == 0


def test_observation_mode_evaluates_exits_without_mutation(monkeypatch, caplog):
    """10. check_exits() evaluates SL/TP, logs [OBSERVE], does not mutate DB or active trades."""
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.obs_exits")
    bot.config = BotConfig(observation_only=True)
    bot.observation_only = True

    trade_record = {
        "id": 42,
        "symbol": "ETH/USDT",
        "side": "LONG",
        "mode": "shadow",
        "entry": 3000.0,
        "stop": 2900.0,
        "target": 3200.0,
        "units": 1.0,
        "strategy": "RSI_MEAN_REV",
        "telegram_enabled": True,
    }
    bot.active_shadow = {42: trade_record}
    bot.active_inverse = {}
    bot._close_trade = Mock()

    # Price at 2850 hits STOP_LOSS (stop=2900.0)
    bot.fetcher = Mock()
    bot.fetcher.get_price.return_value = 2850.0

    with caplog.at_level(logging.INFO):
        bot.check_exits()

    # Assert no trade closing mutation occurred
    bot._close_trade.assert_not_called()
    assert 42 in bot.active_shadow
    assert bot.active_shadow[42]["entry"] == 3000.0

    # Assert [OBSERVE] was logged with exit threshold details
    assert any("[OBSERVE]" in rec.message and "STOP_LOSS" in rec.message and "#42" in rec.message for rec in caplog.records)


def test_observation_mode_mutation_boundary_defense(monkeypatch):
    """11. Direct calls to close_trade() and close_trade_and_update_stats() fail closed without executing SQL."""
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=BotConfig(observation_only=True))

    # 11a: close_trade
    res1 = db.close_trade(trade_id=7, exit_price=100.0, reason="STOP_LOSS", pnl=-10.0, pnl_pct=-1.0)
    assert res1 is False
    assert len(cursor.calls) == 0

    # 11b: close_trade_and_update_stats
    res2 = db.close_trade_and_update_stats(
        trade_id=7, exit_price=100.0, reason="STOP_LOSS", pnl=-10.0, pnl_pct=-1.0,
        strategy="RSI", mode="shadow", exit_alert={"telegram_enabled": True},
    )
    assert res2 is False
    assert len(cursor.calls) == 0

    # 11c: update_stats
    db.update_stats(strategy="RSI", mode="shadow", won=False, pnl=-10.0)
    assert len(cursor.calls) == 0
    assert conn.commits == 0


# ═══════════════════════════════════════════════════════════════════
# FIX C: OUTBOX ENQUEUE AUTHORIZATION TESTS
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "enqueue_flag,delivery_flag,expected",
    [
        ("", "", False),
        ("0", "0", False),
        ("1", "", True),
        ("", "1", False),
        ("1", "0", True),
        ("0", "1", False),
        ("true", "true", False),  # Strictly requires "1"
        ("1", "1", True),
    ],
)
def test_outbox_enqueue_requires_both_authorization_flags(monkeypatch, enqueue_flag, delivery_flag, expected):
    """12. Enqueuing requires RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1 and valid database allowlist, independent of RETAILBOT2_ALLOW_TELEGRAM_DELIVERY."""
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", enqueue_flag)
    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", delivery_flag)

    config = BotConfig(observation_only=False)
    assert is_outbox_enqueue_authorized(config) is expected


def test_outbox_enqueue_suppressed_by_default(monkeypatch):
    """13. When flags are absent or 0, enqueue_telegram_outbox_event returns False and no rows inserted."""
    monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=BotConfig(observation_only=False))

    result = db.enqueue_telegram_outbox_event(
        event_key="op:123",
        event_type="operational",
        payload={"text": "hello"},
    )

    assert result is False
    assert len(cursor.calls) == 0


def test_outbox_enqueue_suppressed_in_observation_mode(monkeypatch):
    """14. Even if both authorization flags are 1, observation mode completely suppresses outbox enqueue."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "1")
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")

    config = BotConfig(observation_only=True)
    assert is_outbox_enqueue_authorized(config) is False

    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=config)

    result = db.enqueue_telegram_outbox_event(
        event_key="op:456",
        event_type="operational",
        payload={"text": "hello"},
    )

    assert result is False
    assert len(cursor.calls) == 0


# ═══════════════════════════════════════════════════════════════════
# FIX D: SHUTDOWN SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════

def test_sigterm_stops_bot_cleanly_without_unauthorized_outbox(monkeypatch, caplog):
    """15. RetailDeathTrapBot handles SIGTERM cleanly, stops loop, and does not enqueue shutdown notification when unauthorized."""
    monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.sigterm")
    bot.config = BotConfig(observation_only=False, exit_check_interval=1, heartbeat_interval=60)
    bot.observation_only = False
    bot.trading_enabled = True
    bot._shutdown_requested = False
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.equity_shadow = 10000.0
    bot.equity_inverse = 10000.0
    bot.tg = Mock()
    bot.tg.direct_authorized = False
    bot.tg.enabled = False

    cursor = MockCursor()
    conn = MockConnection(cursor)
    bot.db = make_test_db(conn=conn, config=bot.config)

    bot._reload_control = Mock()
    bot.scan = Mock()

    # Deliver SIGTERM on the first exit check iteration
    def handle_exit_and_signal():
        os.kill(os.getpid(), sys_signal.SIGTERM)

    bot.check_exits = Mock(side_effect=handle_exit_and_signal)

    with caplog.at_level(logging.INFO):
        bot.run()

    assert bot._shutdown_requested is True
    # scan should not execute after shutdown was requested
    bot.scan.assert_not_called()
    # Outbox should have no queries executed
    assert len(cursor.calls) == 0
    # Logger captures SIGTERM and clean shutdown
    assert any("Received SIGTERM" in rec.message for rec in caplog.records)
    assert any("Bot shutdown complete" in rec.message for rec in caplog.records)



def test_bot_control_fail_closed_on_query_exception():
    """get_bot_control fails closed and releases DB resources when SELECT raises."""
    cursor = MockCursor(execute_error=RuntimeError("simulated query failure"))
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn)

    result = db.get_bot_control()

    assert result == {"enabled": False, "overrides": {}, "available": False}
    assert cursor.closed is True
    assert conn.closed is True


def _make_shutdown_test_bot(symbols=None):
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.shutdown_fence")
    bot.config = BotConfig(
        observation_only=False,
        allow_trading=True,
        exit_check_interval=1,
        heartbeat_interval=60,
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=2,
        risk_per_trade=0.01,
    )
    bot.observation_only = False
    bot.trading_enabled = True
    bot._shutdown_requested = False
    bot.symbols = symbols or ["BTC/USDT", "ETH/USDT"]
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.equity_shadow = 10000.0
    bot.equity_inverse = 10000.0
    bot.fetcher = Mock()
    bot.engine = Mock()
    bot.db = Mock()
    bot.last_trade_time = {}
    bot.strategy_lifecycle = {}
    bot._strategy_lifecycle_initialized = True
    bot._strategy_lifecycle_unavailable = False
    bot.mode = SignalMode.BOTH
    bot.last_scan = {}
    bot._tunable_fields = set()
    return bot


def test_sigterm_during_price_fetch_prevents_all_subsequent_exit_closes():
    bot = _make_shutdown_test_bot()
    bot.active_shadow = {
        1: {"id": 1, "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "units": 1.0, "strategy": "RSI"},
        2: {"id": 2, "symbol": "ETH/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "units": 1.0, "strategy": "RSI"},
    }
    bot._close_trade = Mock()
    def signal_during_fetch(_symbol):
        bot._shutdown_requested = True
        return 90.0
    bot.fetcher.get_price.side_effect = signal_during_fetch

    bot.check_exits()

    bot.fetcher.get_price.assert_called_once()
    bot._close_trade.assert_not_called()


def test_shutdown_during_exit_processing_stops_remaining_positions():
    bot = _make_shutdown_test_bot()
    bot.active_shadow = {
        1: {"id": 1, "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "units": 1.0, "strategy": "RSI"},
        2: {"id": 2, "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "units": 1.0, "strategy": "RSI"},
    }
    bot.fetcher.get_price.return_value = 90.0
    def close_and_request_shutdown(*_args):
        bot._shutdown_requested = True
    bot._close_trade = Mock(side_effect=close_and_request_shutdown)

    bot.check_exits()

    bot._close_trade.assert_called_once()


def test_shutdown_during_scan_fetch_stops_remaining_symbols():
    bot = _make_shutdown_test_bot()
    def fetch_and_request_shutdown(*_args, **_kwargs):
        bot._shutdown_requested = True
        return None
    bot.fetcher.fetch.side_effect = fetch_and_request_shutdown

    bot.scan()

    bot.fetcher.fetch.assert_called_once()


def test_shutdown_during_strategy_processing_prevents_trade_open():
    bot = _make_shutdown_test_bot(symbols=["BTC/USDT"])
    import pandas as pd
    frame = pd.DataFrame({"close": [float(i) for i in range(120)]}, index=pd.date_range("2026-01-01", periods=120, freq="h"))
    bot.fetcher.fetch.side_effect = [frame, frame]
    bot.engine.calculate_indicators.return_value = frame
    def run_all_and_request_shutdown(*_args, **_kwargs):
        bot._shutdown_requested = True
        return [{"strategy": "RSI_CTR", "side": "LONG", "symbol": "BTC/USDT"}]
    bot.engine.run_all.side_effect = run_all_and_request_shutdown
    bot._get_strategy_lifecycle = Mock(return_value={"evaluation_enabled": True, "paper_enabled": True, "telegram_enabled": False})
    bot._open_trade = Mock()

    bot.scan()

    bot.engine.run_all.assert_called_once()
    bot._open_trade.assert_not_called()


def test_open_trade_after_shutdown_never_persists():
    bot = _make_shutdown_test_bot(symbols=["BTC/USDT"])
    bot._shutdown_requested = True
    bot.db.save_trade = Mock()
    result = bot._open_trade(
        {"strategy": "RSI_CTR", "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "rr": 2.0},
        "shadow",
    )
    assert result is None
    bot.db.save_trade.assert_not_called()


def test_normal_exit_processing_still_closes_triggered_trade():
    bot = _make_shutdown_test_bot(symbols=["BTC/USDT"])
    trade = {"id": 1, "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "units": 1.0, "strategy": "RSI", "telegram_enabled": False}
    bot.active_shadow = {1: trade}
    bot.fetcher.get_price.return_value = 90.0
    bot._close_trade = Mock()
    bot.check_exits()
    bot._close_trade.assert_called_once_with(1, 95.0, "STOP_LOSS", "shadow")



def test_sigint_stops_bot_cleanly(monkeypatch, caplog):
    monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.sigint")
    bot.config = BotConfig(observation_only=False, exit_check_interval=1, heartbeat_interval=60)
    bot.observation_only = False
    bot.trading_enabled = True
    bot._shutdown_requested = False
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.equity_shadow = 10000.0
    bot.equity_inverse = 10000.0
    bot.tg = Mock()
    bot.tg.direct_authorized = False
    bot.tg.enabled = False
    bot.db = make_test_db(conn=MockConnection(MockCursor()), config=bot.config)
    bot._reload_control = Mock()
    bot.scan = Mock()

    def signal_sigint():
        os.kill(os.getpid(), sys_signal.SIGINT)

    bot.check_exits = Mock(side_effect=signal_sigint)
    with caplog.at_level(logging.INFO):
        bot.run()

    assert bot._shutdown_requested is True
    bot.scan.assert_not_called()
    assert any("Received SIGINT" in rec.message for rec in caplog.records)


def test_shutdown_during_save_trade_does_not_mutate_in_memory_state():
    bot = _make_shutdown_test_bot(symbols=["BTC/USDT"])
    bot._get_strategy_lifecycle = Mock(return_value={"paper_enabled": True, "telegram_enabled": False})
    bot._can_open = Mock(return_value=(True, "OK"))
    bot.db.save_trade = Mock(side_effect=lambda *args, **kwargs: (setattr(bot, "_shutdown_requested", True) or 77))
    result = bot._open_trade(
        {"strategy": "RSI_CTR", "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "rr": 2.0},
        "shadow",
    )
    assert result == 77
    assert bot.active_shadow == {}
    assert bot.active_inverse == {}
    assert bot.last_trade_time == {}


# ═══════════════════════════════════════════════════════════════════
# PHASE 1: TRADING AUTHORIZATION & LEGACY CONTROL HARDENING
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "env_val,config_val,obs_val,expected",
    [
        (None, None, False, False),       # Unset defaults to unauthorized
        ("0", None, False, False),        # Explicit 0 is unauthorized
        ("false", None, False, False),    # Non-1 is unauthorized
        ("1", None, False, True),         # Env 1 is authorized
        (None, False, False, False),      # Config False is unauthorized
        (None, True, False, True),        # Config True is authorized
        ("1", False, False, False),       # Config False overrides env 1
        ("0", True, False, True),         # Config True overrides env 0
        ("1", True, True, False),         # Observation mode overrides authorization
        ("1", None, True, False),         # Observation mode overrides env 1
    ]
)
def test_is_trading_authorized_matrix(monkeypatch, env_val, config_val, obs_val, expected):
    """Trading authorization strictly gates execution and observation mode always dominates."""
    if env_val is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TRADING", env_val)

    if obs_val:
        monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    else:
        monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cfg = BotConfig(observation_only=obs_val)
    if config_val is not None:
        cfg.allow_trading = config_val
    assert is_trading_authorized(cfg) is expected


def test_bot_control_fresh_db_defaults_to_disabled():
    """Fresh database row (enabled=0, overrides_json=None) returns enabled=False, available=True."""
    cursor = MockCursor(row={"enabled": 0, "overrides_json": None})
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn)
    result = db.get_bot_control()
    assert result == {"enabled": False, "overrides": {}, "available": True}


def test_bot_control_legacy_seeded_row_defaults_to_disabled(caplog):
    """Legacy row seeded by migration 022 (enabled=1, overrides_json=None) returns enabled=False."""
    cursor = MockCursor(row={"enabled": 1, "overrides_json": None})
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn)
    with caplog.at_level(logging.WARNING):
        result = db.get_bot_control()
    assert result == {"enabled": False, "overrides": {}, "available": True, "legacy_unconfigured": True}
    assert any("legacy unconfigured bot_control row" in rec.message for rec in caplog.records)


def test_bot_control_explicitly_configured_row_honors_enabled():
    """Explicitly saved row with overrides_json preserves enabled=True."""
    cursor = MockCursor(row={"enabled": 1, "overrides_json": "{}"})
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn)
    result = db.get_bot_control()
    assert result == {"enabled": True, "overrides": {}, "available": True}


def test_bot_control_does_not_enable_trading_without_independent_authorization(monkeypatch, caplog):
    """Trading remains disabled even when DB row has enabled=1 if independent authorization is missing."""
    monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.no_auth")
    bot.config = BotConfig(allow_trading=False, observation_only=False)
    bot.observation_only = False
    bot.trading_enabled = False
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)

    bot.db = Mock()
    bot.db.get_bot_control.return_value = {"enabled": True, "overrides": {}, "available": True}

    with caplog.at_level(logging.WARNING):
        bot._reload_control(force=True)

    assert bot.trading_enabled is False
    assert any("RETAILBOT2_ALLOW_TRADING=1 is missing" in rec.message for rec in caplog.records)


def test_legacy_persisted_row_cannot_activate_trading_even_with_allow_trading(monkeypatch):
    """Even if RETAILBOT2_ALLOW_TRADING=1 is set, a legacy unconfigured row does not activate trading."""
    monkeypatch.setenv("RETAILBOT2_ALLOW_TRADING", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cursor = MockCursor(row={"enabled": 1, "overrides_json": None})
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=BotConfig(allow_trading=True))

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.legacy_persisted")
    bot.config = BotConfig(allow_trading=True, observation_only=False)
    bot.observation_only = False
    bot.trading_enabled = False
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)
    bot.db = db

    bot._reload_control(force=True)

    assert bot.trading_enabled is False


def test_dual_gates_required_for_trading(monkeypatch):
    """Trading requires BOTH RETAILBOT2_ALLOW_TRADING=1 AND explicit DB enabled=True."""
    monkeypatch.setenv("RETAILBOT2_ALLOW_TRADING", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cursor = MockCursor(row={"enabled": 1, "overrides_json": "{}"})
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=BotConfig(allow_trading=True))

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.dual_gates")
    bot.config = BotConfig(allow_trading=True, observation_only=False)
    bot.observation_only = False
    bot.trading_enabled = False
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)
    bot.db = db

    bot._reload_control(force=True)

    assert bot.trading_enabled is True


def test_trading_authorization_revocation_pauses_trading(caplog):
    """Revoking trading authorization dynamically transitions trading to PAUSED."""
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.revocation")
    bot.config = BotConfig(allow_trading=False, observation_only=False)
    bot.observation_only = False
    bot.trading_enabled = True  # Was previously enabled
    bot._last_control_reload = 0.0
    bot._control_reload_interval = 10.0
    bot._tunable_fields = set()
    bot.notify_operational = Mock(return_value=True)
    bot.db = Mock()
    bot.db.get_bot_control.return_value = {"enabled": True, "overrides": {}, "available": True}

    with caplog.at_level(logging.WARNING):
        bot._reload_control(force=True)

    assert bot.trading_enabled is False
    bot.notify_operational.assert_called_once()
    assert "Trading PAUSED" in bot.notify_operational.call_args[0][0]
    assert any("trading authorization was revoked" in rec.message for rec in caplog.records)


def test_save_trade_fails_closed_when_trading_unauthorized(monkeypatch):
    """DatabaseManager.save_trade returns None without mutating DB when unauthorized."""
    monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=BotConfig(allow_trading=False))

    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 50000.0,
        "stop": 49000.0, "target": 52000.0, "atr": 500.0, "rr": 2.0,
    }
    tid = db.save_trade(signal, units=0.1, mode="shadow")
    assert tid is None
    assert len(cursor.calls) == 0
    assert conn.commits == 0


def test_open_trade_fails_closed_when_trading_unauthorized(monkeypatch):
    """RetailDeathTrapBot._open_trade returns None when trading is unauthorized or disabled."""
    monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.open_unauth")
    bot.config = BotConfig(allow_trading=False, observation_only=False)
    bot.observation_only = False
    bot.trading_enabled = False
    bot._shutdown_requested = False
    bot.db = Mock()

    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 50000.0,
        "stop": 49000.0, "target": 52000.0, "atr": 500.0, "rr": 2.0,
    }
    result = bot._open_trade(signal, "shadow")
    assert result is None
    bot.db.save_trade.assert_not_called()


def test_mixed_strategy_lifecycle_queues_only_selected_telegram_alert(monkeypatch):
    """The real worker entry path must queue an alert only for the strategy whose saved lifecycle flag is on."""
    monkeypatch.setenv("RETAILBOT2_ALLOW_TRADING", "1")
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = BotConfig(allow_trading=True, observation_only=False)
    selected_cursor = MockCursor(lastrowid=101)
    unselected_cursor = MockCursor(lastrowid=102)
    selected_conn = MockConnection(selected_cursor)
    unselected_conn = MockConnection(unselected_cursor)
    db = make_test_db(conn=selected_conn, config=config)
    db.get_conn = Mock(side_effect=[selected_conn, unselected_conn])
    db.get_strategy_lifecycle_settings = Mock(return_value=[
        {
            "strategy_id": "BB_REV", "mode": "shadow",
            "evaluation_enabled": 1, "paper_enabled": 1,
            "telegram_enabled": 1, "report_enabled": 1,
            "auto_promote_enabled": 0,
        },
        {
            "strategy_id": "DBL_PAT", "mode": "shadow",
            "evaluation_enabled": 1, "paper_enabled": 1,
            "telegram_enabled": 0, "report_enabled": 1,
            "auto_promote_enabled": 0,
        },
    ])

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.mixed_strategy_outbox")
    bot.config = config
    bot.observation_only = False
    bot.trading_enabled = True
    bot._shutdown_requested = False
    bot.db = db
    bot.equity_shadow = config.initial_capital
    bot.equity_inverse = config.initial_capital
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.last_trade_time = {}
    bot.strategy_lifecycle = {}
    bot._strategy_lifecycle_initialized = False
    bot._strategy_lifecycle_reload_at = 0.0
    bot._strategy_lifecycle_unavailable = False
    bot._can_open = Mock(return_value=(True, "OK"))

    def signal(strategy_id, symbol):
        return {
            "symbol": symbol, "side": "LONG", "entry": 100.0,
            "stop": 95.0, "target": 110.0, "atr": 2.0, "rr": 2.0,
            "strategy": strategy_id, "strategies_triggered": [strategy_id],
            "confluence_score": 1, "reason": "mixed-strategy lifecycle test",
        }

    selected_trade_id = bot._open_trade(signal("BB_REV", "BTC/USDT"), "shadow")
    unselected_trade_id = bot._open_trade(signal("DBL_PAT", "ETH/USDT"), "shadow")

    assert selected_trade_id == 101
    assert unselected_trade_id == 102
    db.get_strategy_lifecycle_settings.assert_called_once()

    selected_trade_insert = next(
        params for sql, params in selected_cursor.calls
        if "insert into rdt_trades" in sql.lower()
    )
    unselected_trade_insert = next(
        params for sql, params in unselected_cursor.calls
        if "insert into rdt_trades" in sql.lower()
    )
    assert selected_trade_insert[11] is True
    assert unselected_trade_insert[11] is False

    selected_outbox_inserts = [
        (sql, params) for sql, params in selected_cursor.calls
        if "insert into rdt_telegram_outbox" in sql.lower()
    ]
    unselected_outbox_inserts = [
        (sql, params) for sql, params in unselected_cursor.calls
        if "insert into rdt_telegram_outbox" in sql.lower()
    ]
    assert len(selected_outbox_inserts) == 1
    assert selected_outbox_inserts[0][1][0] == "retailbot2:entry:101"
    assert selected_outbox_inserts[0][1][1] == selected_trade_id
    assert unselected_outbox_inserts == []



def test_mixed_strategy_exit_lifecycle_queues_only_selected_telegram_alert(monkeypatch):
    """The real close path must enqueue an exit alert only for trades persisted as Telegram-selected."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = BotConfig(observation_only=False)
    selected_cursor = MockCursor()
    selected_cursor.rowcount = 1
    unselected_cursor = MockCursor()
    unselected_cursor.rowcount = 1
    selected_conn = MockConnection(selected_cursor)
    unselected_conn = MockConnection(unselected_cursor)
    db = make_test_db(conn=selected_conn, config=config)
    db.get_conn = Mock(side_effect=[selected_conn, unselected_conn])

    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.mixed_strategy_exit_outbox")
    bot.config = config
    bot.observation_only = False
    bot._shutdown_requested = False
    bot.db = db
    bot.equity_shadow = 1000.0
    bot.equity_inverse = 1000.0
    bot.active_shadow = {
        201: {
            "id": 201, "symbol": "BTC/USDT", "side": "LONG",
            "entry": 100.0, "units": 2.0, "strategy": "BB_REV",
            "mode": "shadow", "telegram_enabled": True,
        },
        202: {
            "id": 202, "symbol": "ETH/USDT", "side": "LONG",
            "entry": 100.0, "units": 1.0, "strategy": "DBL_PAT",
            "mode": "shadow", "telegram_enabled": False,
        },
    }
    bot.active_inverse = {}

    bot._close_trade(201, 110.0, "TAKE_PROFIT", "shadow")
    bot._close_trade(202, 90.0, "STOP_LOSS", "shadow")

    selected_exit_inserts = [
        (sql, params) for sql, params in selected_cursor.calls
        if "insert into rdt_telegram_outbox" in sql.lower()
    ]
    unselected_exit_inserts = [
        (sql, params) for sql, params in unselected_cursor.calls
        if "insert into rdt_telegram_outbox" in sql.lower()
    ]

    assert len(selected_exit_inserts) == 1
    selected_sql, selected_params = selected_exit_inserts[0]
    assert "values (%s, %s, 'exit'" in selected_sql.lower()
    assert selected_params[0] == "retailbot2:exit:201"
    assert selected_params[1] == 201
    selected_payload = json.loads(selected_params[2])
    assert selected_payload["reason"] == "TAKE_PROFIT"
    assert selected_payload["exit_price"] == 110.0
    assert unselected_exit_inserts == []

    assert selected_conn.commits == 1
    assert unselected_conn.commits == 1
    assert 201 not in bot.active_shadow
    assert 202 not in bot.active_shadow
    assert bot.equity_shadow == 1010.0
    assert bot.equity_inverse == 1000.0
