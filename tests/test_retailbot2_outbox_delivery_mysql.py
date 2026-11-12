"""Opt-in isolated-MySQL acceptance for RetailBot2 outbox delivery/recovery.

Pinned to 127.0.0.1:3306/signal_bot_test. All Telegram transport is mocked;
this test never creates TelegramAlert or contacts Telegram.
Requires migrations 018, 026, and 027 in the isolated test schema.

Run:
    SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION=1 \
    SIGNAL_BOT_TEST_DB_USER=<local-test-db-user> \
    SIGNAL_BOT_TEST_DB_PASSWORD=<local-test-db-password> \
    PYTHONPATH=$PWD python3 -m pytest -q -s \
      tests/test_retailbot2_outbox_delivery_mysql.py

The test account needs SELECT, INSERT, UPDATE, DELETE on signal_bot_test.
"""

import logging
import os
from types import SimpleNamespace
import uuid

import mysql.connector
import pytest

from paper.outbox_worker import ALLOWED_TEST_DATABASES, OutboxWorker
from paper.retailbot2 import DatabaseManager


TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"


def _connection():
    user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip()
    if not user:
        raise AssertionError(
            "Set SIGNAL_BOT_TEST_DB_USER for the isolated signal_bot_test database"
        )
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
        raise AssertionError(f"Refusing unexpected database {actual!r}")
    return conn


def _db():
    db = object.__new__(DatabaseManager)
    db.logger = logging.getLogger("test.retailbot2.outbox.delivery.mysql")
    db.get_conn = _connection
    db.config = SimpleNamespace(
        db_name=TEST_DB_NAME,
        allow_trading=True,
        observation_only=False,
    )
    return db


def _assert_outbox_idle():
    """The production dispatcher claims globally; refuse unrelated queued work."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM rdt_telegram_outbox
            WHERE status IN ('pending', 'processing')
            """
        )
        active = cursor.fetchone()[0]
        assert active == 0, (
            "Refusing dispatcher integration test: isolated test DB contains "
            f"{active} unrelated pending/processing outbox event(s)"
        )
        cursor.close()
    finally:
        conn.close()


def _seed_entry(db, suffix):
    _assert_outbox_idle()
    symbol = f"OTR{suffix}USDT"
    signal = {
        "symbol": symbol,
        "side": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "atr": 2.0,
        "rr": 2.0,
        "strategy": "BB_REV",
        "strategies_triggered": ["BB_REV"],
        "telegram_enabled": True,
        "confluence_score": 1,
        "reason": "isolated outbox delivery/recovery acceptance",
    }
    alert = {
        "signal": {
            "side": "LONG",
            "symbol": symbol,
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "rr": 2.0,
            "strategies_triggered": ["BB_REV"],
            "strategy": "BB_REV",
            "confluence_score": 1,
            "reason": "isolated outbox delivery/recovery acceptance",
        },
        "units": 2.0,
        "risk_amt": 100.0,
        "risk_pct": 1.0,
        "mode": "shadow",
    }
    trade_id = db.save_trade(signal, 2.0, "shadow", entry_alert=alert)
    assert trade_id is not None, "could not seed isolated paper trade/outbox event"
    return trade_id, symbol


def _worker(mock_response):
    return OutboxWorker(
        db_config={
            "host": TEST_DB_HOST,
            "port": TEST_DB_PORT,
            "user": os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip(),
            "password": os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
            "database": TEST_DB_NAME,
        },
        telegram_token="mock-token",
        telegram_chat_id="mock-chat",
        allowed_databases=ALLOWED_TEST_DATABASES,
        mock_transport_response=mock_response,
        poll_interval_sec=0.01,
    )


def _read_event(trade_id):
    conn = _connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, event_key, trade_id, event_type, status, attempts,
                   next_attempt_at, locked_until, claim_token, last_error, sent_at
            FROM rdt_telegram_outbox WHERE trade_id=%s
            """,
            (trade_id,),
        )
        rows = cursor.fetchall()
        assert len(rows) == 1, f"expected one outbox event, got {rows!r}"
        return rows[0]
    finally:
        conn.close()


def _set_event_due(event_id):
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE rdt_telegram_outbox
            SET next_attempt_at=UTC_TIMESTAMP()
            WHERE id=%s
            """,
            (event_id,),
        )
        assert cursor.rowcount == 1
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def _expire_lease(event_id):
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE rdt_telegram_outbox
            SET locked_until=DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 SECOND)
            WHERE id=%s AND status='processing'
            """,
            (event_id,),
        )
        assert cursor.rowcount == 1
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def _force_final_attempt_expired(event_id):
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE rdt_telegram_outbox
            SET status='processing', attempts=8,
                locked_until=DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 SECOND),
                last_error='simulated final-attempt worker interruption'
            WHERE id=%s
            """,
            (event_id,),
        )
        assert cursor.rowcount == 1
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def _cleanup(db, trade_id, strategy=None):
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM rdt_telegram_outbox WHERE trade_id=%s",
            (trade_id,),
        )
        cursor.execute("DELETE FROM rdt_trades WHERE id=%s", (trade_id,))
        if strategy:
            cursor.execute(
                "DELETE FROM rdt_strategy_stats WHERE strategy=%s AND mode='shadow'",
                (strategy,),
            )
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def test_dispatcher_retries_failed_send_then_marks_confirmed_send_sent():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    db = _db()
    trade_id = None
    worker = None
    try:
        trade_id, _symbol = _seed_entry(db, uuid.uuid4().hex[:10].upper())
        worker = _worker({
            "status_code": 200,
            "body": {"ok": False, "error_code": 500, "description": "temporary server failure"},
        })
        assert worker.session_mgr.connect_and_acquire() is True

        # Confirmed transient rejection returns the claimed event to pending.
        assert worker.process_one_event() is True
        failed = _read_event(trade_id)
        assert failed["status"] == "pending"
        assert failed["attempts"] == 1
        assert failed["locked_until"] is None
        assert failed["last_error"] == "temporary server failure"
        assert failed["sent_at"] is None

        # Backoff is tested above; make it due immediately to avoid sleeping.
        _set_event_due(failed["id"])
        worker.mock_transport_response = {
            "status_code": 200,
            "body": {"ok": True, "result": {"message_id": 777}},
        }
        assert worker.process_one_event() is True

        sent = _read_event(trade_id)
        assert sent["status"] == "sent"
        assert sent["attempts"] == 2
        assert sent["locked_until"] is None
        assert sent["last_error"] is None
        assert sent["sent_at"] is not None
    finally:
        if worker is not None:
            worker.session_mgr.close()
        if trade_id is not None:
            _cleanup(db, trade_id)


def test_dispatcher_recovers_expired_claim_after_worker_interruption():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    db = _db()
    trade_id = None
    worker = None
    try:
        trade_id, _symbol = _seed_entry(db, uuid.uuid4().hex[:10].upper())

        # Simulate a worker claiming the event and dying before sending/acking.
        abandoned = db.claim_telegram_outbox_event(lease_seconds=90)
        assert abandoned is not None
        assert abandoned["trade_id"] == trade_id
        assert abandoned["attempts"] == 1
        processing = _read_event(trade_id)
        assert processing["status"] == "processing"
        assert processing["locked_until"] is not None
        assert processing["sent_at"] is None

        # Simulate lease expiry, then let the standalone worker reclaim and send.
        _expire_lease(processing["id"])
        worker = _worker({
            "status_code": 200,
            "body": {"ok": True, "result": {"message_id": 778}},
        })
        assert worker.session_mgr.connect_and_acquire() is True
        assert worker.process_one_event() is True

        recovered = _read_event(trade_id)
        assert recovered["status"] == "sent"
        assert recovered["attempts"] == 2
        assert recovered["locked_until"] is None
        assert recovered["sent_at"] is not None
    finally:
        if worker is not None:
            worker.session_mgr.close()
        if trade_id is not None:
            _cleanup(db, trade_id)


def test_closed_trade_persists_exit_event_and_dispatches_confirmed_delivery():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    db = _db()
    trade_id = None
    strategy = f"OUTBOX_EXIT_{uuid.uuid4().hex[:12].upper()}"
    worker = None
    try:
        _assert_outbox_idle()
        symbol = f"OTX{uuid.uuid4().hex[:10].upper()}USDT"
        trade = {
            "symbol": symbol,
            "side": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "atr": 2.0,
            "rr": 2.0,
            "strategy": strategy,
            "strategies_triggered": [strategy],
            "telegram_enabled": False,
            "confluence_score": 1,
            "reason": "isolated exit outbox acceptance",
        }
        trade_id = db.save_trade(trade, 2.0, "shadow")
        assert trade_id is not None

        exit_alert = {
            "trade": {
                "id": trade_id,
                "symbol": symbol,
                "side": "LONG",
                "entry": 100.0,
                "units": 2.0,
                "strategy": strategy,
                "telegram_enabled": True,
            },
            "exit_price": 106.0,
            "reason": "TAKE_PROFIT",
            "pnl": 12.0,
            "pnl_pct": 6.0,
            "telegram_enabled": True,
        }
        assert db.close_trade_and_update_stats(
            trade_id, 106.0, "TAKE_PROFIT", 12.0, 6.0,
            strategy, "shadow", exit_alert=exit_alert,
        )

        conn = _connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT status, exit_price, net_pnl FROM rdt_trades WHERE id=%s",
                (trade_id,),
            )
            closed_trade = cursor.fetchone()
            assert closed_trade is not None
            assert closed_trade["status"] == "CLOSED"
            assert float(closed_trade["exit_price"]) == pytest.approx(106.0)
            assert float(closed_trade["net_pnl"]) == pytest.approx(12.0)
            cursor.close()
        finally:
            conn.close()

        queued = _read_event(trade_id)
        assert queued["event_type"] == "exit"
        assert queued["status"] == "pending"
        assert queued["attempts"] == 0
        assert queued["sent_at"] is None

        worker = _worker({
            "status_code": 200,
            "body": {"ok": True, "result": {"message_id": 779}},
        })
        assert worker.session_mgr.connect_and_acquire() is True
        assert worker.process_one_event() is True

        sent = _read_event(trade_id)
        assert sent["status"] == "sent"
        assert sent["event_type"] == "exit"
        assert sent["attempts"] == 1
        assert sent["sent_at"] is not None
    finally:
        if worker is not None:
            worker.session_mgr.close()
        if trade_id is not None:
            _cleanup(db, trade_id, strategy=strategy)


def test_expired_final_attempt_is_dead_lettered_not_reclaimed():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    db = _db()
    trade_id = None
    try:
        trade_id, _symbol = _seed_entry(db, uuid.uuid4().hex[:10].upper())
        event = _read_event(trade_id)
        _force_final_attempt_expired(event["id"])

        # A crashed worker on attempt 8 must not trigger a ninth send.
        assert db.claim_telegram_outbox_event(lease_seconds=90) is None
        final = _read_event(trade_id)
        assert final["status"] == "dead"
        assert final["attempts"] == 8
        assert final["locked_until"] is None
        assert final["sent_at"] is None
        assert final["last_error"] == (
            "simulated final-attempt worker interruption"
        )
    finally:
        if trade_id is not None:
            _cleanup(db, trade_id)


def test_active_lease_prevents_second_worker_from_claiming_same_event():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    first_worker_db = _db()
    second_worker_db = _db()
    trade_id = None
    try:
        trade_id, _symbol = _seed_entry(
            first_worker_db, uuid.uuid4().hex[:10].upper()
        )
        event = _read_event(trade_id)

        first_claim = first_worker_db.claim_telegram_outbox_event(
            lease_seconds=90
        )
        assert first_claim is not None
        assert first_claim["id"] == event["id"]
        assert first_claim["attempts"] == 1

        # A separate DatabaseManager/connection represents another worker.
        second_claim = second_worker_db.claim_telegram_outbox_event(
            lease_seconds=90
        )
        assert second_claim is None

        persisted = _read_event(trade_id)
        assert persisted["status"] == "processing"
        assert persisted["attempts"] == 1
        assert persisted["locked_until"] is not None
    finally:
        if trade_id is not None:
            _cleanup(first_worker_db, trade_id)


def test_stale_worker_cannot_ack_or_retry_after_event_is_reclaimed():
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip("opt-in isolated signal_bot_test integration test")

    first_worker_db = _db()
    second_worker_db = _db()
    trade_id = None
    try:
        trade_id, _symbol = _seed_entry(
            first_worker_db, uuid.uuid4().hex[:10].upper()
        )
        first_claim = first_worker_db.claim_telegram_outbox_event(lease_seconds=90)
        assert first_claim is not None
        first_token = first_claim["claim_token"]

        # Worker one stalls past its lease; worker two recovers the event.
        _expire_lease(first_claim["id"])
        second_claim = second_worker_db.claim_telegram_outbox_event(lease_seconds=90)
        assert second_claim is not None
        assert second_claim["id"] == first_claim["id"]
        second_token = second_claim["claim_token"]
        assert second_token != first_token

        # Stale worker one must not acknowledge or release worker two's claim.
        assert not first_worker_db.mark_telegram_outbox_sent(
            first_claim["id"], first_token
        )
        assert not first_worker_db.retry_telegram_outbox_event(
            first_claim["id"], first_token, first_claim["attempts"],
            "stale worker failure",
        )
        current = _read_event(trade_id)
        assert current["status"] == "processing"
        assert current["claim_token"] == second_token
        assert current["attempts"] == 2

        # Current owner can still complete its own claim.
        assert second_worker_db.mark_telegram_outbox_sent(
            second_claim["id"], second_token
        )
        completed = _read_event(trade_id)
        assert completed["status"] == "sent"
        assert completed["claim_token"] is None
    finally:
        if trade_id is not None:
            _cleanup(first_worker_db, trade_id)
