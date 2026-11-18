"""
Tests for Work Order 71873: Deprecate Binance Futures Liquidation WebSocket Collector.

Verifies:
1. run_collectors.py does not import or invoke ws_liquidations_run.
2. Other collector startup functions (ws_kline_main, funding_poller_run, oi_poller_run) remain registered.
3. Shutdown and cancellation behavior in run_collectors.main() remain correct.
4. Importing run_collectors or collectors.ws_liquidations does not start background connections.
5. collectors.ws_liquidations remains importable and contains clear deprecation notice.
6. collectors.ws_liquidations.run() logs a deprecation warning.
7. Database safety: Verifies isolation with signal_bot_test and schema.sql preservation.
"""
import asyncio
import inspect
import logging
import os
from unittest.mock import patch, AsyncMock
import pytest


def test_run_collectors_does_not_import_ws_liquidations():
    """Verify run_collectors namespace contains no reference to ws_liquidations or its run function."""
    import run_collectors
    assert not hasattr(run_collectors, "ws_liquidations_run"), (
        "Safety failure: ws_liquidations_run is still imported in run_collectors!"
    )
    assert not hasattr(run_collectors, "ws_liquidations"), (
        "Safety failure: ws_liquidations module is still referenced in run_collectors!"
    )

    source = inspect.getsource(run_collectors)
    assert "ws_liquidations_run" not in source, "Source still contains ws_liquidations_run"
    assert "from collectors.ws_liquidations" not in source, (
        "Source still imports from collectors.ws_liquidations"
    )


def test_ws_liquidations_remains_importable_and_deprecated():
    """Verify collectors.ws_liquidations is importable and has deprecation notice."""
    import collectors.ws_liquidations as ws_liq

    assert hasattr(ws_liq, "run"), "ws_liquidations must retain run() function"
    assert hasattr(ws_liq, "event_to_row"), "ws_liquidations must retain event_to_row() helper"
    assert "DEPRECATED" in ws_liq.__doc__, "Module docstring must contain DEPRECATED notice"
    assert "run_collectors.py" in ws_liq.__doc__, (
        "Module docstring must document deprecation from run_collectors.py"
    )


def test_ws_liquidations_run_logs_deprecation_warning(caplog):
    """Verify that calling ws_liquidations.run() logs a deprecation warning before connecting."""
    import collectors.ws_liquidations as ws_liq

    async def run_briefly():
        with caplog.at_level(logging.WARNING, logger="ws_liquidations"):
            with patch("websockets.connect", side_effect=asyncio.CancelledError):
                task = asyncio.create_task(ws_liq.run())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(run_briefly())
    assert any("DEPRECATED" in record.message for record in caplog.records), (
        "Expected deprecation warning logged on ws_liquidations.run()"
    )


def test_run_collectors_main_futures_enabled_schedules_intended_tasks():
    """
    When ENABLE_FUTURES is True, run_collectors.main() must schedule:
    - ws_kline_main()
    - funding_poller_run()
    - oi_poller_run()
    And must NOT schedule any liquidation collector.
    """
    import run_collectors

    async def execute():
        mock_kline_coro = AsyncMock(name="mock_kline")
        mock_funding_coro = AsyncMock(name="mock_funding")
        mock_oi_coro = AsyncMock(name="mock_oi")

        scheduled_tasks = []

        async def fake_gather(*tasks):
            scheduled_tasks.extend(tasks)
            for t in tasks:
                if asyncio.iscoroutine(t):
                    t.close()
            return []

        with patch("run_collectors.ENABLE_FUTURES", True), \
             patch("run_collectors.ws_kline_main", return_value=mock_kline_coro()), \
             patch("run_collectors.funding_poller_run", return_value=mock_funding_coro()), \
             patch("run_collectors.oi_poller_run", return_value=mock_oi_coro()), \
             patch("run_collectors.asyncio.gather", side_effect=fake_gather):

            await run_collectors.main()

        assert len(scheduled_tasks) == 3, f"Expected 3 tasks, got {len(scheduled_tasks)}"
        task_reprs = [repr(t) for t in scheduled_tasks]
        for r in task_reprs:
            assert "liquidation" not in r.lower(), f"Unexpected liquidation task scheduled: {r}"

    asyncio.run(execute())


def test_run_collectors_main_futures_disabled_schedules_only_spot():
    """
    When ENABLE_FUTURES is False, run_collectors.main() must schedule ONLY ws_kline_main().
    """
    import run_collectors

    async def execute():
        mock_kline_coro = AsyncMock(name="mock_kline")
        scheduled_tasks = []

        async def fake_gather(*tasks):
            scheduled_tasks.extend(tasks)
            for t in tasks:
                if asyncio.iscoroutine(t):
                    t.close()
            return []

        with patch("run_collectors.ENABLE_FUTURES", False), \
             patch("run_collectors.ws_kline_main", return_value=mock_kline_coro()), \
             patch("run_collectors.asyncio.gather", side_effect=fake_gather):

            await run_collectors.main()

        assert len(scheduled_tasks) == 1, f"Expected 1 task, got {len(scheduled_tasks)}"

    asyncio.run(execute())


def test_run_collectors_cancellation_behavior():
    """
    Verify that if asyncio.gather is cancelled, CancelledError propagates cleanly.
    """
    import run_collectors

    async def execute():
        async def hanging_task():
            await asyncio.sleep(100)

        with patch("run_collectors.ENABLE_FUTURES", True), \
             patch("run_collectors.ws_kline_main", side_effect=hanging_task), \
             patch("run_collectors.funding_poller_run", side_effect=hanging_task), \
             patch("run_collectors.oi_poller_run", side_effect=hanging_task):

            task = asyncio.create_task(run_collectors.main())
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(execute())


def test_import_does_not_create_background_tasks():
    """Verify that importing modules does not spawn running background tasks or threads."""
    import sys
    sys.modules.pop("run_collectors", None)
    sys.modules.pop("collectors.ws_liquidations", None)

    import run_collectors
    import collectors.ws_liquidations

    import threading
    active_threads = [t.name for t in threading.enumerate()]
    assert not any("liquidation" in name.lower() for name in active_threads)


def test_signal_bot_test_db_isolation_and_credentials():
    """
    Validates DB isolation boundary:
    1. Connects strictly using test account `signal_bot_test`.
    2. Rejects cms and root credentials.
    3. Confirms active database is `signal_bot_test`.
    """
    import mysql.connector

    test_user = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
    test_pass = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()
    test_host = os.getenv("SIGNAL_BOT_TEST_DB_HOST", "127.0.0.1").strip()
    test_port = int(os.getenv("SIGNAL_BOT_TEST_DB_PORT", "3306"))

    assert test_user not in {"cms", "root"}, "cms/root prohibited for test verification"

    conn = mysql.connector.connect(
        host=test_host,
        port=test_port,
        database="signal_bot_test",
        user=test_user,
        password=test_pass,
        connection_timeout=5,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT DATABASE(), CURRENT_USER()")
        row = cur.fetchone()
        assert row[0] == "signal_bot_test", f"Expected signal_bot_test, got {row[0]}"
        assert "signal_bot_test" in row[1], f"Expected signal_bot_test user, got {row[1]}"
        cur.close()
    finally:
        conn.close()


def test_schema_sql_preserves_liquidations_table():
    """
    Verify that db/schema.sql maintains the liquidations table definition intact
    with all expected columns and indices.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(repo_root, "db", "schema.sql")

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    assert "CREATE TABLE IF NOT EXISTS liquidations" in schema_sql, (
        "liquidations table definition must remain in db/schema.sql"
    )
    assert "orig_qty" in schema_sql
    assert "price" in schema_sql
    assert "event_time" in schema_sql
    assert "idx_liq_symbol_time" in schema_sql
