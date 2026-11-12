"""Focused regression suite for Work Order 71759.

Verifies the separation of outbox enqueue authorization from Telegram delivery authorization:
1. Enqueue succeeds on signal_bot_test with enqueue flag enabled and delivery flag unset.
2. Enqueue fails when enqueue flag is unset.
3. Enqueue fails in observation-only mode.
4. Enqueue fails when database identity is missing or not allowlisted.
5. Enqueue does not require delivery authorization.
6. Delivery remains blocked when delivery authorization is absent.
7. Dispatcher / sender makes zero HTTP requests when delivery is unauthorized (fail-on-call mock).
8. Enqueue authorization cannot indirectly activate the worker or dispatcher.
9. Promoted-strategy filtering remains unchanged.
10. Non-promoted strategies do not produce outbox events.
11. Transaction rollback and idempotency behavior remain unchanged.
12. Production startup safety behavior remains unchanged.
"""

import json
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import pytest

from notifications.telegram_notifier import (
    is_telegram_delivery_authorized,
    send_telegram_message,
)
from paper.retailbot2 import (
    ALLOWED_OUTBOX_ENQUEUE_DATABASES,
    BotConfig,
    DatabaseManager,
    RetailDeathTrapBot,
    TelegramAlert,
    is_observation_only,
    is_outbox_enqueue_authorized,
    is_trading_authorized,
)
import run_all


class MockCursor:
    def __init__(self, row=None, rows=None, lastrowid=101, execute_error=None):
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
    db.logger = logging.getLogger("test.retailbot2.outbox.auth")
    db.config = config or BotConfig()
    db.get_conn = Mock(return_value=conn)
    return db


# ═══════════════════════════════════════════════════════════════════
# 1. Enqueue succeeds on signal_bot_test with enqueue enabled, delivery unset
# ═══════════════════════════════════════════════════════════════════

def test_enqueue_succeeds_on_signal_bot_test_with_delivery_unset(monkeypatch):
    """1. Enqueue succeeds on signal_bot_test when RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1
    and RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is unset."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=True)
    assert is_outbox_enqueue_authorized(config) is True

    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=config)

    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_success",
        event_type="operational",
        payload={"text": "hello test"},
    )
    assert result is True
    assert len(cursor.calls) == 1
    assert "INSERT INTO rdt_telegram_outbox" in cursor.calls[0][0]
    assert conn.commits == 1


# ═══════════════════════════════════════════════════════════════════
# 2. Enqueue fails when enqueue flag is unset
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag_value", [None, "", "0", "false", "no"])
def test_enqueue_fails_when_enqueue_flag_unset(monkeypatch, flag_value):
    """2. Enqueue authorization fails closed when RETAILBOT2_OUTBOX_ENQUEUE_ENABLED is unset or not '1'."""
    if flag_value is None:
        monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", flag_value)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    assert is_outbox_enqueue_authorized(config) is False

    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=config)

    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_flag_unset",
        event_type="operational",
        payload={"text": "hello"},
    )
    assert result is False
    assert len(cursor.calls) == 0
    assert conn.commits == 0


# ═══════════════════════════════════════════════════════════════════
# 3. Enqueue fails in observation-only mode
# ═══════════════════════════════════════════════════════════════════

def test_enqueue_fails_in_observation_only_mode(monkeypatch):
    """3. Observation mode (via config or environment) strictly suppresses outbox enqueue."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)

    # 3a. config.observation_only = True
    config_obs = SimpleNamespace(db_name="signal_bot_test", observation_only=True)
    assert is_outbox_enqueue_authorized(config_obs) is False

    # 3b. RETAILBOT2_OBSERVATION_ONLY=1 in environment
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    config_non_obs = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    assert is_outbox_enqueue_authorized(config_non_obs) is False

    cursor = MockCursor()
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=config_obs)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_obs",
        event_type="operational",
        payload={"text": "blocked"},
    )
    assert result is False
    assert len(cursor.calls) == 0


# ═══════════════════════════════════════════════════════════════════
# 4. Enqueue fails when database identity is missing or not allowlisted
# ═══════════════════════════════════════════════════════════════════

def test_enqueue_fails_when_db_missing_or_not_allowlisted(monkeypatch):
    """4. Enqueue authorization validates database identity against allowlist and fails closed."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    # Allowlist contains exactly signal_bot_test and crypto_signals
    assert ALLOWED_OUTBOX_ENQUEUE_DATABASES == ("signal_bot_test", "crypto_signals")

    # 4a. config is None -> fails closed
    assert is_outbox_enqueue_authorized(None) is False

    # 4b. config has no db_name or database attribute -> fails closed
    assert is_outbox_enqueue_authorized(SimpleNamespace(observation_only=False)) is False

    # 4c. Unauthorized database names fail closed
    for bad_db in ["crypto_signals_dev", "test", "production", "mysql", "information_schema", "", "   "]:
        cfg = SimpleNamespace(db_name=bad_db, observation_only=False)
        assert is_outbox_enqueue_authorized(cfg) is False

    # 4d. Allowlisted databases pass
    assert is_outbox_enqueue_authorized(SimpleNamespace(db_name="signal_bot_test", observation_only=False)) is True
    assert is_outbox_enqueue_authorized(SimpleNamespace(db_name="crypto_signals", observation_only=False)) is True


# ═══════════════════════════════════════════════════════════════════
# 5. Enqueue does not require delivery authorization
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("delivery_val", [None, "", "0", "false", "no", "arbitrary"])
def test_enqueue_does_not_require_delivery_authorization(monkeypatch, delivery_val):
    """5. Enqueue authorization is independent of RETAILBOT2_ALLOW_TELEGRAM_DELIVERY."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    if delivery_val is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", delivery_val)

    cfg_test = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)

    assert is_outbox_enqueue_authorized(cfg_test) is True
    assert is_outbox_enqueue_authorized(cfg_prod) is True


# ═══════════════════════════════════════════════════════════════════
# 6. Delivery remains blocked when delivery authorization is absent
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("delivery_val", [None, "", "0", "false"])
def test_delivery_remains_blocked_when_delivery_authorization_absent(monkeypatch, delivery_val):
    """6. External Telegram delivery remains strictly blocked when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is not '1'."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    if delivery_val is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", delivery_val)

    # 6a. is_telegram_delivery_authorized is False
    assert is_telegram_delivery_authorized() is False

    # 6b. send_telegram_message suppresses send
    res = send_telegram_message("Test message")
    assert res.get("success") is False
    assert res.get("suppressed") is True

    # 6c. TelegramAlert in retailbot2 is disabled
    alert = TelegramAlert(token="dummy_token", chat_id="123456", logger=logging.getLogger("test"))
    assert alert.global_authorized is False
    assert alert.direct_authorized is False
    assert alert.enabled is False
    assert alert.send("direct message") is False


# ═══════════════════════════════════════════════════════════════════
# 7. Dispatcher makes zero HTTP requests when delivery is unauthorized
# ═══════════════════════════════════════════════════════════════════

def test_dispatcher_makes_zero_http_requests_when_delivery_unauthorized(monkeypatch):
    """7. Network transport boundary is never reached when delivery is unauthorized."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)

    def fail_http(*args, **kwargs):
        raise AssertionError("HTTP boundary breached: request attempted without delivery authorization!")

    with patch("requests.post", side_effect=fail_http), \
         patch("requests.get", side_effect=fail_http), \
         patch("requests.Session.send", side_effect=fail_http), \
         patch("requests.Session.request", side_effect=fail_http):

        # Attempting send_telegram_message must not invoke HTTP
        res = send_telegram_message("Network probe")
        assert res.get("success") is False
        assert res.get("suppressed") is True

        # Attempting direct TelegramAlert must not invoke HTTP
        alert = TelegramAlert(token="dummy_token", chat_id="123456", logger=logging.getLogger("test"))
        assert alert.send("Network probe direct") is False

        # Standalone outbox worker CLI refuses production startup without delivery flag
        from paper import outbox_worker
        with patch.object(sys, "argv", ["outbox_worker", "--database", "crypto_signals"]):
            with pytest.raises(SystemExit) as exc_info:
                outbox_worker.main()
            assert exc_info.value.code == 1


# ═══════════════════════════════════════════════════════════════════
# 8. Enqueue authorization cannot indirectly activate worker or dispatcher
# ═══════════════════════════════════════════════════════════════════

def test_enqueue_authorization_cannot_indirectly_activate_worker_or_dispatcher(monkeypatch):
    """8. Enabling RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1 alone does not activate outbox_worker in supervisor."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OUTBOX_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)

    env = {
        "RETAILBOT2_ENABLED": "1",
        "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED": "1",
    }
    processes = run_all.build_processes(env)
    assert "retailbot2_outbox" not in processes

    # Delivery authorization remains absent
    assert is_telegram_delivery_authorized() is False


# ═══════════════════════════════════════════════════════════════════
# 9 & 10. Promoted strategy filtering and non-promoted strategy suppression
# ═══════════════════════════════════════════════════════════════════

def test_promoted_and_unpromoted_strategy_outbox_persistence(monkeypatch):
    """9 & 10. Promoted strategies enqueue outbox events; unpromoted strategies do not."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(
        db_name="signal_bot_test",
        allow_trading=True,
        observation_only=False,
        rr_ratio=2.0,
    )
    cursor = MockCursor(lastrowid=42)
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=config)

    # 9. Promoted strategy (telegram_enabled=True) persists outbox event
    promoted_signal = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry": 50000.0,
        "stop": 49000.0,
        "target": 52000.0,
        "atr": 1000.0,
        "rr": 2.0,
        "strategy": "BB_REV",
        "telegram_enabled": True,
        "confluence_score": 1,
    }
    entry_alert = {"signal": promoted_signal, "units": 1.0, "mode": "shadow"}
    tid_promoted = db.save_trade(promoted_signal, 1.0, "shadow", entry_alert=entry_alert)
    assert tid_promoted == 42
    sql_statements = [call[0] for call in cursor.calls]
    assert any("INSERT INTO rdt_trades" in s for s in sql_statements)
    assert any("INSERT INTO rdt_telegram_outbox" in s for s in sql_statements)

    # 10. Unpromoted strategy (telegram_enabled=False) does NOT persist outbox event
    cursor.calls.clear()
    cursor.lastrowid = 43
    unpromoted_signal = {
        "symbol": "ETH/USDT",
        "side": "LONG",
        "entry": 3000.0,
        "stop": 2900.0,
        "target": 3200.0,
        "atr": 100.0,
        "rr": 2.0,
        "strategy": "DBL_PAT",
        "telegram_enabled": False,
        "confluence_score": 1,
    }
    tid_unpromoted = db.save_trade(unpromoted_signal, 1.0, "shadow", entry_alert=None)
    assert tid_unpromoted == 43
    sql_statements = [call[0] for call in cursor.calls]
    assert any("INSERT INTO rdt_trades" in s for s in sql_statements)
    assert not any("INSERT INTO rdt_telegram_outbox" in s for s in sql_statements)


# ═══════════════════════════════════════════════════════════════════
# 11. Transaction rollback and idempotency
# ═══════════════════════════════════════════════════════════════════

def test_transaction_rollback_and_idempotency(monkeypatch):
    """11. DB failure during trade persistence triggers rollback; duplicate event_key is idempotent."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(
        db_name="signal_bot_test",
        allow_trading=True,
        observation_only=False,
        rr_ratio=2.0,
    )

    # 11a. Rollback on execution error
    failing_cursor = MockCursor(execute_error=RuntimeError("Deadlock / connection loss"))
    conn = MockConnection(failing_cursor)
    db = make_test_db(conn=conn, config=config)

    signal = {
        "symbol": "BTC/USDT",
        "side": "LONG",
        "entry": 50000.0,
        "stop": 49000.0,
        "target": 52000.0,
        "atr": 1000.0,
        "rr": 2.0,
        "strategy": "BB_REV",
        "telegram_enabled": True,
    }
    tid = db.save_trade(signal, 1.0, "shadow", entry_alert={"signal": signal})
    assert tid is None
    assert conn.rollbacks == 1

    # 11b. Idempotency SQL verify
    ok_cursor = MockCursor(lastrowid=99)
    ok_conn = MockConnection(ok_cursor)
    ok_db = make_test_db(conn=ok_conn, config=config)
    ok_db.save_trade(signal, 1.0, "shadow", entry_alert={"signal": signal})
    outbox_calls = [c for c in ok_cursor.calls if "rdt_telegram_outbox" in c[0]]
    assert len(outbox_calls) == 1
    assert "ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)" in outbox_calls[0][0]


# ═══════════════════════════════════════════════════════════════════
# 12. Production startup safety behavior unchanged
# ═══════════════════════════════════════════════════════════════════

def test_production_startup_safety_unchanged(monkeypatch):
    """12. Clean production environment (no flags set) defaults to all gates closed."""
    for key in [
        "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED",
        "RETAILBOT2_OUTBOX_WORKER_ENABLED",
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY",
        "RETAILBOT2_ALLOW_DIRECT_TELEGRAM",
        "RETAILBOT2_ALLOW_TRADING",
        "RETAILBOT2_OBSERVATION_ONLY",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Default BotConfig
    cfg = BotConfig()
    assert cfg.observation_only is False
    assert cfg.allow_trading is False

    # All authorization gates fail closed
    assert is_trading_authorized(cfg) is False
    assert is_outbox_enqueue_authorized(cfg) is False
    assert is_telegram_delivery_authorized() is False

    # Supervisor launches no outbox worker by default
    assert "retailbot2_outbox" not in run_all.build_processes({})
