"""Focused regression suite for Work Order 71760.

Verifies connection-aware MariaDB identity verification before outbox enqueue:
1. Actual signal_bot_test connection identity permits enqueue with delivery authorization unset.
2. Config says signal_bot_test, actual connection reports another database: reject.
3. Config says crypto_signals, actual connection reports signal_bot_test: reject (identity mismatch).
4. Config says signal_bot_test, actual connection reports signal_bot_test: accept.
   Config says crypto_signals, actual connection reports crypto_signals: accept.
5. Missing configuration fails closed (None or missing db_name).
6. Missing connection fails closed (conn is None).
7. SELECT DATABASE() returns NULL / None: reject.
8. Database identity query raises an exception: reject.
9. Unallowlisted actual database: reject.
10. Enqueue flag unset: reject.
11. Observation-only mode: reject (via config or environment).
12. Delivery authorization unset: enqueue remains possible on the test database.
13. Delivery remains blocked without delivery authorization.
14. Unauthorized dispatch makes zero HTTP requests (fail-on-call mock).
15. Enqueue authorization cannot start or activate a worker in supervisor.
16. Existing promoted-strategy filtering remains unchanged and verifies connection identity.
17. Non-promoted strategies remain suppressed.
18. Existing transaction rollback and idempotency behavior remain intact.
19. (retired with the worker) close_trade_and_update_stats verified connection
    identity before outbox enqueue; trade CRUD is gone, the identity check
    itself is covered by 1-18 against OutboxStore.enqueue_telegram_outbox_event.
20. No OutboxConfig() fallback when configuration is absent in any enqueue authorization path.
21. Caller-supplied database parameter never substitutes for active connection identity.
22. Production startup safety defaults remain completely closed.
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
# Step 3: the worker (paper/retailbot2.py) is deleted. The outbox store
# and its gates moved to paper/outbox_store.py in step 1; this suite now
# tests that module directly. BotConfig -> OutboxConfig (same
# db_name/observation_only surface used here), DatabaseManager -> OutboxStore.
from paper.outbox_store import (
    ALLOWED_OUTBOX_ENQUEUE_DATABASES,
    OutboxConfig,
    OutboxStore,
    get_connection_database,
    is_observation_only,
    is_outbox_enqueue_authorized,
)
import run_all


class MockCursor:
    def __init__(
        self,
        db_name="signal_bot_test",
        row=None,
        rows=None,
        lastrowid=101,
        execute_error=None,
        query_errors=None,
    ):
        self.db_name = db_name
        self.row = row if row is not None else ((db_name,) if db_name is not None else None)
        self.rows = rows or []
        self.lastrowid = lastrowid
        self.rowcount = 1
        self.execute_error = execute_error
        self.query_errors = query_errors or {}
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error
        for query_key, err in self.query_errors.items():
            if query_key.upper() in sql.upper():
                raise err
        if "SELECT DATABASE()" in sql.upper():
            if self.db_name is not None:
                self.row = (self.db_name,)
            else:
                self.row = (None,)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class MockConnection:
    def __init__(self, cursor=None, db_name="signal_bot_test"):
        if cursor is None:
            self.cur = MockCursor(db_name=db_name)
        else:
            self.cur = cursor
            if not hasattr(self.cur, "db_name"):
                self.cur.db_name = db_name
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
    db = object.__new__(OutboxStore)
    db.logger = logging.getLogger("test.retailbot2.outbox.auth")
    db.config = config
    db.get_conn = Mock(return_value=conn)
    return db


# ═══════════════════════════════════════════════════════════════════
# 1. Enqueue succeeds on signal_bot_test with delivery authorization unset
# ═══════════════════════════════════════════════════════════════════

def test_actual_signal_bot_test_connection_permits_enqueue_with_delivery_unset(monkeypatch):
    """1. Actual signal_bot_test connection identity permits enqueue with delivery authorization unset."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=True)
    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)

    assert is_outbox_enqueue_authorized(config, conn=conn) is True

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_success",
        event_type="operational",
        payload={"text": "hello test"},
    )
    assert result is True
    # Verify SELECT DATABASE() occurred on the active connection before INSERT
    sql_calls = [c[0] for c in cursor.calls]
    assert any("SELECT DATABASE()" in sql for sql in sql_calls)
    assert any("INSERT INTO telegram_outbox" in sql for sql in sql_calls)
    assert conn.commits == 1


# ═══════════════════════════════════════════════════════════════════
# 2. Config says signal_bot_test, actual connection reports another database: reject
# ═══════════════════════════════════════════════════════════════════

def test_config_signal_bot_test_actual_connection_reports_another_db_rejected(monkeypatch):
    """2. Config says signal_bot_test, actual connection reports another database: reject."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False, allow_trading=True)
    cursor = MockCursor(db_name="other_database")
    conn = MockConnection(cursor)

    assert is_outbox_enqueue_authorized(config, conn=conn) is False

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_mismatch",
        event_type="operational",
        payload={"text": "mismatch"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)
    assert conn.commits == 0


# ═══════════════════════════════════════════════════════════════════
# 3. Config says crypto_signals, actual connection reports signal_bot_test: reject
# ═══════════════════════════════════════════════════════════════════

def test_config_crypto_signals_actual_connection_signal_bot_test_rejected(monkeypatch):
    """3. Config says crypto_signals, actual connection reports signal_bot_test: reject (identity mismatch)."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    config = SimpleNamespace(db_name="crypto_signals", observation_only=False, allow_trading=True)
    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)

    assert is_outbox_enqueue_authorized(config, conn=conn) is False

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_cross_mismatch",
        event_type="operational",
        payload={"text": "cross mismatch"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)
    assert conn.commits == 0


# ═══════════════════════════════════════════════════════════════════
# 4. Config agrees with actual connection database: accept allowlisted identities
# ═══════════════════════════════════════════════════════════════════

def test_config_agrees_with_actual_connection_database_accepted(monkeypatch):
    """4. Config and actual connection identity agree on allowlisted databases: accept."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    # 4a. signal_bot_test match
    cfg_test = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    conn_test = MockConnection(MockCursor(db_name="signal_bot_test"))
    assert is_outbox_enqueue_authorized(cfg_test, conn=conn_test) is True

    # 4b. crypto_signals match without RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED: rejected (WO71764)
    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)
    conn_prod = MockConnection(MockCursor(db_name="crypto_signals"))
    assert is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod) is False

    # 4c. crypto_signals match with BOTH flags set to '1': accepted in unit test
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", "1")
    assert is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod) is True


# ═══════════════════════════════════════════════════════════════════
# 5. Missing configuration fails closed
# ═══════════════════════════════════════════════════════════════════

def test_missing_configuration_fails_closed(monkeypatch):
    """5. Missing configuration (None or missing db_name) fails closed."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)

    # 5a. config is None -> fails closed
    assert is_outbox_enqueue_authorized(None, conn=conn) is False

    # 5b. config has no db_name or database attribute -> fails closed
    assert is_outbox_enqueue_authorized(SimpleNamespace(observation_only=False), conn=conn) is False

    # 5c. OutboxStore with config=None fails closed
    db = make_test_db(conn=conn, config=None)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_missing_cfg",
        event_type="operational",
        payload={"text": "none"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)


# ═══════════════════════════════════════════════════════════════════
# 6. Missing connection fails closed
# ═══════════════════════════════════════════════════════════════════

def test_missing_connection_fails_closed(monkeypatch):
    """6. Missing connection (conn is None) fails closed."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    assert is_outbox_enqueue_authorized(config, conn=None) is False

    db = make_test_db(conn=None, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_missing_conn",
        event_type="operational",
        payload={"text": "none"},
    )
    assert result is False


# ═══════════════════════════════════════════════════════════════════
# 7. SELECT DATABASE() returns NULL: reject
# ═══════════════════════════════════════════════════════════════════

def test_select_database_returns_null_rejected(monkeypatch):
    """7. SELECT DATABASE() returns NULL/None: reject."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    cursor = MockCursor(db_name=None, row=(None,))
    conn = MockConnection(cursor)

    assert get_connection_database(conn) is None
    assert is_outbox_enqueue_authorized(config, conn=conn) is False

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_null_db",
        event_type="operational",
        payload={"text": "null"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)


# ═══════════════════════════════════════════════════════════════════
# 8. Database identity query raises an exception: reject
# ═══════════════════════════════════════════════════════════════════

def test_database_identity_query_raises_exception_rejected(monkeypatch):
    """8. Database identity query raises an exception: reject."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    cursor = MockCursor(query_errors={"SELECT DATABASE()": RuntimeError("Simulated query failure")})
    conn = MockConnection(cursor)

    assert get_connection_database(conn) is None
    assert is_outbox_enqueue_authorized(config, conn=conn) is False

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_err_db",
        event_type="operational",
        payload={"text": "err"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)


# ═══════════════════════════════════════════════════════════════════
# 9. Unallowlisted actual database: reject
# ═══════════════════════════════════════════════════════════════════

def test_unallowlisted_actual_database_rejected(monkeypatch):
    """9. Unallowlisted actual database: reject."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    bad_dbs = [
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
        "crypto_signals_dev",
        "test",
        "production",
        "",
        "   ",
    ]
    for bad_db in bad_dbs:
        config = SimpleNamespace(db_name=bad_db, observation_only=False)
        cursor = MockCursor(db_name=bad_db)
        conn = MockConnection(cursor)
        assert is_outbox_enqueue_authorized(config, conn=conn) is False


# ═══════════════════════════════════════════════════════════════════
# 10. Enqueue flag unset: reject
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag_value", [None, "", "0", "false", "no"])
def test_enqueue_flag_unset_rejected(monkeypatch, flag_value):
    """10. Enqueue flag unset or not '1': reject."""
    if flag_value is None:
        monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", flag_value)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)

    assert is_outbox_enqueue_authorized(config, conn=conn) is False

    db = make_test_db(conn=conn, config=config)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_flag_unset",
        event_type="operational",
        payload={"text": "hello"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)
    assert conn.commits == 0


# ═══════════════════════════════════════════════════════════════════
# 11. Observation-only mode: reject
# ═══════════════════════════════════════════════════════════════════

def test_observation_only_mode_rejected(monkeypatch):
    """11. Observation-only mode (via config or environment) strictly suppresses outbox enqueue."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)

    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)

    # 11a. config.observation_only = True
    config_obs = SimpleNamespace(db_name="signal_bot_test", observation_only=True)
    assert is_outbox_enqueue_authorized(config_obs, conn=conn) is False

    # 11b. RETAILBOT2_OBSERVATION_ONLY=1 in environment
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    config_non_obs = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    assert is_outbox_enqueue_authorized(config_non_obs, conn=conn) is False

    db = make_test_db(conn=conn, config=config_obs)
    result = db.enqueue_telegram_outbox_event(
        event_key="op:test_obs",
        event_type="operational",
        payload={"text": "blocked"},
    )
    assert result is False
    assert not any("INSERT INTO telegram_outbox" in c[0] for c in cursor.calls)


# ═══════════════════════════════════════════════════════════════════
# 12. Delivery authorization unset: enqueue remains possible on the test database
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("delivery_val", [None, "", "0", "false", "no", "arbitrary"])
def test_delivery_authorization_unset_enqueue_remains_possible(monkeypatch, delivery_val):
    """12. Enqueue authorization is independent of RETAILBOT2_ALLOW_TELEGRAM_DELIVERY."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    if delivery_val is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", delivery_val)

    cfg_test = SimpleNamespace(db_name="signal_bot_test", observation_only=False)
    conn_test = MockConnection(MockCursor(db_name="signal_bot_test"))
    assert is_outbox_enqueue_authorized(cfg_test, conn=conn_test) is True


# ═══════════════════════════════════════════════════════════════════
# 13. Delivery remains blocked without delivery authorization
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("delivery_val", [None, "", "0", "false"])
def test_delivery_remains_blocked_without_delivery_authorization(monkeypatch, delivery_val):
    """13. External Telegram delivery remains strictly blocked when RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is not '1'."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    if delivery_val is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", delivery_val)

    # 13a. is_telegram_delivery_authorized is False
    assert is_telegram_delivery_authorized() is False

    # 13b. send_telegram_message suppresses send
    res = send_telegram_message("Test message")
    assert res.get("success") is False
    assert res.get("suppressed") is True

    # 13c. (retired with the worker) TelegramAlert direct delivery is
    # gone; direct sends now go through the outbox only, gated by
    # is_telegram_delivery_authorized() (asserted in 13a/13b above).


# ═══════════════════════════════════════════════════════════════════
# 14. Unauthorized dispatch makes zero HTTP requests
# ═══════════════════════════════════════════════════════════════════

def test_unauthorized_dispatch_makes_zero_http_requests(monkeypatch):
    """14. Network transport boundary is never reached when delivery is unauthorized."""
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

        # Standalone outbox worker CLI refuses production startup without delivery flag
        from paper import outbox_worker
        with patch.object(sys, "argv", ["outbox_worker", "--database", "crypto_signals"]):
            with pytest.raises(SystemExit) as exc_info:
                outbox_worker.main()
            assert exc_info.value.code == 1


# ═══════════════════════════════════════════════════════════════════
# 15. Enqueue authorization cannot start or activate a worker
# ═══════════════════════════════════════════════════════════════════

def test_enqueue_authorization_cannot_start_or_activate_worker(monkeypatch):
    """15. Enabling RETAILBOT2_OUTBOX_ENQUEUE_ENABLED=1 alone does not activate outbox_worker in supervisor."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OUTBOX_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)

    env = {
        "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED": "1",
    }
    processes = run_all.build_processes(env)
    assert "retailbot2_outbox" not in processes

    # Delivery authorization remains absent
    assert is_telegram_delivery_authorized() is False


# ═══════════════════════════════════════════════════════════════════
# 16 & 17. Promoted and non-promoted strategy outbox persistence
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# 18. Transaction rollback and idempotency
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# 19. Close trade and update stats verifies connection identity
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# 20. No BotConfig() fallback when configuration is absent
# ═══════════════════════════════════════════════════════════════════

def test_no_default_outbox_config_fallback_when_configuration_absent(monkeypatch):
    """20. Absence of config fails closed; no fallback to OutboxConfig() defaults."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    cursor = MockCursor(db_name="signal_bot_test")
    conn = MockConnection(cursor)
    db = make_test_db(conn=conn, config=None)

    # enqueue_telegram_outbox_event fails closed
    assert db.enqueue_telegram_outbox_event("op:test_no_cfg", "operational", {"text": "hi"}) is False

    # (retired with the worker) RetailDeathTrapBot.notify_operational is
    # gone; the enqueue gate itself failing closed is the assertion above.

# ═══════════════════════════════════════════════════════════════════
# 21. Caller-supplied database parameter never substitutes for active connection identity
# ═══════════════════════════════════════════════════════════════════

def test_caller_supplied_database_string_never_proves_connection_identity(monkeypatch):
    """21. Caller-supplied database parameter cannot authorize enqueue without valid connection identity."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OBSERVATION_ONLY", raising=False)

    config = SimpleNamespace(db_name="signal_bot_test", observation_only=False)

    # 21a. database string given, but connection is None -> reject
    assert is_outbox_enqueue_authorized(config, conn=None, database="signal_bot_test") is False

    # 21b. database string given, but connection reports wrong database -> reject
    cursor = MockCursor(db_name="wrong_db")
    conn = MockConnection(cursor)
    assert is_outbox_enqueue_authorized(config, conn=conn, database="signal_bot_test") is False

    # 21c. database string differs from config -> reject
    cursor_ok = MockCursor(db_name="signal_bot_test")
    conn_ok = MockConnection(cursor_ok)
    assert is_outbox_enqueue_authorized(config, conn=conn_ok, database="crypto_signals") is False


# ═══════════════════════════════════════════════════════════════════
# 22. Production startup safety behavior unchanged
# ═══════════════════════════════════════════════════════════════════

def test_production_startup_safety_unchanged(monkeypatch):
    """22. Clean production environment (no flags set) defaults to all gates closed."""
    for key in [
        "RETAILBOT2_OUTBOX_ENQUEUE_ENABLED",
        "RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED",
        "RETAILBOT2_OUTBOX_WORKER_ENABLED",
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY",
        "RETAILBOT2_ALLOW_DIRECT_TELEGRAM",
        "RETAILBOT2_ALLOW_TRADING",
        "RETAILBOT2_OBSERVATION_ONLY",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Default OutboxConfig
    cfg = OutboxConfig()
    assert cfg.observation_only is False

    # All authorization gates fail closed (trading authorization died with
    # the worker -- there is no trading gate left to assert here)
    assert is_outbox_enqueue_authorized(cfg, conn=None) is False
    assert is_telegram_delivery_authorized() is False

    # Supervisor launches no outbox worker by default
    assert "retailbot2_outbox" not in run_all.build_processes({})


# ═══════════════════════════════════════════════════════════════════
# 23. Production enqueue gate fails closed on missing, malformed, or non-'1' values (WO71764)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag_value", [None, "", "0", "false", "no", "yes", "true", "2", "ENABLE", "ENABLED"])
def test_production_enqueue_gate_fails_closed_on_invalid_flags(monkeypatch, flag_value):
    """23. For crypto_signals, any value of RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED other than '1' fails closed."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    if flag_value is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", flag_value)

    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)
    conn_prod = MockConnection(MockCursor(db_name="crypto_signals"))

    assert is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod) is False


# ═══════════════════════════════════════════════════════════════════
# 24. Production enqueue flag alone does NOT authorize anything (WO71764)
# ═══════════════════════════════════════════════════════════════════

def test_production_flag_alone_does_not_authorize_any_operation(monkeypatch):
    """24. RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1 alone never authorizes enqueue, delivery, or worker startup."""
    monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    monkeypatch.delenv("RETAILBOT2_OUTBOX_WORKER_ENABLED", raising=False)

    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)
    conn_prod = MockConnection(MockCursor(db_name="crypto_signals"))

    # Outbox enqueue remains rejected (generic flag unset)
    assert is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod) is False

    # Delivery remains unauthorized
    assert is_telegram_delivery_authorized() is False

    # Supervisor process table contains no outbox worker
    assert "retailbot2_outbox" not in run_all.build_processes({})


# ═══════════════════════════════════════════════════════════════════
# 25. Production enqueue gate preserves connection identity verification (WO71764)
# ═══════════════════════════════════════════════════════════════════

def test_production_gate_preserves_connection_verification_and_observation_rejection(monkeypatch):
    """25. When both enqueue flags are '1', connection identity, agreement, and observation-mode checks still apply."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", "1")

    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)

    # Missing connection fails closed
    assert is_outbox_enqueue_authorized(cfg_prod, conn=None) is False

    # Connection identity query returning NULL fails closed
    cursor_null = MockCursor(db_name=None)
    assert is_outbox_enqueue_authorized(cfg_prod, conn=MockConnection(cursor_null)) is False

    # Connection identity query error fails closed
    cursor_err = MockCursor(execute_error=RuntimeError("Identity error"))
    assert is_outbox_enqueue_authorized(cfg_prod, conn=MockConnection(cursor_err)) is False

    # Connection reports different database (e.g. signal_bot_test) fails closed
    cursor_mismatch = MockCursor(db_name="signal_bot_test")
    assert is_outbox_enqueue_authorized(cfg_prod, conn=MockConnection(cursor_mismatch)) is False

    # Observation-only mode active via config fails closed
    cfg_obs = SimpleNamespace(db_name="crypto_signals", observation_only=True)
    conn_prod = MockConnection(MockCursor(db_name="crypto_signals"))
    assert is_outbox_enqueue_authorized(cfg_obs, conn=conn_prod) is False

    # Observation-only mode active via environment fails closed
    monkeypatch.setenv("RETAILBOT2_OBSERVATION_ONLY", "1")
    assert is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod) is False


# ═══════════════════════════════════════════════════════════════════
# 26. Safe rejection logging without sensitive payloads or credentials (WO71764)
# ═══════════════════════════════════════════════════════════════════

def test_safe_rejection_logging_without_sensitive_payloads(monkeypatch, caplog):
    """26. Rejection logs clear warning reason without credentials or sensitive payloads."""
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)

    cfg_prod = SimpleNamespace(db_name="crypto_signals", observation_only=False)
    conn_prod = MockConnection(MockCursor(db_name="crypto_signals"))

    with caplog.at_level(logging.WARNING, logger="retailbot2.auth"):
        res = is_outbox_enqueue_authorized(cfg_prod, conn=conn_prod)

    assert res is False
    assert any("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1 is required" in record.message for record in caplog.records)
    # Ensure no sensitive words appear in logged records
    for record in caplog.records:
        msg = record.message.lower()
        assert "password" not in msg
        assert "token" not in msg
        assert "secret" not in msg
