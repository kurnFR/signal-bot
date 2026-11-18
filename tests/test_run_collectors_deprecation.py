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


def test_run_collectors_does_not_import_funding_poller():
    """Verify run_collectors namespace does not schedule funding_poller (exclusive to systemd service)."""
    import run_collectors
    assert not hasattr(run_collectors, "funding_poller_run"), (
        "Dual ownership failure: funding_poller_run is still imported/scheduled in run_collectors!"
    )
    source = inspect.getsource(run_collectors)
    assert "funding_poller_run" not in source, "Source still references funding_poller_run"
    assert "from collectors.funding_poller" not in source, (
        "Source still imports from collectors.funding_poller"
    )


def test_run_collectors_main_futures_enabled_schedules_intended_tasks():
    """
    When ENABLE_FUTURES is True, run_collectors.main() must schedule:
    - ws_kline_main()
    - oi_poller_run()
    And must NOT schedule funding_poller or any liquidation collector.
    """
    import run_collectors

    async def execute():
        mock_kline_coro = AsyncMock(name="mock_kline")
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
             patch("run_collectors.oi_poller_run", return_value=mock_oi_coro()), \
             patch("run_collectors.asyncio.gather", side_effect=fake_gather):

            await run_collectors.main()

        assert len(scheduled_tasks) == 2, f"Expected 2 tasks (kline + oi), got {len(scheduled_tasks)}"
        task_reprs = [repr(t) for t in scheduled_tasks]
        for r in task_reprs:
            assert "liquidation" not in r.lower(), f"Unexpected liquidation task scheduled: {r}"
            assert "funding" not in r.lower(), f"Unexpected funding task scheduled in run_collectors: {r}"

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


def test_two_simultaneous_funding_pollers_cannot_poll_concurrently():
    """Verify MariaDB advisory lock prevents two funding poller instances from polling concurrently."""
    from collectors.funding_poller import FundingPollerLock
    from unittest.mock import MagicMock

    mock_conn1 = MagicMock()
    cur1 = MagicMock()
    cur1.fetchone.return_value = (1,)  # lock acquired
    mock_conn1.cursor.return_value = cur1

    mock_conn2 = MagicMock()
    cur2 = MagicMock()
    cur2.fetchone.return_value = (0,)  # lock held by another
    mock_conn2.cursor.return_value = cur2

    lock1 = FundingPollerLock(connection=mock_conn1)
    lock2 = FundingPollerLock(connection=mock_conn2)

    assert lock1.acquire() is True
    assert lock2.acquire() is False
    lock1.release()


def test_funding_poller_lock_release_after_success_and_exceptions():
    """Verify FundingPollerLock releases the lock and connection in all scenarios."""
    from collectors.funding_poller import FundingPollerLock
    from unittest.mock import MagicMock

    # 1. Normal success
    mock_conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (1,)
    mock_conn.cursor.return_value = cur
    lock = FundingPollerLock(connection=mock_conn)
    with lock as acquired:
        assert acquired is True
    cur.execute.assert_any_call("SELECT RELEASE_LOCK(%s)", ("signal_bot_funding_poller",))

    # 2. Unhandled exception
    mock_conn_exc = MagicMock()
    cur_exc = MagicMock()
    cur_exc.fetchone.return_value = (1,)
    mock_conn_exc.cursor.return_value = cur_exc
    lock_exc = FundingPollerLock(connection=mock_conn_exc)
    with pytest.raises(ZeroDivisionError):
        with lock_exc as acquired:
            assert acquired is True
            _ = 1 / 0
    cur_exc.execute.assert_any_call("SELECT RELEASE_LOCK(%s)", ("signal_bot_funding_poller",))


def test_competing_funding_poller_skips_without_modifying_data():
    """Verify that when advisory lock is held, poll_all_symbols skips cleanly without fetching or inserting."""
    from collectors.funding_poller import poll_all_symbols, FundingPollerLock
    from unittest.mock import MagicMock, patch

    mock_lock = MagicMock(spec=FundingPollerLock)
    mock_lock.acquire.return_value = False  # Lock held by competing instance

    with patch("collectors.funding_poller.poll_symbol_incremental") as mock_incremental:
        res = poll_all_symbols(symbols=["BTCUSDT", "ETHUSDT"], acquire_lock=True, lock=mock_lock)

    assert res.get("skipped") is True
    assert res.get("reason") == "advisory_lock_held"
    assert res.get("rows_inserted") == 0
    assert mock_incremental.call_count == 0


def test_funding_poller_lock_connection_failure_distinguished():
    """Verify database connection initialization failure raises FundingPollerLockConnectionError and cleans up."""
    from collectors.funding_poller import FundingPollerLock, FundingPollerLockConnectionError
    from unittest.mock import patch, MagicMock

    mock_cfg = MagicMock()
    mock_cfg.to_dict.return_value = {"host": "127.0.0.1", "port": 3306, "user": "test", "password": "x", "database": "db"}

    with patch("mysql.connector.connect", side_effect=Exception("Can't connect to MySQL server on '127.0.0.1'")):
        lock = FundingPollerLock(config=mock_cfg)
        with pytest.raises(FundingPollerLockConnectionError) as exc_info:
            lock.acquire()
        assert "Connection failure" in str(exc_info.value)
        assert lock._conn is None
        assert lock._acquired is False


def test_funding_poller_lock_authentication_failure_distinguished():
    """Verify database authentication failure raises FundingPollerLockAuthenticationError."""
    from collectors.funding_poller import FundingPollerLock, FundingPollerLockAuthenticationError
    from unittest.mock import patch, MagicMock

    # 1. Missing credentials (ValueError)
    with patch("collectors.funding_poller.load_funding_collector_config", side_effect=ValueError("Dedicated database password (FUNDING_DB_PASSWORD) is missing.")):
        lock = FundingPollerLock()
        with pytest.raises(FundingPollerLockAuthenticationError) as exc_info:
            lock.acquire()
        assert "Authentication failure" in str(exc_info.value)

    # 2. Access denied error (error code 1045)
    access_err = Exception("1045 (28000): Access denied for user 'funding_poller'@'localhost'")
    access_err.errno = 1045
    mock_cfg = MagicMock()
    mock_cfg.to_dict.return_value = {"host": "127.0.0.1", "port": 3306, "user": "bad_user", "password": "bad", "database": "db"}
    with patch("mysql.connector.connect", side_effect=access_err):
        lock2 = FundingPollerLock(config=mock_cfg)
        with pytest.raises(FundingPollerLockAuthenticationError) as exc_info2:
            lock2.acquire()
        assert "Authentication failure" in str(exc_info2.value)


def test_funding_poller_lock_unexpected_query_failure_distinguished():
    """Verify unexpected database error during GET_LOCK query raises FundingPollerLockQueryError."""
    from collectors.funding_poller import FundingPollerLock, FundingPollerLockQueryError
    from unittest.mock import MagicMock

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.execute.side_effect = Exception("Lost connection to MySQL server during query")
    mock_conn.cursor.return_value = mock_cur

    lock = FundingPollerLock(connection=mock_conn)
    with pytest.raises(FundingPollerLockQueryError) as exc_info:
        lock.acquire()
    assert "Unexpected database error" in str(exc_info.value)
    assert lock._acquired is False
    # Verify cursor closed
    mock_cur.close.assert_called_once()


def test_poll_all_symbols_lock_error_raises_and_records_heartbeat():
    """Verify poll_all_symbols records an error heartbeat and raises when lock acquisition fails with an error."""
    from collectors.funding_poller import poll_all_symbols, FundingPollerLock, FundingPollerLockConnectionError
    from unittest.mock import MagicMock, patch

    mock_lock = MagicMock(spec=FundingPollerLock)
    mock_lock.acquire.side_effect = FundingPollerLockConnectionError("Connection failure connecting to MariaDB")

    with patch("collectors.funding_poller.heartbeat") as mock_hb:
        with pytest.raises(FundingPollerLockConnectionError):
            poll_all_symbols(symbols=["BTCUSDT"], acquire_lock=True, lock=mock_lock)

        mock_hb.assert_called_once()
        call_args = mock_hb.call_args
        assert call_args[0][0] == "funding_poller"
        assert call_args[1]["status"] == "error"
        assert "Connection failure" in call_args[1]["detail"]


def test_poll_all_symbols_forwards_config_path_to_lock():
    """Verify poll_all_symbols passes custom config_path to FundingPollerLock."""
    from collectors.funding_poller import poll_all_symbols
    from unittest.mock import patch, MagicMock

    with patch("collectors.funding_poller.FundingPollerLock") as MockLock:
        mock_instance = MagicMock()
        mock_instance.acquire.return_value = False
        MockLock.return_value = mock_instance

        custom_path = "/etc/signal-bot/custom-collector.env"
        res = poll_all_symbols(symbols=["BTCUSDT"], config_path=custom_path)

        MockLock.assert_called_once_with(config_path=custom_path)
        assert res.get("skipped") is True


def test_funding_poller_run_forwards_config_path():
    """Verify async run() forwards custom config_path to poll_all_symbols and loads custom config."""
    import asyncio
    from collectors.funding_poller import run
    from unittest.mock import patch, MagicMock

    custom_path = "/etc/signal-bot/custom-collector.env"

    class StopLoop(BaseException):
        pass

    async def execute():
        call_params = []

        def fake_poll_all_symbols(*args, **kwargs):
            call_params.append(kwargs)
            raise StopLoop("Stop async loop after first cycle")

        with patch("collectors.funding_poller.load_funding_collector_config") as mock_load_cfg, \
             patch("collectors.funding_poller.get_funding_pool") as mock_get_pool, \
             patch("collectors.funding_poller.poll_all_symbols", side_effect=fake_poll_all_symbols), \
             patch("collectors.funding_poller.heartbeat"):

            mock_pool = MagicMock()
            mock_get_pool.return_value = mock_pool

            with pytest.raises(StopLoop):
                await run(symbols=["BTCUSDT"], poll_interval=1, config_path=custom_path)

            mock_load_cfg.assert_called_once_with(env_path=custom_path)
            assert len(call_params) == 1
            assert call_params[0].get("config_path") == custom_path

    asyncio.run(execute())
