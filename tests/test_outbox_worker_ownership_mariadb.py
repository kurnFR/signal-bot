"""Isolated MariaDB integration tests for outbox worker ownership, fencing, and database authorization.

STRICTLY HARD-PINNED to 127.0.0.1:3306/signal_bot_test.
Refuses to execute if connected to any other database.
"""

import json
import logging
import os
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
    verify_database_identity,
)
from paper.retailbot2 import (
    OUTBOX_LOCK_NAME,
    DatabaseManager,
    RetailDeathTrapBot,
    TelegramAlert,
)


TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"


def _test_connection():
    user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms").strip()
    conn = mysql.connector.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        database=TEST_DB_NAME,
        user=user,
        password=os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
        connection_timeout=5,
        autocommit=False,
    )
    cursor = conn.cursor()
    cursor.execute("SELECT DATABASE()")
    actual = cursor.fetchone()[0]
    cursor.close()
    if actual != TEST_DB_NAME:
        conn.close()
        raise AssertionError(f"Refusing unexpected database {actual!r}, must be {TEST_DB_NAME!r}")
    return conn


def _clean_outbox(conn):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rdt_telegram_outbox")
    conn.commit()
    cursor.close()


def _insert_test_event(conn, event_type="entry", trade_id=None):
    if trade_id is None:
        trade_id = -999000 - int(uuid.uuid4().int % 1000)
    event_key = f"test:{trade_id}:{uuid.uuid4().hex[:12]}"
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

    def test_standalone_vs_standalone_contention(self):
        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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

    def test_standalone_vs_full_retailbot2_contention(self):
        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
        standalone = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)

        clean_conn = _test_connection()
        _clean_outbox(clean_conn)
        event_id = _insert_test_event(clean_conn)
        clean_conn.close()

        assert standalone.connect_and_acquire() is True

        try:
            # Full RetailBot2 dispatcher should detect lock held by standalone worker and return 0
            db_mgr = object.__new__(DatabaseManager)
            db_mgr.logger = logging.getLogger("test_dispatch")
            db_mgr.get_conn = _test_connection

            bot = object.__new__(RetailDeathTrapBot)
            bot.db = db_mgr
            bot.tg = object.__new__(TelegramAlert)
            bot.tg.enabled = True
            bot.logger = logging.getLogger("test_bot")

            # RetailDeathTrapBot dispatcher skips when lock is held
            delivered = bot._dispatch_telegram_outbox(limit=1)
            assert delivered == 0

            # Event should remain pending in outbox
            chk_conn = _test_connection()
            cur = chk_conn.cursor(dictionary=True)
            cur.execute("SELECT status, claim_token FROM rdt_telegram_outbox WHERE id=%s", (event_id,))
            row = cur.fetchone()
            cur.close()
            chk_conn.close()

            assert row["status"] == "pending"
            assert row["claim_token"] is None
        finally:
            standalone.close()

    def test_lock_loss_prevents_further_claims(self):
        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
        mgr = OutboxSessionManager(db_cfg, ALLOWED_TEST_DATABASES)
        assert mgr.connect_and_acquire() is True

        # Simulate connection loss by closing underlying connection
        mgr.s_lock.close()

        with pytest.raises(LockLostError):
            mgr.claim_event()

        with pytest.raises(LockLostError):
            mgr.mark_sent(1, "fake_token")


    def test_every_claim_and_transition_uses_exact_same_connection_id(self):
        conn = _test_connection()
        e1 = _insert_test_event(conn)
        e2 = _insert_test_event(conn)
        e3 = _insert_test_event(conn)
        conn.close()

        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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

    def test_full_retailbot2_fails_closed_when_lock_setup_unavailable(self):
        # Database manager lacks acquire_outbox_lock or get_conn
        db_mgr = object.__new__(DatabaseManager)
        db_mgr.logger = logging.getLogger("test_dispatch_fail_closed")
        # Do not provide acquire_outbox_lock
        bot = object.__new__(RetailDeathTrapBot)
        bot.db = db_mgr
        bot.tg = object.__new__(TelegramAlert)
        bot.tg.enabled = True
        bot.logger = logging.getLogger("test_bot")

        # Must fail closed and return 0 without touching outbox
        assert bot._dispatch_telegram_outbox(limit=1) == 0

    def test_full_retailbot2_fails_closed_when_lock_acquisition_raises(self):
        db_mgr = object.__new__(DatabaseManager)
        db_mgr.logger = logging.getLogger("test_dispatch_fail_closed")
        db_mgr.get_conn = _test_connection
        db_mgr.acquire_outbox_lock = Mock(side_effect=RuntimeError("Simulated lock acquisition error"))

        bot = object.__new__(RetailDeathTrapBot)
        bot.db = db_mgr
        bot.tg = object.__new__(TelegramAlert)
        bot.tg.enabled = True
        bot.logger = logging.getLogger("test_bot")

        # Must catch error, fail closed, and return 0
        assert bot._dispatch_telegram_outbox(limit=1) == 0
    @pytest.fixture(autouse=True)
    def clean_db(self):
        conn = _test_connection()
        _clean_outbox(conn)
        conn.close()

    def test_correct_token_acknowledges(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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

    def test_stale_token_cannot_acknowledge_retry_or_dead(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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

    def test_expired_lease_cannot_be_acknowledged(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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

    def test_new_claim_token_invalidates_previous_owner(self):
        conn = _test_connection()
        event_id = _insert_test_event(conn)
        conn.close()

        db_cfg = {
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "cms"),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        }
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
