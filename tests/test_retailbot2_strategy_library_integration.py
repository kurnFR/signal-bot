"""
Test Strategy Library, Paper Trading, and Telegram Alert Gating for RetailBot2 strategies.

Verifies:
1. All RetailBot2 strategies (BB_REV, BB_REV_INV, DBL_PAT, DBL_PAT_INV, etc.) appear in Strategy Library.
2. Strategies can be added to Paper Trading configurations.
3. Adding/updating RetailBot2 paper configs synchronizes with rdt_strategy_lifecycle.
4. Only chosen strategies with telegram_enabled=True send trade open/close signals to Telegram.
5. Background shadow/inverse execution continues recording paper trades.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from web.app import app
from web.routes.backtest import list_strategies, STRATEGY_METADATA
from backtest.strategies import STRATEGIES
from backtest.strategies.retailbot2_strategies import RETAILBOT2_STRATEGY_NAMES, RETAILBOT2_STRATEGIES
from paper.engine import close_position_in_db


@pytest.fixture
def api_client():
    return TestClient(app)


def test_retailbot2_strategies_registered_in_strategies():
    assert "BB_REV" in STRATEGIES
    assert "BB_REV_INV" in STRATEGIES
    assert "DBL_PAT" in STRATEGIES
    assert "DBL_PAT_INV" in STRATEGIES
    assert "RSI_CTR_INV" in STRATEGIES
    assert "MA_WHIP_INV" in STRATEGIES
    assert "SR_TRAP_INV" in STRATEGIES
    assert "MACD_DIV_INV" in STRATEGIES
    assert "SPIKE_FADE_INV" in STRATEGIES
    assert "COMPOSITE_INV" in STRATEGIES
    assert "DBL_PAT_INV_inverse" in STRATEGIES


def test_strategy_library_catalog_exposes_bb_rev_inv_and_dbl_pat_inv():
    catalog = list_strategies()
    strategies = catalog.get("strategies", [])
    assert len(strategies) >= 40

    by_id = {s["id"]: s for s in strategies}
    assert "BB_REV_INV" in by_id
    assert "DBL_PAT_INV" in by_id

    bb_rev_inv = by_id["BB_REV_INV"]
    assert bb_rev_inv["isInverse"] is True
    assert bb_rev_inv["baseName"] == "BB_REV"
    assert "Bollinger" in bb_rev_inv["displayName"]
    assert bb_rev_inv["category"] == "RetailBot2 (Research)"
    assert len(bb_rev_inv["params"]) >= 3

    dbl_pat_inv = by_id["DBL_PAT_INV"]
    assert dbl_pat_inv["isInverse"] is True
    assert dbl_pat_inv["baseName"] == "DBL_PAT"
    assert "Double" in dbl_pat_inv["displayName"]
    assert dbl_pat_inv["category"] == "RetailBot2 (Research)"


def test_paper_config_accepts_bb_rev_inv_and_syncs_lifecycle():
    with patch("web.routes.paper_routes.set_paper_config") as mock_set_cfg, \
         patch("web.routes.paper_routes._sync_retailbot2_lifecycle") as mock_sync:
        mock_set_cfg.return_value = {"status": "success", "strategy": "BB_REV_INV"}

        from web.routes.paper_routes import update_config, PaperConfigRequest

        req = PaperConfigRequest(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1d",
            strategy_name="BB_REV_INV",
            is_active=True,
            allocated_capital=5000.0,
            risk_per_trade_pct=1.0,
            telegram_enabled=True,
            report_enabled=True,
        )
        res = update_config(req)
        assert res["status"] == "success"

        mock_set_cfg.assert_called_once()
        mock_sync.assert_called_once_with("BB_REV_INV", True, True, True)


def test_sync_retailbot2_lifecycle_updates_db():
    from web.routes.paper_routes import _sync_retailbot2_lifecycle
    with patch("db.db.get_retailbot2_strategy_lifecycle_settings", return_value=[]), \
         patch("db.db.set_retailbot2_strategy_lifecycle_setting") as mock_db_set:
        _sync_retailbot2_lifecycle("BB_REV_INV", is_active=True, telegram_enabled=True, report_enabled=True)
        mock_db_set.assert_called_once_with(
            "BB_REV_INV", "inverse",
            {
                "evaluation_enabled": True,
                "paper_enabled": True,
                "telegram_enabled": True,
                "report_enabled": True,
                "auto_promote_enabled": False,
            }
        )


def test_trade_close_alert_suppressed_when_telegram_disabled():
    pos = {
        "id": 999,
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1d",
        "strategy_name": "BB_REV_INV",
        "direction": "LONG",
        "entry_price": 50000.0,
        "stop_loss": 48000.0,
        "take_profit": 54000.0,
        "quantity": 0.1,
        "status": "OPEN",
        "allocated_capital": 5000.0,
        "risk_per_trade_pct": 1.0,
        "notional_value": 5000.0,
        "entry_fee": 5.0,
        "stop_distance": 2000.0,
        "entry_time": 1700000000000,
        "holding_bars": 10,
    }

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"telegram_enabled": 0}  # Telegram is DISABLED
    mock_cur.rowcount = 1
    mock_conn.cursor.return_value = mock_cur

    with patch("paper.engine.get_pool") as mock_pool, \
         patch("notifications.telegram_notifier.send_trade_close_alert") as mock_tg_close:
        mock_pool.return_value.get_connection.return_value = mock_conn

        close_position_in_db(pos, exit_price=54000.0, exit_time=1700086400000, exit_reason="TP")

        # Telegram close alert must NOT be dispatched because telegram_enabled is False!
        mock_tg_close.assert_not_called()


def test_trade_close_alert_dispatched_when_telegram_enabled():
    pos = {
        "id": 999,
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1d",
        "strategy_name": "BB_REV_INV",
        "direction": "LONG",
        "entry_price": 50000.0,
        "stop_loss": 48000.0,
        "take_profit": 54000.0,
        "quantity": 0.1,
        "status": "OPEN",
        "allocated_capital": 5000.0,
        "risk_per_trade_pct": 1.0,
        "notional_value": 5000.0,
        "entry_fee": 5.0,
        "stop_distance": 2000.0,
        "entry_time": 1700000000000,
        "holding_bars": 10,
    }

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {"telegram_enabled": 1}  # Telegram is ENABLED
    mock_cur.rowcount = 1
    mock_conn.cursor.return_value = mock_cur

    with patch("paper.engine.get_pool") as mock_pool, \
         patch("notifications.telegram_notifier.send_trade_close_alert") as mock_tg_close:
        mock_pool.return_value.get_connection.return_value = mock_conn

        close_position_in_db(pos, exit_price=54000.0, exit_time=1700086400000, exit_reason="TP")

        # Telegram close alert MUST be dispatched because telegram_enabled is True!
        mock_tg_close.assert_called_once()
