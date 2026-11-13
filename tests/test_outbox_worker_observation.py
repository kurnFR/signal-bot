"""Focused test suite for RetailBot2 Outbox Worker observation mode and credential resolution (WO71764).

Covers:
1. resolve_worker_db_credentials strict fail-closed policy for test database:
   - Requires SIGNAL_BOT_TEST_DB_USER and SIGNAL_BOT_TEST_DB_PASSWORD.
   - Fails closed on missing or empty credentials without falling back to MYSQL_USER, MYSQL_PASSWORD, or 'cms'.
   - Never leaks credentials in exception messages.
   - Preserves credential resolution for crypto_signals.
2. OutboxWorker observation mode:
   - Observation mode constructor flag and OBSERVING state.
   - Queue inspection via inspect_queue() queries only schema-supported columns (no updated_at).
   - Zero event claims, zero row mutations, zero transport subprocess calls, zero HTTP requests.
   - No requirement for TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.
   - Graceful shutdown on SIGINT/SIGTERM cleanly releases advisory lock.
   - Fails closed on advisory lock loss, schema failure, and database identity mismatch.
3. Standalone CLI --observation behavior:
   - Observation mode on crypto_signals bypasses delivery authorization requirement.
   - Delivery mode on crypto_signals still requires RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1.
   - Standalone CLI fails closed when dedicated test credentials are absent.
"""

import logging
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import pytest

from notifications.telegram_notifier import is_telegram_delivery_authorized
from paper.retailbot2 import (
    BotConfig,
    is_outbox_enqueue_authorized,
    is_trading_authorized,
)
import run_all
from paper.outbox_worker import (
    ALLOWED_PROD_DATABASES,
    ALLOWED_TEST_DATABASES,
    DatabaseAuthorizationError,
    LockLostError,
    OutboxSessionManager,
    OutboxWorker,
    OutboxWorkerState,
    SchemaVerificationError,
    is_production_outbox_observation_authorized,
    main as worker_main,
    resolve_worker_db_credentials,
)


# ═══════════════════════════════════════════════════════════════════
# 1. Credential Resolution Tests
# ═══════════════════════════════════════════════════════════════════

def test_resolve_test_credentials_success(monkeypatch):
    """signal_bot_test resolves explicit dedicated test credentials."""
    monkeypatch.setenv("SIGNAL_BOT_TEST_DB_USER", "test_user_ok")
    monkeypatch.setenv("SIGNAL_BOT_TEST_DB_PASSWORD", "test_pass_ok")
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "wrong_pass")

    creds = resolve_worker_db_credentials("signal_bot_test")
    assert creds == {"user": "test_user_ok", "password": "test_pass_ok"}


@pytest.mark.parametrize(
    "user_val,pass_val",
    [
        (None, "some_pass"),
        ("", "some_pass"),
        ("   ", "some_pass"),
        ("test_user", None),
        ("test_user", ""),
        ("test_user", "   "),
        (None, None),
        ("", ""),
    ],
)
def test_resolve_test_credentials_fails_closed_no_fallbacks(monkeypatch, user_val, pass_val):
    """Missing or empty dedicated test credentials fail closed without falling back to MYSQL_USER or cms."""
    if user_val is None:
        monkeypatch.delenv("SIGNAL_BOT_TEST_DB_USER", raising=False)
    else:
        monkeypatch.setenv("SIGNAL_BOT_TEST_DB_USER", user_val)

    if pass_val is None:
        monkeypatch.delenv("SIGNAL_BOT_TEST_DB_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("SIGNAL_BOT_TEST_DB_PASSWORD", pass_val)

    # Set generic env vars that MUST NOT be used as fallbacks
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "generic_secret")

    with pytest.raises(ValueError) as exc_info:
        resolve_worker_db_credentials("signal_bot_test")

    err_msg = str(exc_info.value)
    assert "SIGNAL_BOT_TEST_DB_USER" in err_msg
    assert "SIGNAL_BOT_TEST_DB_PASSWORD" in err_msg
    assert "generic or default credential fallbacks are strictly prohibited" in err_msg
    # Ensure no credentials leaked in error message
    assert "generic_secret" not in err_msg
    assert "some_pass" not in err_msg


def test_resolve_prod_credentials_preserves_existing_behavior(monkeypatch):
    """crypto_signals in delivery mode (observation=False) preserves standard MYSQL_USER / MYSQL_PASSWORD resolution."""
    monkeypatch.setenv("MYSQL_USER", "prod_user")
    monkeypatch.setenv("MYSQL_PASSWORD", "prod_secret")

    creds = resolve_worker_db_credentials("crypto_signals", observation=False)
    assert creds == {"user": "prod_user", "password": "prod_secret"}

    # Default fallback for prod user is 'cms' if unset
    monkeypatch.delenv("MYSQL_USER", raising=False)
    creds_default = resolve_worker_db_credentials("crypto_signals", observation=False)
    assert creds_default["user"] == "cms"


def test_resolve_prod_observation_credentials_success(monkeypatch):
    """crypto_signals in observation mode resolves dedicated observation credentials when authorized."""
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", "obs_user_ok")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", "obs_pass_ok")
    # Set generic env vars that must NOT be used
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "wrong_generic_pass")

    assert is_production_outbox_observation_authorized() is True
    creds = resolve_worker_db_credentials("crypto_signals", observation=True)
    assert creds == {"user": "obs_user_ok", "password": "obs_pass_ok"}


@pytest.mark.parametrize(
    "auth_val",
    [None, "", "0", "false", "no", "yes", "true", "2", "ENABLE", "ENABLED", "1 "],
)
def test_resolve_prod_observation_fails_closed_when_auth_flag_unset_or_invalid(monkeypatch, auth_val):
    """Production observation fails closed if RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED is not exact '1'."""
    if auth_val is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", raising=False)
    else:
        # Note: if stripped string is '1', it passes; test non-'1' values
        if auth_val.strip() == "1":
            # Pass through stripped '1' is accepted; test invalid only
            return
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", auth_val)

    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", "obs_user")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", "obs_pass")
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "generic_pass")

    assert is_production_outbox_observation_authorized() is False

    with pytest.raises(ValueError) as exc_info:
        resolve_worker_db_credentials("crypto_signals", observation=True)

    err = str(exc_info.value)
    assert "RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1 is required" in err
    assert "cms" not in err


@pytest.mark.parametrize(
    "user_val,pass_val",
    [
        (None, "obs_pass"),
        ("", "obs_pass"),
        ("   ", "obs_pass"),
        ("obs_user", None),
        ("obs_user", ""),
        ("obs_user", "   "),
        (None, None),
        ("", ""),
    ],
)
def test_resolve_prod_observation_fails_closed_when_credentials_missing_or_empty(monkeypatch, user_val, pass_val):
    """Production observation fails closed if dedicated user or password is missing/empty."""
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "1")

    if user_val is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", user_val)

    if pass_val is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", pass_val)

    # Set generic fallbacks that MUST NOT be used
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "generic_secret")

    with pytest.raises(ValueError) as exc_info:
        resolve_worker_db_credentials("crypto_signals", observation=True)

    err = str(exc_info.value)
    assert "RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER" in err
    assert "RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD" in err
    assert "generic or default credential fallbacks are strictly prohibited" in err
    # Ensure no credentials leaked in error message
    assert "generic_secret" not in err
    assert "obs_pass" not in err


def test_generic_mysql_credentials_cannot_satisfy_prod_observation(monkeypatch):
    """Generic MYSQL credentials (or default 'cms') can never satisfy production observation."""
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", raising=False)
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "cms_pass")

    with pytest.raises(ValueError) as exc_info:
        resolve_worker_db_credentials("crypto_signals", observation=True)

    assert "RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER" in str(exc_info.value)


def test_prod_observation_flag_does_not_authorize_enqueue_trading_or_delivery(monkeypatch):
    """RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED=1 alone never authorizes enqueue, trading, or delivery."""
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "1")
    monkeypatch.delenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TRADING", raising=False)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.delenv("RETAILBOT2_OUTBOX_WORKER_ENABLED", raising=False)

    cfg = SimpleNamespace(db_name="crypto_signals", observation_only=False)

    # Outbox enqueue remains rejected
    assert is_outbox_enqueue_authorized(cfg, conn=None) is False

    # Trading remains unauthorized
    assert is_trading_authorized(cfg) is False

    # Telegram delivery remains unauthorized
    assert is_telegram_delivery_authorized() is False

    # Supervisor process table contains no outbox worker
    assert "retailbot2_outbox" not in run_all.build_processes({})


# ═══════════════════════════════════════════════════════════════════
# 2. Mock DB Infrastructure for Worker Observation Tests
# ═══════════════════════════════════════════════════════════════════

class ObservationMockCursor:
    def __init__(self, db_name="signal_bot_test", lock_held=1, status_rows=None, due_count=0, dictionary=False):
        self.db_name = db_name
        self.lock_held = lock_held
        self.status_rows = status_rows if status_rows is not None else [
            {"status": "pending", "count": 3, "oldest_created_at": "2026-11-13 09:00:00"},
            {"status": "sent", "count": 10, "oldest_created_at": "2026-11-13 08:00:00"},
        ]
        self.due_count = due_count
        self.dictionary = dictionary
        self.executed_queries = []
        self.closed = False
        self._current_result = None

    def execute(self, sql, params=None):
        self.executed_queries.append((sql, params))
        sql_u = sql.upper()
        if "SELECT DATABASE()" in sql_u:
            self._current_result = [{"db": self.db_name}] if self.dictionary else [(self.db_name,)]
        elif "INFORMATION_SCHEMA.TABLES" in sql_u:
            self._current_result = [{"TABLE_NAME": "rdt_telegram_outbox"}] if self.dictionary else [("rdt_telegram_outbox",)]
        elif "INFORMATION_SCHEMA.COLUMNS" in sql_u:
            cols = [
                "id", "event_key", "trade_id", "event_type", "payload_json", "status",
                "attempts", "next_attempt_at", "locked_until", "claim_token", "last_error",
                "created_at", "sent_at",
            ]
            self._current_result = [{"COLUMN_NAME": c} for c in cols] if self.dictionary else [(c,) for c in cols]
        elif "GET_LOCK" in sql_u:
            self._current_result = [{"lock": 1}] if self.dictionary else [(1,)]
        elif "RELEASE_LOCK" in sql_u:
            self._current_result = [{"lock": 1}] if self.dictionary else [(1,)]
        elif "IS_USED_LOCK" in sql_u:
            self._current_result = [{"lock_held": self.lock_held}] if self.dictionary else [(self.lock_held,)]
        elif "CONNECTION_ID" in sql_u:
            self._current_result = [{"cid": 42}] if self.dictionary else [(42,)]
        elif "GROUP BY STATUS" in sql_u:
            self._current_result = list(self.status_rows)
        elif "DUE_COUNT" in sql_u:
            self._current_result = [{"due_count": self.due_count}]
        elif "SELECT 1" in sql_u:
            self._current_result = [{"alive": 1}] if self.dictionary else [(1,)]
        else:
            self._current_result = []

    def fetchone(self):
        if self._current_result and len(self._current_result) > 0:
            return self._current_result[0]
        return None

    def fetchall(self):
        return list(self._current_result or [])

    def close(self):
        self.closed = True


class ObservationMockConnection:
    def __init__(self, db_name="signal_bot_test", lock_held=1, status_rows=None, due_count=0):
        self.db_name = db_name
        self.lock_held = lock_held
        self.status_rows = status_rows
        self.due_count = due_count
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.executed_queries = []

    def cursor(self, dictionary=False, **kwargs):
        cur = ObservationMockCursor(
            db_name=self.db_name,
            lock_held=self.lock_held,
            status_rows=self.status_rows,
            due_count=self.due_count,
            dictionary=dictionary,
        )
        old_execute = cur.execute
        def tracked_execute(sql, params=None):
            self.executed_queries.append((sql, params))
            return old_execute(sql, params)
        cur.execute = tracked_execute
        return cur

    def is_connected(self):
        return not self.closed

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


# ═══════════════════════════════════════════════════════════════════
# 3. Session Manager inspect_queue() Tests
# ═══════════════════════════════════════════════════════════════════

def test_inspect_queue_queries_verified_columns_only():
    """inspect_queue() executes read-only query using verified schema columns without updated_at."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test", due_count=2)

    with patch("mysql.connector.connect", return_value=mock_conn):
        mgr = OutboxSessionManager(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
        )
        assert mgr.connect_and_acquire() is True

        summary = mgr.inspect_queue()

        assert summary["due_count"] == 2
        assert summary["status_counts"]["pending"] == 3
        assert summary["status_counts"]["sent"] == 10

        # Verify SQL queries executed:
        # None must contain FOR UPDATE, INSERT, UPDATE, DELETE, or updated_at
        all_sql = " ".join(q[0].upper() for q in mock_conn.executed_queries)
        assert "UPDATED_AT" not in all_sql
        assert "FOR UPDATE" not in all_sql
        assert "UPDATE RDT_TELEGRAM_OUTBOX" not in all_sql
        assert "DELETE FROM RDT_TELEGRAM_OUTBOX" not in all_sql


def test_inspect_queue_fails_closed_if_lock_lost():
    """inspect_queue() raises LockLostError if advisory lock is lost."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test", lock_held=1)

    with patch("mysql.connector.connect", return_value=mock_conn):
        mgr = OutboxSessionManager(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
        )
        assert mgr.connect_and_acquire() is True
        mock_conn.lock_held = 0

        with pytest.raises(LockLostError) as exc_info:
            mgr.inspect_queue()
        assert "advisory lock ownership lost" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════
# 4. OutboxWorker Observation Mode Invariants
# ═══════════════════════════════════════════════════════════════════

def test_worker_observation_mode_initialization_and_state():
    """OutboxWorker with observation_mode=True sets OBSERVING state and requires no telegram tokens."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test")

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            observation_mode=True,
            # Explicitly omit telegram_token and telegram_chat_id
        )

        assert worker.observation_mode is True
        assert worker.telegram_token == ""
        assert worker.telegram_chat_id == ""


def test_worker_observation_mode_process_one_event_zero_mutations():
    """In observation mode, process_one_event() inspects queue and never claims or mutates rows."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test", due_count=5)

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            observation_mode=True,
        )
        assert worker.session_mgr.connect_and_acquire() is True

        worker.session_mgr.claim_event = Mock(side_effect=AssertionError("claim_event MUST NOT be called in observation mode"))
        worker.session_mgr.mark_sent = Mock(side_effect=AssertionError("mark_sent MUST NOT be called in observation mode"))
        worker.session_mgr.mark_retry = Mock(side_effect=AssertionError("mark_retry MUST NOT be called in observation mode"))
        worker.session_mgr.mark_dead = Mock(side_effect=AssertionError("mark_dead MUST NOT be called in observation mode"))

        with patch("paper.outbox_worker.dispatch_event_via_subprocess") as mock_dispatch:
            mock_dispatch.side_effect = AssertionError("dispatch_event_via_subprocess MUST NOT be called in observation mode")

            processed = worker.process_one_event()

            assert processed is False
            assert mock_dispatch.call_count == 0
            assert worker.session_mgr.claim_event.call_count == 0
            assert mock_conn.commits == 0


def test_worker_observation_run_sets_observing_state_and_shuts_down_cleanly():
    """Worker run() loop sets OBSERVING state and exits cleanly on shutdown signal releasing lock."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test")

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            poll_interval_sec=0.01,
            observation_mode=True,
        )

        observed_states = []

        def fake_process():
            observed_states.append(worker.state)
            worker._shutdown_requested = True
            return False

        worker.process_one_event = fake_process

        exit_code = worker.run()

        assert exit_code == 0
        assert OutboxWorkerState.OBSERVING in observed_states
        assert worker.state == OutboxWorkerState.STOPPED
        # Verify advisory lock released and connection closed
        assert mock_conn.closed is True


def test_worker_observation_fails_closed_on_lock_loss():
    """Worker observation mode fails closed when lock heartbeat fails."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test")

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            poll_interval_sec=0.001,
            observation_mode=True,
        )

        original_connect_and_acquire = worker.session_mgr.connect_and_acquire

        def failing_connect_and_heartbeat():
            res = original_connect_and_acquire()
            # Once acquired, simulate lost heartbeat
            worker.session_mgr.heartbeat = Mock(return_value=False)
            return res

        worker.session_mgr.connect_and_acquire = failing_connect_and_heartbeat

        with pytest.raises(LockLostError):
            worker.run()


def test_worker_observation_fails_closed_on_database_identity_mismatch():
    """Worker observation mode fails closed if connection reports unauthorized database."""
    # Connected DB is crypto_signals, but worker only allows ALLOWED_TEST_DATABASES
    mock_conn = ObservationMockConnection(db_name="crypto_signals")

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            observation_mode=True,
        )

        assert worker.session_mgr.connect_and_acquire() is False
        assert worker.run() == 1


def test_worker_observation_fails_closed_on_schema_verification_failure():
    """Worker observation mode fails closed if rdt_telegram_outbox is missing required columns."""
    mock_conn = ObservationMockConnection(db_name="signal_bot_test")
    # Simulate missing claim_token column
    old_cursor = mock_conn.cursor
    def corrupt_cursor(**kwargs):
        cur = old_cursor(**kwargs)
        orig_execute = cur.execute
        def modified_execute(sql, params=None):
            orig_execute(sql, params)
            if "INFORMATION_SCHEMA.COLUMNS" in sql.upper():
                # Omit claim_token
                cur._current_result = [r for r in cur._current_result if (r[0] != "claim_token" if isinstance(r, tuple) else r.get("COLUMN_NAME") != "claim_token")]
        cur.execute = modified_execute
        return cur
    mock_conn.cursor = corrupt_cursor

    with patch("mysql.connector.connect", return_value=mock_conn):
        worker = OutboxWorker(
            db_config={"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "signal_bot_test"},
            allowed_databases=ALLOWED_TEST_DATABASES,
            observation_mode=True,
        )

        with pytest.raises(SchemaVerificationError) as exc_info:
            worker.session_mgr.connect_and_acquire()

        assert "Required outbox column(s) missing from 'rdt_telegram_outbox': claim_token" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════
# 5. CLI --observation and Credential Gate Tests
# ═══════════════════════════════════════════════════════════════════

def test_cli_observation_on_crypto_signals_with_valid_auth_and_creds(monkeypatch):
    """Running CLI with --database crypto_signals --observation succeeds with dedicated observation creds."""
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", "obs_user")
    monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", "obs_pass")
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "generic_pass")

    with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals", "--observation"]), \
         patch("paper.outbox_worker.OutboxWorker") as mock_worker_cls:
        instance = mock_worker_cls.return_value
        instance.run.return_value = 0

        with pytest.raises(SystemExit) as exc_info:
            worker_main()

        assert exc_info.value.code == 0
        mock_worker_cls.assert_called_once()
        kwargs = mock_worker_cls.call_args[1]
        assert kwargs["observation_mode"] is True
        assert kwargs["db_config"]["user"] == "obs_user"
        assert kwargs["db_config"]["password"] == "obs_pass"
        assert kwargs["telegram_token"] == ""
        assert kwargs["telegram_chat_id"] == ""


@pytest.mark.parametrize(
    "auth_flag,user_val,pass_val",
    [
        (None, "obs_user", "obs_pass"),
        ("0", "obs_user", "obs_pass"),
        ("1", None, "obs_pass"),
        ("1", "", "obs_pass"),
        ("1", "obs_user", None),
        ("1", "obs_user", ""),
        ("1", None, None),
    ],
)
def test_cli_observation_on_crypto_signals_fails_closed_without_auth_or_creds(
    monkeypatch, auth_flag, user_val, pass_val
):
    """Running CLI with --database crypto_signals --observation aborts with code 1 if auth flag or creds are missing."""
    if auth_flag is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED", auth_flag)

    if user_val is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_USER", user_val)

    if pass_val is None:
        monkeypatch.delenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_PRODUCTION_OBSERVATION_DB_PASSWORD", pass_val)

    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "generic_pass")

    with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals", "--observation"]):
        with pytest.raises(SystemExit) as exc_info:
            worker_main()

        assert exc_info.value.code == 1


def test_cli_delivery_mode_on_crypto_signals_requires_delivery_gate(monkeypatch):
    """Running CLI with --database crypto_signals without --observation strictly requires delivery flag."""
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    monkeypatch.setenv("MYSQL_USER", "prod_user")
    monkeypatch.setenv("MYSQL_PASSWORD", "prod_pass")

    with patch("sys.argv", ["outbox_worker", "--database", "crypto_signals"]):
        with pytest.raises(SystemExit) as exc_info:
            worker_main()

        assert exc_info.value.code == 1


def test_cli_signal_bot_test_aborts_when_test_credentials_absent(monkeypatch):
    """Running CLI with --database signal_bot_test aborts with code 1 if dedicated test credentials are unset."""
    monkeypatch.delenv("SIGNAL_BOT_TEST_DB_USER", raising=False)
    monkeypatch.delenv("SIGNAL_BOT_TEST_DB_PASSWORD", raising=False)
    monkeypatch.setenv("MYSQL_USER", "cms")
    monkeypatch.setenv("MYSQL_PASSWORD", "cms_pass")

    with patch("sys.argv", ["outbox_worker", "--database", "signal_bot_test", "--observation"]):
        with pytest.raises(SystemExit) as exc_info:
            worker_main()

        assert exc_info.value.code == 1
