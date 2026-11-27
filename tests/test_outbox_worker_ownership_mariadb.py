"""Isolated MariaDB integration tests for outbox worker ownership, fencing, and database authorization.

STRICTLY HARD-PINNED to 127.0.0.1:3306/signal_bot_test.
Refuses to execute if connected to any other database.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import MagicMock, Mock, patch

import mysql.connector
import pytest

from paper.outbox_worker import (
    ALLOWED_TEST_DATABASES,
    DatabaseAuthorizationError,
    LockLostError,
    OutboxSessionManager,
    OutboxWorker,
    OutboxWorkerState,
    verify_database_identity,
)
# Step 3: the worker (paper/retailbot2.py) is deleted. The advisory lock
# constant lives in paper/outbox_store.py. The three "legacy dispatcher"
# tests below are deleted with the worker: there is no in-process dispatcher
# left that could bypass the standalone worker, so the bypass they guarded
# against is impossible by construction.
from paper.outbox_store import OUTBOX_LOCK_NAME


TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"


def _test_db_config():
    return {
        "host": TEST_DB_HOST,
        "port": TEST_DB_PORT,
        "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip(),
        "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
        "database": TEST_DB_NAME,
    }


def _test_connection():
    # Never fall back to generic/production credentials. Missing explicit test
    # credentials mean the live integration test is not runnable in this shell.
    if not os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip() or not os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""):
        pytest.skip(
            "Live MariaDB integration requires explicit SIGNAL_BOT_TEST_DB_USER and "
            "SIGNAL_BOT_TEST_DB_PASSWORD; no fallback credentials are permitted."
        )
    cfg = _test_db_config()
    try:
        conn = mysql.connector.connect(
            host=cfg["host"],
            port=cfg["port"],
            database=cfg["database"],
            user=cfg["user"],
            password=cfg["password"],
            connection_timeout=2,
            autocommit=False,
        )
    except mysql.connector.Error as exc:
        # Skip only when the isolated test database is genuinely unreachable.
        # Authentication, schema, and configuration errors must fail loudly.
        if getattr(exc, "errno", None) in {2002, 2003, 2005, 2006, 2013}:
            pytest.skip(
                f"Isolated MariaDB test database {TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME} "
                f"is not reachable: {exc}"
            )
        raise
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        actual = cursor.fetchone()[0]
        cursor.close()
        if actual != TEST_DB_NAME:
            conn.close()
            raise AssertionError(f"Refusing unexpected database {actual!r}, must be {TEST_DB_NAME!r}")
        return conn
    except mysql.connector.Error:
        conn.close()
        raise


@pytest.mark.parametrize(
    ("errno", "should_skip"),
    [
        (2003, True),   # Connection refused / isolated DB unavailable
        (1045, False),  # Access denied must fail, not silently skip
    ],
)
def test_test_connection_skips_only_network_unreachable_errors(errno, should_skip):
    error = mysql.connector.Error("simulated connector failure")
    error.errno = errno
    with patch.dict(os.environ, {"SIGNAL_BOT_TEST_DB_USER": "mock-user", "SIGNAL_BOT_TEST_DB_PASSWORD": "mock-password"}), \
         patch("mysql.connector.connect", side_effect=error):
        if should_skip:
            with pytest.raises(pytest.skip.Exception):
                _test_connection()
        else:
            with pytest.raises(mysql.connector.Error):
                _test_connection()


def test_test_connection_query_failure_is_not_skipped():
    conn = Mock()
    cursor = Mock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = mysql.connector.Error("simulated SELECT DATABASE failure")
    with patch.dict(os.environ, {"SIGNAL_BOT_TEST_DB_USER": "mock-user", "SIGNAL_BOT_TEST_DB_PASSWORD": "mock-password"}), \
         patch("mysql.connector.connect", return_value=conn):
        with pytest.raises(mysql.connector.Error):
            _test_connection()
    conn.close.assert_called_once()


TEST_EVENT_KEY_PREFIX = "test:retailbot2_outbox_worker_ownership:"


def _clean_test_outbox_events(conn):
    """Delete only rows created by this integration suite, never the shared outbox."""
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM rdt_telegram_outbox
        WHERE event_key LIKE %s
           OR (
               event_key LIKE 'test:%'
               AND trade_id BETWEEN -999999 AND -999000
           )
        """,
        (f"{TEST_EVENT_KEY_PREFIX}%",),
    )
    conn.commit()
    cursor.close()


def _assert_no_unrelated_outbox_rows(conn):
    """Refuse worker tests if any non-suite outbox rows could be claimed or mutated."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM rdt_telegram_outbox
        WHERE NOT (
            event_key LIKE %s
            OR (
                event_key LIKE 'test:%'
                AND trade_id BETWEEN -999999 AND -999000
            )
        )
        """,
        (f"{TEST_EVENT_KEY_PREFIX}%",),
    )
    unrelated_count = int(cursor.fetchone()[0] or 0)
    cursor.close()
    if unrelated_count:
        raise RuntimeError(
            "Refusing outbox worker integration tests: signal_bot_test contains "
            f"{unrelated_count} unrelated outbox row(s); preserve them and use a clean isolated test database."
        )


def _insert_test_event(conn, event_type="entry", trade_id=None):
    if trade_id is None:
        trade_id = -999000 - int(uuid.uuid4().int % 1000)
    event_key = f"{TEST_EVENT_KEY_PREFIX}{trade_id}:{uuid.uuid4().hex[:12]}"
    payload = {
        "signal": {
            "symbol": "BTC/USDT",
            "side": "LONG",
            "entry": 50000.0,
            "stop": 48000.0,
            "target": 55000.0,
            "candle_close_time": "2026-11-10 14:00 UTC",
            "strategy": "MOMENTUM_TEST",
        },
        "units": 0.1,
        "risk_amt": 200.0,
        "risk_pct": 2.0,
        "mode": "shadow",
    }
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO rdt_telegram_outbox
        (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
        VALUES (%s, %s, %s, %s, 'pending', 0, UTC_TIMESTAMP())
        """,
        (event_key, trade_id, event_type, json.dumps(payload)),
    )
    event_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    return event_id


class TestDatabaseAuthorization:
    @pytest.mark.db_integration
    def test_database_authorization_signal_bot_test_accepted(self):
        conn = _test_connection()
        try:
            actual = verify_database_identity(conn, ALLOWED_TEST_DATABASES)
            assert actual == TEST_DB_NAME
        finally:
            conn.close()

    def test_database_authorization_crypto_signals_rejected(self):
        # Even if connection reported crypto_signals, test policy must reject it
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("crypto_signals",)
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(DatabaseAuthorizationError) as exc_info:
            verify_database_identity(mock_conn, ("signal_bot_test",))
        assert "crypto_signals" in str(exc_info.value)

    def test_database_authorization_unknown_db_rejected(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("unknown_hacked_db",)
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(DatabaseAuthorizationError):
            verify_database_identity(mock_conn, ("signal_bot_test",))


class TestAdvisoryLockOwnership:
    def test_outbox_lock_name_constant_is_authoritative_rdt_outbox_worker_lock(self):
        assert OUTBOX_LOCK_NAME == "rdt_outbox_worker_lock"

    @pytest.mark.db_integration
    def test_single_session_lock_and_rejection_on_contention(self):
        conn1 = _test_connection()
        conn2 = _test_connection()
        try:
            cur1 = conn1.cursor()
            cur1.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur1.fetchone()[0] == 1

            cur2 = conn2.cursor()
            cur2.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur2.fetchone()[0] == 0  # Rejection on contention

            cur1.close()
            cur2.close()
        finally:
            cur_rel = conn1.cursor()
            cur_rel.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            cur_rel.fetchall()
            cur_rel.close()
            conn1.close()
            conn2.close()

    @pytest.mark.db_integration
    def test_advisory_lock_released_when_session_closes(self):
        conn1 = _test_connection()
        conn2 = _test_connection()
        try:
            cur1 = conn1.cursor()
            cur1.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur1.fetchone()[0] == 1
            cur1.close()

            # Close connection 1 completely
            conn1.close()

            # Connection 2 should now acquire the lock immediately
            cur2 = conn2.cursor()
            cur2.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur2.fetchone()[0] == 1
            cur2.close()
        finally:
            cur_rel = conn2.cursor()
            cur_rel.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            cur_rel.fetchall()
            cur_rel.close()
            conn2.close()

    @pytest.mark.db_integration
    def test_standalone_vs_standalone_contention(self):
        db_cfg = _test_db_config()
        worker1 = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        worker2 = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)

        try:
            assert worker1.connect_and_acquire() is True
            assert worker1.check_lock_owned() is True

            # Second worker must fail to acquire and cleanly close
            assert worker2.connect_and_acquire() is False
        finally:
            worker1.close()
            worker2.close()

    @pytest.mark.db_integration
    @pytest.mark.db_integration
    def test_lock_loss_prevents_further_claims(self):
        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        # Simulate connection loss by closing underlying connection
        mgr.s_lock.close()

        with pytest.raises(LockLostError):
            mgr.claim_event()

        with pytest.raises(LockLostError):
            mgr.mark_sent(1, "fake_token")


    @pytest.mark.db_integration
    def test_every_claim_and_transition_uses_exact_same_connection_id(self):
        conn = _test_connection()
        e1 = _insert_test_event(conn)
        e2 = _insert_test_event(conn)
        e3 = _insert_test_event(conn)
        conn.close()

        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        try:
            # Query the connection ID of S_lock
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT CONNECTION_ID()")
            expected_cid = cur.fetchone()[0]
            cur.close()

            # Verify advisory lock is held by this exact connection ID
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT IS_USED_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == expected_cid
            cur.close()

            # Claim e1, acknowledge e1
            event1 = mgr.claim_event(lease_seconds=60)
            assert event1 is not None
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT CONNECTION_ID()")
            assert cur.fetchone()[0] == expected_cid
            cur.close()

            assert mgr.mark_sent(event1["id"], event1["claim_token"]) is True
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT CONNECTION_ID()")
            assert cur.fetchone()[0] == expected_cid
            cur.close()

            # Claim e2, retry e2
            event2 = mgr.claim_event(lease_seconds=60)
            assert event2 is not None
            assert mgr.mark_retry(event2["id"], event2["claim_token"], 1, "retry test") is True
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT CONNECTION_ID()")
            assert cur.fetchone()[0] == expected_cid
            cur.close()

            # Claim e3, mark dead e3
            event3 = mgr.claim_event(lease_seconds=60)
            assert event3 is not None
            assert mgr.mark_dead(event3["id"], event3["claim_token"], "dead test") is True
            cur = mgr.s_lock.cursor()
            cur.execute("SELECT CONNECTION_ID()")
            assert cur.fetchone()[0] == expected_cid
            cur.close()
        finally:
            mgr.close()

    @pytest.mark.db_integration
    def test_real_mariadb_advisory_lock_retention_during_fatal_lockout(self):
        db_cfg = _test_db_config()
        # 1. Start worker using real OutboxSessionManager connected to signal_bot_test
        worker = OutboxWorker(
            db_config=db_cfg,
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=(TEST_DB_NAME,),
            poll_interval_sec=0.05,
        )

        # 3. Enter FATAL_LOCKOUT with real surviving test process group
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid

        competing_conn = None
        worker_thread = None
        try:
            # 2. Acquire actual MariaDB advisory lock on persistent S_lock connection
            assert worker.session_mgr.connect_and_acquire() is True
            worker_conn_id = worker.session_mgr.conn_id
            assert worker_conn_id is not None

            worker.state = OutboxWorkerState.FATAL_LOCKOUT
            worker._fatal_pgid = pgid

            worker_thread = threading.Thread(target=worker.run)
            worker_thread.start()

            # Wait for worker loop to cycle
            time.sleep(0.15)
            assert worker_thread.is_alive() is True

            # 4. Use separate MariaDB connection to verify:
            competing_conn = _test_connection()
            cur = competing_conn.cursor()

            # IS_USED_LOCK identifies the worker's connection ID
            cur.execute("SELECT IS_USED_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            lock_owner = cur.fetchone()[0]
            assert lock_owner == worker_conn_id

            # Competing connection cannot acquire the lock
            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 0

            # Worker's connection remains alive
            assert worker.session_mgr.s_lock.is_connected() is True

            # 5. Request shutdown while descendant remains alive
            worker._handle_signal(signal.SIGTERM, None)
            assert worker._shutdown_requested is True

            # 6. Verify worker retains real MariaDB lock and does not exit
            time.sleep(0.2)
            assert worker_thread.is_alive() is True
            assert worker.state == OutboxWorkerState.FATAL_LOCKOUT

            cur.execute("SELECT IS_USED_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == worker_conn_id

            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 0

            # 7. Terminate the descendant
            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)

            # 8. Verify worker releases lock only after process inspection returns CONFIRMED_EMPTY
            worker_thread.join(timeout=3.0)
            assert worker_thread.is_alive() is False
            assert worker.state == OutboxWorkerState.STOPPED

            # 9. Verify competing connection can acquire the lock after clean shutdown
            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 1

            # Clean up competing lock
            cur.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            cur.fetchall()
            cur.close()
        finally:
            # 10. Clean up every process and database connection in finally blocks
            if competing_conn:
                try:
                    competing_conn.close()
                except Exception:
                    pass
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_thread and worker_thread.is_alive():
                worker._shutdown_requested = True
                worker._fatal_pgid = None
                worker_thread.join(timeout=2.0)
            worker.session_mgr.close()

    @pytest.mark.db_integration
    def test_real_mariadb_advisory_lock_retained_on_actual_os_sigterm(self):
        # Start background surviving descendant in its own PGID
        bg_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pgid = bg_proc.pid

        # Child worker script running in independent OS process with registered signal handlers
        worker_script = f"""
import os, signal, sys, time
from paper.outbox_worker import OutboxWorker, OutboxWorkerState

db_cfg = {_test_db_config()!r}

fatal_pgid = int(sys.argv[1])
worker = OutboxWorker(
    db_config=db_cfg,
    telegram_token="test_token",
    telegram_chat_id="test_chat",
    allowed_databases=("{TEST_DB_NAME}",),
    poll_interval_sec=0.05,
)
worker.setup_signals()
assert worker.session_mgr.connect_and_acquire() is True
worker.state = OutboxWorkerState.FATAL_LOCKOUT
worker._fatal_pgid = fatal_pgid

sys.stdout.write(f"READY {{os.getpid()}} {{worker.session_mgr.conn_id}}\\n")
sys.stdout.flush()

exit_code = worker.run()
sys.stdout.write(f"EXIT {{exit_code}}\\n")
sys.stdout.flush()
sys.exit(exit_code)
"""

        worker_proc = None
        competing_conn = None
        try:
            worker_proc = subprocess.Popen(
                [sys.executable, "-c", worker_script, str(pgid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # 1. Wait for READY line from worker process
            ready_line = worker_proc.stdout.readline().strip()
            assert ready_line.startswith("READY"), f"Unexpected worker output: {ready_line}"
            _, worker_pid_str, worker_conn_id_str = ready_line.split()
            worker_pid = int(worker_pid_str)
            worker_conn_id = int(worker_conn_id_str)
            assert worker_pid == worker_proc.pid

            # 2. Check lock ownership from competing connection
            competing_conn = _test_connection()
            cur = competing_conn.cursor()

            cur.execute("SELECT IS_USED_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == worker_conn_id

            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 0

            # 3. Send ACTUAL OS SIGTERM using os.kill(worker_pid, signal.SIGTERM)
            os.kill(worker_pid, signal.SIGTERM)

            # 4. Verify worker remains alive and retains real MariaDB advisory lock
            time.sleep(0.3)
            assert worker_proc.poll() is None, "Worker process exited prematurely on SIGTERM during fatal lockout"

            cur.execute("SELECT IS_USED_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == worker_conn_id

            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 0

            # 5. Terminate descendant process
            os.killpg(pgid, signal.SIGKILL)
            bg_proc.wait(timeout=3.0)

            # 6. Verify clean worker exit and lock release
            retcode = worker_proc.wait(timeout=4.0)
            assert retcode == 0

            cur.execute("SELECT GET_LOCK(%s, 0)", (OUTBOX_LOCK_NAME,))
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            cur.fetchall()
            cur.close()
        finally:
            if competing_conn:
                try:
                    competing_conn.close()
                except Exception:
                    pass
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            bg_proc.wait()
            if worker_proc and worker_proc.poll() is None:
                try:
                    os.kill(worker_proc.pid, signal.SIGKILL)
                    worker_proc.wait(timeout=2.0)
                except OSError:
                    pass
    @pytest.fixture(autouse=True)
    def isolate_test_outbox_rows(self, request):
        if request.node.name in (
            "test_outbox_lock_name_constant_is_authoritative_rdt_outbox_worker_lock",
            "test_legacy_dispatcher_is_disabled_without_lock_api",
            "test_legacy_dispatcher_never_attempts_lock_acquisition",
        ):
            yield
            return
        # Clean only this suite's reserved event-key namespace and legacy
        # sentinel IDs. Preserve every unrelated outbox row in signal_bot_test.
        conn = _test_connection()
        try:
            _clean_test_outbox_events(conn)
            _assert_no_unrelated_outbox_rows(conn)
        finally:
            conn.close()
        yield
        conn = _test_connection()
        try:
            _clean_test_outbox_events(conn)
        finally:
            conn.close()

    @pytest.mark.db_integration
    def test_cleanup_preserves_unrelated_outbox_rows(self):
        conn = _test_connection()
        owned_id = None
        unrelated_id = None
        try:
            owned_id = _insert_test_event(conn)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO rdt_telegram_outbox
                (event_key, trade_id, event_type, payload_json, status, attempts, next_attempt_at)
                VALUES (%s, %s, 'entry', %s, 'pending', 0, UTC_TIMESTAMP())
                """,
                (
                    f"cleanup-guard:{uuid.uuid4().hex}",
                    987654321,
                    json.dumps({"test": "must be preserved"}),
                ),
            )
            unrelated_id = cursor.lastrowid
            conn.commit()
            cursor.close()

            with pytest.raises(RuntimeError, match="unrelated outbox row"):
                _assert_no_unrelated_outbox_rows(conn)

            _clean_test_outbox_events(conn)

            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM rdt_telegram_outbox WHERE id IN (%s, %s)",
                (owned_id, unrelated_id),
            )
            remaining = {row[0] for row in cursor.fetchall()}
            cursor.close()
            assert owned_id not in remaining
            assert unrelated_id in remaining
        finally:
            if unrelated_id is not None:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM rdt_telegram_outbox WHERE id=%s", (unrelated_id,))
                conn.commit()
                cursor.close()
            conn.close()

    @pytest.mark.db_integration
    def test_correct_token_acknowledges(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        try:
            event = mgr.claim_event(lease_seconds=60)
            assert event is not None
            assert event["id"] == event_id
            token = event["claim_token"]

            ok = mgr.mark_sent(event_id, token)
            assert ok is True

            chk_conn = _test_connection()
            cur = chk_conn.cursor(dictionary=True)
            cur.execute("SELECT status, sent_at, claim_token FROM rdt_telegram_outbox WHERE id=%s", (event_id,))
            row = cur.fetchone()
            cur.close()
            chk_conn.close()

            assert row["status"] == "sent"
            assert row["sent_at"] is not None
            assert row["claim_token"] is None
        finally:
            mgr.close()

    @pytest.mark.db_integration
    def test_stale_token_cannot_acknowledge_retry_or_dead(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        try:
            event = mgr.claim_event(lease_seconds=60)
            stale_token = "deadbeef" * 4

            # Stale token must fail
            assert mgr.mark_sent(event_id, stale_token) is False
            assert mgr.mark_retry(event_id, stale_token, 1, "test err") is False
            assert mgr.mark_dead(event_id, stale_token, "test err") is False

            # Correct token can still acknowledge
            assert mgr.mark_sent(event_id, event["claim_token"]) is True
        finally:
            mgr.close()

    @pytest.mark.db_integration
    def test_expired_lease_cannot_be_acknowledged(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        try:
            event = mgr.claim_event(lease_seconds=60)
            token = event["claim_token"]

            # Manually expire the lease in DB
            mod_conn = _test_connection()
            cur = mod_conn.cursor()
            cur.execute(
                "UPDATE rdt_telegram_outbox SET locked_until=DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 SECOND) WHERE id=%s",
                (event_id,)
            )
            mod_conn.commit()
            cur.close()
            mod_conn.close()

            # mark_sent must be rejected because locked_until is expired
            assert mgr.mark_sent(event_id, token) is False
        finally:
            mgr.close()

    @pytest.mark.db_integration
    def test_new_claim_token_invalidates_previous_owner(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = _test_db_config()
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        try:
            event1 = mgr.claim_event(lease_seconds=1)
            token1 = event1["claim_token"]

            # Expire lease
            mod_conn = _test_connection()
            cur = mod_conn.cursor()
            cur.execute(
                "UPDATE rdt_telegram_outbox SET locked_until=DATE_SUB(UTC_TIMESTAMP(), INTERVAL 5 SECOND) WHERE id=%s",
                (event_id,)
            )
            mod_conn.commit()
            cur.close()
            mod_conn.close()

            # Second claim acquires event with new token
            event2 = mgr.claim_event(lease_seconds=60)
            token2 = event2["claim_token"]
            assert token1 != token2

            # Token 1 must be rejected
            assert mgr.mark_sent(event_id, token1) is False

            # Token 2 succeeds
            assert mgr.mark_sent(event_id, token2) is True
        finally:
            mgr.close()
