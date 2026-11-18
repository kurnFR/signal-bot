"""Opt-in MySQL integration test for the RetailBot2 worker -> Telegram outbox boundary.

This test is deliberately pinned to 127.0.0.1:3306/signal_bot_test. It does not
read the application's MYSQL_* settings, instantiate the live worker, construct
TelegramAlert, call an exchange, or dispatch queued messages.

Run explicitly with:
    SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION=1 \
    SIGNAL_BOT_TEST_DB_USER=<local-test-db-user> \
    SIGNAL_BOT_TEST_DB_PASSWORD=<local-test-db-password> \
    PYTHONPATH=$PWD python3 -m pytest -q -s \
      tests/test_retailbot2_worker_outbox_mysql.py

The test user needs SELECT/INSERT/UPDATE/DELETE on the isolated signal_bot_test schema.
The schema must already have migrations 018 and 026 applied.
"""

import json
import logging
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import mysql.connector
import pytest

from paper.retailbot2 import DatabaseManager, RetailDeathTrapBot


TEST_DB_HOST = "127.0.0.1"
TEST_DB_PORT = 3306
TEST_DB_NAME = "signal_bot_test"


def _test_connection():
    """Connect only to the hard-pinned local integration database."""
    user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "").strip()
    if not user:
        raise AssertionError(
            "Set SIGNAL_BOT_TEST_DB_USER to a local account for signal_bot_test"
        )
    return mysql.connector.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        database=TEST_DB_NAME,
        user=user,
        password=os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", ""),
        connection_timeout=5,
        autocommit=False,
    )


def _signal(symbol, strategy):
    return {
        "symbol": symbol,
        "side": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "atr": 2.0,
        "rr": 2.0,
        "strategy": strategy,
        "strategies_triggered": [strategy],
        "confluence_score": 1,
        "reason": "isolated worker/outbox integration regression",
    }


def test_worker_persists_entry_outbox_only_for_promoted_strategy(monkeypatch):
    if os.getenv("SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION") != "1":
        pytest.skip(
            "opt-in only; set SIGNAL_BOT_RUN_RETAILBOT2_DB_INTEGRATION=1 "
            "to use the isolated local signal_bot_test database"
        )

    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    suffix = uuid.uuid4().hex[:10].upper()
    # The worker rejects unregistered strategy IDs before touching the DB.
    # Use registered strategies and restore any existing lifecycle settings.
    promoted_strategy = "BB_REV"
    unpromoted_strategy = "DBL_PAT"
    promoted_symbol = f"TB{suffix}AUSDT"
    unpromoted_symbol = f"TB{suffix}BUSDT"
    symbols = (promoted_symbol, unpromoted_symbol)
    strategies = (promoted_strategy, unpromoted_strategy)

    original_lifecycle = {}
    seed_conn = _test_connection()
    try:
        cursor = seed_conn.cursor()
        cursor.execute("SELECT DATABASE()")
        actual_database = cursor.fetchone()[0]
        assert actual_database == TEST_DB_NAME, (
            f"Refusing to test against unexpected database: {actual_database!r}"
        )

        # UUID-scoped keys make the fixture independent of operator settings.
        cursor.execute(
            "SELECT COUNT(*) FROM rdt_trades WHERE symbol IN (%s, %s)",
            symbols,
        )
        assert cursor.fetchone()[0] == 0, "unexpected pre-existing test symbols"
        cursor.execute(
            """
            SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
                   telegram_enabled, report_enabled, auto_promote_enabled
            FROM rdt_strategy_lifecycle
            WHERE strategy_id IN (%s, %s) AND mode='shadow'
            """,
            strategies,
        )
        lifecycle_columns = (
            "strategy_id", "mode", "evaluation_enabled", "paper_enabled",
            "telegram_enabled", "report_enabled", "auto_promote_enabled",
        )
        original_lifecycle = {
            row[0]: dict(zip(lifecycle_columns, row))
            for row in cursor.fetchall()
        }

        cursor.executemany(
            """
            INSERT INTO rdt_strategy_lifecycle
                (strategy_id, mode, evaluation_enabled, paper_enabled,
                 telegram_enabled, report_enabled, auto_promote_enabled)
            VALUES (%s, 'shadow', 1, 1, %s, 1, 0)
            ON DUPLICATE KEY UPDATE
                evaluation_enabled=VALUES(evaluation_enabled),
                paper_enabled=VALUES(paper_enabled),
                telegram_enabled=VALUES(telegram_enabled),
                report_enabled=VALUES(report_enabled),
                auto_promote_enabled=VALUES(auto_promote_enabled)
            """,
            [
                (promoted_strategy, 1),
                (unpromoted_strategy, 0),
            ],
        )
        seed_conn.commit()
        cursor.close()
    finally:
        seed_conn.close()

    def connect():
        return _test_connection()

    db = object.__new__(DatabaseManager)
    db.logger = logging.getLogger("test.retailbot2.worker_outbox.mysql")
    # Use the production DatabaseManager methods with fresh connections, but
    # avoid creating a worker pool or invoking any startup/network side effects.
    db.get_conn = connect

    bot = object.__new__(RetailDeathTrapBot)
    bot.db = db
    bot.config = SimpleNamespace(
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=0,
        risk_per_trade=0.01,
        allow_trading=True,
        observation_only=False,
        db_name=TEST_DB_NAME,
    )
    db.config = bot.config
    bot.trading_enabled = True
    bot.observation_only = False
    bot._shutdown_requested = False
    bot.logger = logging.getLogger("test.retailbot2.worker_outbox.mysql")
    bot.strategy_lifecycle = {}
    bot._strategy_lifecycle_reload_at = 0.0
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.last_trade_time = {}
    bot.equity_shadow = 10000.0
    bot.equity_inverse = 10000.0

    try:
        # Any accidental Telegram transport call fails the test immediately.
        with patch(
            "paper.retailbot2.requests.get",
            side_effect=AssertionError("Telegram HTTP GET is forbidden in this test"),
        ), patch(
            "paper.retailbot2.requests.post",
            side_effect=AssertionError("Telegram HTTP POST is forbidden in this test"),
        ):
            promoted_trade_id = bot._open_trade(
                _signal(promoted_symbol, promoted_strategy), "shadow"
            )
            unpromoted_trade_id = bot._open_trade(
                _signal(unpromoted_symbol, unpromoted_strategy), "shadow"
            )

        assert promoted_trade_id is not None, "promoted paper trade was not persisted"
        assert unpromoted_trade_id is not None, "unpromoted paper trade was not persisted"

        verify_conn = _test_connection()
        try:
            cursor = verify_conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, symbol, strategy, mode, status, telegram_enabled
                FROM rdt_trades WHERE id IN (%s, %s)
                """,
                (promoted_trade_id, unpromoted_trade_id),
            )
            trades = {row["id"]: row for row in cursor.fetchall()}
            assert set(trades) == {promoted_trade_id, unpromoted_trade_id}
            assert trades[promoted_trade_id]["telegram_enabled"] in (1, True)
            assert trades[unpromoted_trade_id]["telegram_enabled"] in (0, False)
            assert trades[promoted_trade_id]["strategy"] == promoted_strategy
            assert trades[unpromoted_trade_id]["strategy"] == unpromoted_strategy
            assert trades[promoted_trade_id]["status"] == "OPEN"
            assert trades[unpromoted_trade_id]["status"] == "OPEN"

            cursor.execute(
                """
                SELECT trade_id, event_type, status, payload_json
                FROM rdt_telegram_outbox
                WHERE trade_id IN (%s, %s)
                ORDER BY trade_id
                """,
                (promoted_trade_id, unpromoted_trade_id),
            )
            events = cursor.fetchall()
            assert len(events) == 1, (
                "expected exactly one outbox event, belonging only to the "
                f"promoted strategy; got {events!r}"
            )
            event = events[0]
            assert event["trade_id"] == promoted_trade_id
            assert event["event_type"] == "entry"
            assert event["status"] == "pending"
            payload = json.loads(event["payload_json"])
            assert payload["signal"]["strategy"] == promoted_strategy
            assert payload["mode"] == "shadow"
            cursor.close()
        finally:
            verify_conn.close()
    finally:
        # Remove only rows carrying this test's UUID-scoped symbols/strategy IDs.
        cleanup_conn = _test_connection()
        try:
            cursor = cleanup_conn.cursor()
            cursor.execute(
                """
                DELETE o FROM rdt_telegram_outbox o
                INNER JOIN rdt_trades t ON t.id = o.trade_id
                WHERE t.symbol IN (%s, %s)
                """,
                symbols,
            )
            cursor.execute(
                "DELETE FROM rdt_trades WHERE symbol IN (%s, %s)",
                symbols,
            )
            # Restore pre-existing operator rows; remove only rows introduced
            # by this test. This keeps the fixture safe on a populated test DB.
            for strategy_id in strategies:
                original = original_lifecycle.get(strategy_id)
                if original is None:
                    cursor.execute(
                        "DELETE FROM rdt_strategy_lifecycle "
                        "WHERE strategy_id=%s AND mode='shadow'",
                        (strategy_id,),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE rdt_strategy_lifecycle
                        SET evaluation_enabled=%s, paper_enabled=%s,
                            telegram_enabled=%s, report_enabled=%s,
                            auto_promote_enabled=%s
                        WHERE strategy_id=%s AND mode=%s
                        """,
                        (
                            original["evaluation_enabled"],
                            original["paper_enabled"],
                            original["telegram_enabled"],
                            original["report_enabled"],
                            original["auto_promote_enabled"],
                            original["strategy_id"],
                            original["mode"],
                        ),
                    )
            cleanup_conn.commit()
            cursor.close()
        finally:
            cleanup_conn.close()
