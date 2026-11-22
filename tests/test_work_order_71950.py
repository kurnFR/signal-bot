"""Unit tests for Work Order 71950:
1. Whitespace symbol rejection and normalization in collectors/funding_poller.py
2. Exit price agreement between paper_positions and paper_trades in paper/engine.py and RetailBot2 accounting alignment
3. Cursor and connection lifecycle cleanup during query exceptions in paper/engine.py, paper/retailbot2.py, and db/db.py
"""

import argparse
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest

from backtest.accounting import calculate_trade_accounting
from collectors import funding_poller
from db import db
from paper import engine as paper_engine
from paper.retailbot2 import DatabaseManager, RetailDeathTrapBot


# ==============================================================================
# 1. Whitespace Symbol Validation & Normalization in funding_poller.py
# ==============================================================================


@pytest.mark.parametrize("invalid_symbol", ["", "   ", "\t\n", None, 123, []])
def test_poll_symbol_incremental_rejects_empty_and_whitespace(invalid_symbol):
    """poll_symbol_incremental raises ValueError for empty, whitespace, or invalid symbol types."""
    session = Mock()
    with pytest.raises(ValueError, match="symbol cannot be empty or whitespace-only"):
        funding_poller.poll_symbol_incremental(session, invalid_symbol)


def test_poll_symbol_incremental_normalizes_valid_symbol(monkeypatch):
    """poll_symbol_incremental strips whitespace and converts symbols to uppercase."""
    session = Mock()
    captured_symbol = None

    def fake_fetch_funding_page(session, symbol, **kwargs):
        nonlocal captured_symbol
        captured_symbol = symbol
        return []

    monkeypatch.setattr(funding_poller, "get_latest_funding_time", lambda *a, **k: 1000)
    monkeypatch.setattr(funding_poller, "fetch_funding_page", fake_fetch_funding_page)

    res = funding_poller.poll_symbol_incremental(session, "  btcusdt  ", end_ms=2000)
    assert res == 0
    assert captured_symbol == "BTCUSDT"


def test_poll_all_symbols_filters_whitespace_symbols(monkeypatch):
    """poll_all_symbols filters out whitespace-only symbols and normalizes valid ones."""
    polled = []

    def fake_poll_symbol(session, symbol, **kwargs):
        polled.append(symbol)
        return 1

    monkeypatch.setattr(funding_poller, "poll_symbol_incremental", fake_poll_symbol)
    monkeypatch.setattr(funding_poller, "heartbeat", Mock())

    res = funding_poller.poll_all_symbols(
        symbols=["  btcusdt  ", "   ", "", "\t", "ethusdt"],
        session=Mock(),
        acquire_lock=False,
    )

    assert polled == ["BTCUSDT", "ETHUSDT"]
    assert res["symbols_total"] == 2
    assert res["symbols_ok"] == 2


@pytest.mark.parametrize("empty_symbols", [[], ["   "], ["", "   ", "\t\n"]])
def test_poll_all_symbols_rejects_all_empty_or_whitespace(empty_symbols):
    """poll_all_symbols raises ValueError if no valid symbols are provided."""
    with pytest.raises(ValueError, match="Symbols list cannot be empty or contain only whitespace"):
        funding_poller.poll_all_symbols(symbols=empty_symbols, session=Mock(), acquire_lock=False)


def test_cli_main_rejects_whitespace_symbol(monkeypatch):
    """CLI parser in main() errors out when --symbol is whitespace-only."""
    monkeypatch.setattr("sys.argv", ["funding_poller", "--symbol", "   "])
    with pytest.raises(SystemExit) as exc_info:
        funding_poller.main()
    assert exc_info.value.code != 0


def test_cli_main_normalizes_valid_symbol(monkeypatch):
    """CLI parser in main() strips and uppercases valid symbols."""
    poll_all_mock = Mock(return_value={"symbols_ok": 1, "symbols_total": 1, "rows_inserted": 0, "skipped": False})
    monkeypatch.setattr(funding_poller, "poll_all_symbols", poll_all_mock)
    monkeypatch.setattr(funding_poller, "load_funding_collector_config", lambda *a, **k: Mock())
    monkeypatch.setattr(funding_poller, "get_funding_pool", lambda *a, **k: Mock())
    monkeypatch.setattr("sys.argv", ["funding_poller", "--symbol", "  solusdt  ", "--once"])

    funding_poller.main()
    poll_all_mock.assert_called_once()
    assert poll_all_mock.call_args[1]["symbols"] == ["SOLUSDT"]


# ==============================================================================
# 2. Accounting Alignment: Position and Trade Exit Price Agreement
# ==============================================================================


def test_paper_engine_exit_exec_price_matches_position_and_trade(monkeypatch):
    """close_position_in_db must persist exit_exec_price into paper_positions.current_price
    so that paper_positions.current_price matches paper_trades.exit_price exactly,
    even when exit slippage is applied."""
    pos = {
        "id": 42,
        "symbol": "BTCUSDT",
        "market": "futures",
        "timeframe": "1h",
        "strategy_name": "ema_crossover",
        "direction": "LONG",
        "entry_time": 1000000,
        "entry_price": 50000.0,
        "stop_loss": 48000.0,
        "take_profit": 55000.0,
        "current_price": 50000.0,
        "quantity": 0.5,
        "allocated_capital": 25000.0,
        "risk_per_trade_pct": 1.0,
        "stop_distance": 2000.0,
        "notional_value": 25000.0,
        "entry_fee": 10.0,
        "holding_bars": 5,
        "status": "OPEN",
    }

    # Slippage will make exit_exec_price lower than raw exit_price for LONG
    raw_exit_price = 54000.0
    accounting = calculate_trade_accounting(
        direction=pos["direction"],
        quantity=float(pos["quantity"]),
        entry_price=float(pos["entry_price"]),
        exit_price=raw_exit_price,
        fee_pct=paper_engine.BACKTEST_FEE_PCT,
        entry_slippage_pct=0.0,
        exit_slippage_pct=paper_engine.BACKTEST_SLIPPAGE_PCT,
        stop_price=float(pos["stop_loss"]),
        funding_cost=0.0,
    )
    expected_exit_exec_price = float(accounting["exit_exec_price"])
    assert expected_exit_exec_price != raw_exit_price  # Non-zero slippage verified

    mock_cursor = Mock()
    mock_cursor.rowcount = 1
    mock_cursor.fetchall.return_value = []  # No existing trade in paper_trades
    mock_cursor.fetchone.return_value = None

    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor

    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn

    monkeypatch.setattr(paper_engine, "get_pool", lambda: mock_pool)
    monkeypatch.setattr(paper_engine, "_funding_cost_for_position", lambda *a, **k: 0.0)

    closed = paper_engine.close_position_in_db(
        pos=pos,
        exit_price=raw_exit_price,
        exit_time=2000000,
        exit_reason="TP",
    )

    assert closed is True
    # In-memory position must reflect exit_exec_price
    assert pos["current_price"] == expected_exit_exec_price
    assert pos["status"] == "CLOSED"

    # Verify SQL UPDATE paper_positions set current_price = exit_exec_price
    update_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "UPDATE paper_positions" in call[0][0]
    ]
    assert len(update_calls) == 1
    update_sql, update_params = update_calls[0][0]
    # In UPDATE query: SET status = 'CLOSED', current_price = %s, last_update_time = %s, funding_pnl = %s, holding_bars = %s WHERE id = %s
    assert update_params[0] == expected_exit_exec_price

    # Verify SQL INSERT INTO paper_trades exit_price = exit_exec_price
    insert_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "INSERT INTO paper_trades" in call[0][0]
    ]
    assert len(insert_calls) == 1
    insert_sql, insert_params = insert_calls[0][0]
    # In paper_trades params: (..., entry_price, exit_time, exit_price, exit_reason, ...)
    # exit_price is index 9
    assert insert_params[9] == expected_exit_exec_price
    # Check that position current_price and trade exit_price are identical
    assert update_params[0] == insert_params[9]


def test_paper_engine_duplicate_trade_preserves_exit_exec_price(monkeypatch):
    """When a paper position already has a paper_trades record, close_position_in_db
    still updates paper_positions.current_price to exit_exec_price consistently."""
    pos = {
        "id": 99,
        "symbol": "ETHUSDT",
        "market": "futures",
        "timeframe": "1h",
        "strategy_name": "rsi_reversal",
        "direction": "SHORT",
        "entry_time": 1000000,
        "entry_price": 3000.0,
        "stop_loss": 3100.0,
        "take_profit": 2800.0,
        "current_price": 3000.0,
        "quantity": 2.0,
        "allocated_capital": 6000.0,
        "risk_per_trade_pct": 1.0,
        "stop_distance": 100.0,
        "notional_value": 6000.0,
        "entry_fee": 2.4,
        "holding_bars": 3,
        "status": "OPEN",
    }

    mock_cursor = Mock()
    mock_cursor.rowcount = 1
    # Existing trade found in paper_trades
    mock_cursor.fetchall.return_value = [(1,)]
    mock_cursor.fetchone.return_value = None

    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor

    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn

    monkeypatch.setattr(paper_engine, "get_pool", lambda: mock_pool)
    monkeypatch.setattr(paper_engine, "_funding_cost_for_position", lambda *a, **k: 0.0)

    closed = paper_engine.close_position_in_db(
        pos=pos,
        exit_price=2900.0,
        exit_time=1500000,
        exit_reason="MANUAL",
    )

    assert closed is True
    # Position must have exit_exec_price, NOT raw exit_price
    assert pos["current_price"] != 2900.0
    assert pos["status"] == "CLOSED"


def test_retailbot2_close_trade_gross_and_net_accounting():
    """RetailBot2 _close_trade supports net accounting via deduct_fees_slippage=True
    and explicitly labels default metrics as Gross P&L."""
    trade = {
        "id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "mode": "shadow",
        "entry": 50000.0,
        "stop": 48000.0,
        "units": 0.1,
        "strategy": "s1",
    }

    # 1. Default (gross PnL) mode
    bot_gross = object.__new__(RetailDeathTrapBot)
    bot_gross.observation_only = False
    bot_gross.db = Mock()
    bot_gross.db.close_trade_and_update_stats.return_value = True
    bot_gross.logger = Mock()
    bot_gross.tg = Mock()
    bot_gross.active_shadow = {1: dict(trade)}
    bot_gross.active_inverse = {}
    bot_gross.equity_shadow = 1000.0
    bot_gross.equity_inverse = 1000.0
    bot_gross.config = Mock()
    bot_gross.config.observation_only = False
    bot_gross.config.deduct_fees_slippage = False

    bot_gross._close_trade(1, 52000.0, "TP", mode="shadow")
    # Gross PnL: (52000 - 50000) * 0.1 = +200.0
    assert bot_gross.equity_shadow == 1200.0
    bot_gross.db.close_trade_and_update_stats.assert_called_once()
    saved_pnl_gross = bot_gross.db.close_trade_and_update_stats.call_args[0][3]
    assert saved_pnl_gross == 200.0
    # Check that logger recorded Gross P&L
    log_messages = [call[0][0] for call in bot_gross.logger.info.call_args_list]
    assert any("Gross P&L: $+200.00" in msg for msg in log_messages)

    # 2. Net accounting mode (deduct_fees_slippage=True)
    bot_net = object.__new__(RetailDeathTrapBot)
    bot_net.observation_only = False
    bot_net.db = Mock()
    bot_net.db.close_trade_and_update_stats.return_value = True
    bot_net.logger = Mock()
    bot_net.tg = Mock()
    bot_net.active_shadow = {2: dict(trade)}
    bot_net.active_inverse = {}
    bot_net.equity_shadow = 1000.0
    bot_net.equity_inverse = 1000.0
    bot_net.config = Mock()
    bot_net.config.observation_only = False
    bot_net.config.deduct_fees_slippage = True

    bot_net._close_trade(2, 52000.0, "TP", mode="shadow")
    bot_net.db.close_trade_and_update_stats.assert_called_once()
    saved_pnl_net = bot_net.db.close_trade_and_update_stats.call_args[0][3]
    # Net PnL must be strictly less than gross PnL due to fee and slippage deductions
    assert saved_pnl_net < 200.0
    assert bot_net.equity_shadow < 1200.0
    # Check that logger recorded Net P&L
    log_messages_net = [call[0][0] for call in bot_net.logger.info.call_args_list]
    assert any("Net P&L:" in msg for msg in log_messages_net)


# ==============================================================================
# 3. Database & Paper Cursor Lifecycle Cleanup on Exceptions
# ==============================================================================


def test_paper_engine_cursor_cleanup_on_exception(monkeypatch):
    """Ensure cur.close() and conn.close() are always called on exceptions
    across paper/engine.py query functions."""
    mock_cursor = Mock()
    mock_cursor.execute.side_effect = RuntimeError("DB Query Failure")
    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn

    monkeypatch.setattr(paper_engine, "get_pool", lambda: mock_pool)

    # Test get_paper_configs
    with pytest.raises(RuntimeError, match="DB Query Failure"):
        paper_engine.get_paper_configs()
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # Test get_active_positions
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    with pytest.raises(RuntimeError, match="DB Query Failure"):
        paper_engine.get_active_positions()
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # Test manual_close_position
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    with pytest.raises(RuntimeError, match="DB Query Failure"):
        paper_engine.manual_close_position(123)
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


def test_paper_engine_close_position_in_db_cur_cfg_cleanup(monkeypatch):
    """Ensure cur_cfg is properly closed even if an exception occurs during telegram check."""
    pos = {
        "id": 10,
        "symbol": "BTCUSDT",
        "market": "futures",
        "timeframe": "1h",
        "strategy_name": "s1",
        "direction": "LONG",
        "entry_time": 1000,
        "entry_price": 50000.0,
        "stop_loss": 48000.0,
        "take_profit": 55000.0,
        "current_price": 50000.0,
        "quantity": 1.0,
        "allocated_capital": 50000.0,
        "risk_per_trade_pct": 1.0,
        "stop_distance": 2000.0,
        "notional_value": 50000.0,
        "entry_fee": 10.0,
        "holding_bars": 1,
        "status": "OPEN",
    }

    trade_cursor = Mock()
    trade_cursor.rowcount = 1
    trade_cursor.fetchall.return_value = []
    trade_conn = Mock()
    trade_conn.cursor.return_value = trade_cursor

    cfg_cursor = Mock()
    cfg_cursor.execute.side_effect = RuntimeError("Config Query Failed")
    cfg_conn = Mock()
    cfg_conn.cursor.return_value = cfg_cursor

    connections = [trade_conn, cfg_conn]
    mock_pool = Mock()
    mock_pool.get_connection.side_effect = lambda: connections.pop(0)

    monkeypatch.setattr(paper_engine, "get_pool", lambda: mock_pool)
    monkeypatch.setattr(paper_engine, "_funding_cost_for_position", lambda *a, **k: 0.0)

    # close_position_in_db catches config check errors and logs warning, returning True
    res = paper_engine.close_position_in_db(pos, exit_price=51000.0, exit_time=2000, exit_reason="TP")
    assert res is True
    # cur_cfg must be closed in its finally block
    cfg_cursor.close.assert_called_once()
    cfg_conn.close.assert_called_once()


def test_retailbot2_database_manager_cursor_cleanup_on_exception():
    """Ensure DatabaseManager closes cursors and connections even when SQL queries raise exceptions."""
    db_mgr = object.__new__(DatabaseManager)
    db_mgr.logger = Mock()

    # 1. count_open
    mock_cursor = Mock()
    mock_cursor.execute.side_effect = RuntimeError("count_open failed")
    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor
    db_mgr.get_conn = Mock(return_value=mock_conn)

    count = db_mgr.count_open()
    assert count == 0
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # 2. get_stats
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    mock_cursor.execute.side_effect = RuntimeError("get_stats failed")
    stats = db_mgr.get_stats()
    assert stats == {}
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # 3. get_realized_pnl_by_mode
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    mock_cursor.execute.side_effect = RuntimeError("get_realized_pnl_by_mode failed")
    pnl_by_mode = db_mgr.get_realized_pnl_by_mode()
    assert pnl_by_mode == {}
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()

    # 4. get_last_trade_times
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    mock_cursor.execute.side_effect = RuntimeError("get_last_trade_times failed")
    trade_times = db_mgr.get_last_trade_times()
    assert trade_times == {}
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


def test_db_owned_cursor_cleanup_on_exception(monkeypatch):
    """Ensure _owned_cursor in db/db.py closes cursor, rolls back, and closes connection on exception."""
    mock_cursor = Mock()
    mock_cursor.execute.side_effect = RuntimeError("SQL error")
    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor

    mock_pool = Mock()
    mock_pool.get_connection.return_value = mock_conn
    monkeypatch.setattr(db, "get_retailbot2_pool", lambda: mock_pool)

    # 1. get_retailbot2_open_trades
    with pytest.raises(RuntimeError, match="SQL error"):
        db.get_retailbot2_open_trades()
    mock_cursor.close.assert_called_once()
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()

    # 2. get_retailbot2_recent_trades
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    with pytest.raises(RuntimeError, match="SQL error"):
        db.get_retailbot2_recent_trades()
    mock_cursor.close.assert_called_once()
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()

    # 3. set_retailbot2_strategy_lifecycle_setting
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    settings = {
        "evaluation_enabled": True,
        "paper_enabled": True,
        "telegram_enabled": False,
        "report_enabled": True,
        "auto_promote_enabled": False,
    }
    with pytest.raises(RuntimeError, match="SQL error"):
        db.set_retailbot2_strategy_lifecycle_setting("s1", "shadow", settings)
    mock_cursor.close.assert_called_once()
    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()
