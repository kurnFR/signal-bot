"""Control parity tests for all 16 RetailBot2 strategies (14 + COMPOSITE/COMPOSITE_INV) in Paper Trading."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import notifications.telegram_notifier as tg
import paper.engine as pe
from paper.engine import PaperConfigHasHistory
from paper.paper_report import format_paper_report
from scripts import migrate_retailbot2_lifecycle as mig
from web.app import app

ALL_RETAILBOT2_CANONICAL_NAMES = (
    "BB_REV", "BB_REV_INV",
    "DBL_PAT", "DBL_PAT_INV",
    "RSI_CTR", "RSI_CTR_INV",
    "MA_WHIP", "MA_WHIP_INV",
    "SR_TRAP", "SR_TRAP_INV",
    "MACD_DIV", "MACD_DIV_INV",
    "SPIKE_FADE", "SPIKE_FADE_INV",
    "COMPOSITE", "COMPOSITE_INV",
)

_CLIENT = TestClient(app, raise_server_exceptions=False)


def _trader_user():
    return {
        "id": 7,
        "username": "trader-parity",
        "email": None,
        "role": "trader",
        "is_active": 1,
        "must_change_password": 0,
    }


def _sample_candles(count=60, anchor_end_ms=1_795_737_600_000):
    base = anchor_end_ms - count * 3_600_000
    rows = []
    for i in range(count):
        open_p = 100.0 + i * 0.1
        rows.append({
            "open_time": base + i * 3_600_000,
            "close_time": base + (i + 1) * 3_600_000 - 1,
            "open": open_p,
            "high": open_p + 1.0,
            "low": open_p - 1.0,
            "close": open_p + 0.5,
            "volume": 500.0,
            "atr": 1.2,
        })
    return pd.DataFrame(rows)


def _make_fake_pool(captured_queries):
    class _Cursor:
        def __init__(self):
            self._rows = []
            self._one = None
            self.lastrowid = 1
            self.rowcount = 1

        def execute(self, sql, params=None):
            captured_queries.append((sql, params))
            self._one = None
            self._rows = []
            if "SELECT telegram_enabled FROM paper_configs" in sql:
                self._one = {"telegram_enabled": 0}

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._rows

        def close(self):
            pass

    class _Conn:
        def cursor(self, dictionary=False):
            return _Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _Pool:
        def get_connection(self):
            return _Conn()

    return _Pool()


@pytest.mark.parametrize("strategy_name", ALL_RETAILBOT2_CANONICAL_NAMES)
def test_all_16_strategies_deploy_edit_pause_archive_permanent_delete(strategy_name):
    configs = [
        {
            "id": 101,
            "symbol": "ETHUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": strategy_name,
            "is_active": True,
            "allocated_capital": 5000.0,
            "risk_per_trade_pct": 1.0,
            "ml_model_id": None,
            "ml_generator_model_id": None,
            "telegram_enabled": False,
            "report_enabled": True,
            "archived_at": None,
        }
    ]
    with patch("web.app.authenticate_request", return_value=_trader_user()), \
         patch("web.routes.paper_routes.set_paper_config", return_value={"status": "success", "id": 101, "strategy": strategy_name}) as mock_set, \
         patch("web.routes.paper_routes.sync_and_evaluate_paper_trading", return_value={"evaluated_configs": 1}), \
         patch("web.routes.paper_routes.get_paper_configs", return_value=configs), \
         patch("web.routes.paper_routes.update_paper_deployment", return_value=True) as mock_patch, \
         patch("web.routes.paper_routes.delete_paper_config", return_value=True) as mock_archive, \
         patch("web.routes.paper_routes.purge_paper_config", return_value=True) as mock_purge:
        res_deploy = _CLIENT.post("/api/paper/deploy", json={
            "symbol": "ETHUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": strategy_name,
            "allocated_capital": 5000.0,
            "risk_per_trade_pct": 1.0,
            "send_telegram": False,
        })
        assert res_deploy.status_code == 200, res_deploy.text
        assert mock_set.call_args.args[:4] == ("ETHUSDT", "spot", "1h", strategy_name)

        res_edit = _CLIENT.patch("/api/paper/configs/101", json={"allocated_capital": 7500.0, "is_active": False})
        assert res_edit.status_code == 200
        mock_patch.assert_called_with(101, is_active=False, allocated_capital=7500.0)

        res_archive = _CLIENT.delete("/api/paper/configs/101")
        assert res_archive.status_code == 200
        mock_archive.assert_called_once_with(101)

        res_purge = _CLIENT.delete("/api/paper/configs/101/permanent")
        assert res_purge.status_code == 200
        mock_purge.assert_called_once_with(101)

    with patch("web.app.authenticate_request", return_value=_trader_user()), \
         patch("web.routes.paper_routes.purge_paper_config", side_effect=PaperConfigHasHistory("has trades")):
        res_conflict = _CLIENT.delete("/api/paper/configs/101/permanent")
        assert res_conflict.status_code == 409


@pytest.mark.parametrize("strategy_name", ALL_RETAILBOT2_CANONICAL_NAMES)
def test_inactive_strategy_never_opens_entry(strategy_name):
    cfg = {
        "id": 11,
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1h",
        "strategy_name": strategy_name,
        "is_active": False,
        "allocated_capital": 5000.0,
        "risk_per_trade_pct": 1.0,
        "telegram_enabled": True,
        "report_enabled": True,
    }
    mock_strat = MagicMock(return_value=[{"direction": "LONG"}])
    with patch.object(pe, "get_paper_configs", return_value=[cfg]), \
         patch.object(pe, "_process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch.object(pe, "get_active_positions", return_value=[]), \
         patch.object(pe, "paper_strategy_registry", return_value={strategy_name: mock_strat}), \
         patch.object(pe, "create_pending_order") as mock_order, \
         patch.object(pe, "_insert_position") as mock_insert:
        res = pe._sync_and_evaluate_paper_trading_impl()

    assert res["evaluated_configs"] == 0
    assert res["new_positions_opened"] == 0
    mock_strat.assert_not_called()
    mock_order.assert_not_called()
    mock_insert.assert_not_called()


@pytest.mark.parametrize("strategy_name", ALL_RETAILBOT2_CANONICAL_NAMES)
def test_telegram_disabled_sends_no_alert_or_outbox_event(strategy_name):
    df = _sample_candles(60)
    candle_open_time = int(df.iloc[-1]["open_time"])
    entry_candle_time = candle_open_time + 3_600_000
    open_candle = {
        "open_time": entry_candle_time,
        "open": 106.0,
        "high": 107.0,
        "low": 105.0,
        "close": 106.2,
        "volume": 500.0,
    }
    cfg = {
        "id": 22,
        "symbol": "ETHUSDT",
        "market": "spot",
        "timeframe": "1h",
        "strategy_name": strategy_name,
        "is_active": True,
        "allocated_capital": 5000.0,
        "risk_per_trade_pct": 1.0,
        "telegram_enabled": False,
        "report_enabled": True,
        "strategy_params": None,
        "ml_model_id": None,
        "ml_generator_model_id": None,
    }
    sim_trade = {
        "signal_open_time": candle_open_time,
        "entry_time": entry_candle_time,
        "direction": "LONG",
        "entry_price": 106.0,
        "stop_loss": 104.0,
        "take_profit": 110.0,
        "atr_at_signal": 1.2,
    }
    created_order = {
        "id": 301,
        "status": "PENDING",
        "config_id": 22,
        "symbol": "ETHUSDT",
        "market": "spot",
        "timeframe": "1h",
        "strategy_name": strategy_name,
        "entry_time": entry_candle_time,
        "direction": "LONG",
        "requested_entry_price": 106.0,
        "stop_loss": 104.0,
        "take_profit": 110.0,
        "atr_at_signal": 1.2,
        "ml_model_id": None,
        "ml_generator_model_id": None,
        "ml_probability": None,
    }
    captured_queries = []

    with patch.object(pe.time, "time", return_value=(entry_candle_time + 10_000) / 1000.0), \
         patch.object(pe, "get_paper_configs", return_value=[cfg]), \
         patch.object(pe, "_process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch.object(pe, "get_active_positions", return_value=[]), \
         patch.object(pe, "fetch_ohlcv_with_features_df", return_value=df), \
         patch.object(pe, "fetch_latest_open_candle", return_value=open_candle), \
         patch.object(pe, "_signal_already_traded", return_value=False), \
         patch("paper.circuit_breaker.check_circuit_breakers", return_value=(True, "ok")), \
         patch.object(pe, "paper_strategy_registry", return_value={strategy_name: lambda _df, _p: [sim_trade]}), \
         patch.object(pe, "create_pending_order", return_value=created_order), \
         patch.object(pe, "fill_pending_order", return_value={"status": "filled"}), \
         patch("notifications.telegram_notifier.send_signal_alert") as mock_entry_tg, \
         patch("notifications.telegram_notifier.send_trade_close_alert") as mock_exit_tg, \
         patch.object(pe, "get_pool", return_value=_make_fake_pool(captured_queries)):
        res = pe._sync_and_evaluate_paper_trading_impl()
        assert res["orders_filled"] == 1
        mock_entry_tg.assert_not_called()

        closed = pe.close_position_in_db(
            {
                "id": 501,
                "status": "OPEN",
                "config_id": 22,
                "symbol": "ETHUSDT",
                "market": "spot",
                "timeframe": "1h",
                "strategy_name": strategy_name,
                "direction": "LONG",
                "entry_time": entry_candle_time,
                "entry_price": 106.0,
                "stop_loss": 104.0,
                "current_stop": 104.0,
                "take_profit": 110.0,
                "initial_risk": 2.0,
                "stop_distance": 2.0,
                "allocated_capital": 5000.0,
                "risk_per_trade_pct": 1.0,
                "notional_value": 106.0,
                "quantity": 1.0,
                "entry_fee": 0.1,
                "risk_amount": 50.0,
                "holding_bars": 3,
                "ml_model_id": None,
                "ml_generator_model_id": None,
            },
            exit_price=110.0,
            exit_time=entry_candle_time + 10_800_000,
            exit_reason="TP",
        )
        assert closed is True
        mock_exit_tg.assert_not_called()

    assert not any("rdt_telegram_outbox" in sql.lower() for sql, _ in captured_queries)


@pytest.mark.parametrize("strategy_name", ALL_RETAILBOT2_CANONICAL_NAMES)
def test_telegram_enabled_entry_and_exit_text_includes_strategy_and_pair(strategy_name):
    sent_texts = []
    with patch.object(tg, "send_telegram_message", side_effect=lambda text, **_kw: sent_texts.append(text) or {"success": True}):
        tg.send_signal_alert(
            symbol="SOLUSDT",
            market="spot",
            timeframe="1h",
            strategy_name=strategy_name,
            direction="LONG",
            entry_price=150.0,
            stop_loss=145.0,
            take_profit=160.0,
            initial_risk=5.0,
            atr=2.0,
        )
        tg.send_trade_close_alert(
            symbol="SOLUSDT",
            strategy_name=strategy_name,
            direction="LONG",
            entry_price=150.0,
            exit_price=160.0,
            exit_reason="TP",
            net_return_pct=6.5,
            r_multiple=2.0,
            holding_bars=4,
        )

    assert len(sent_texts) == 2
    for body in sent_texts:
        assert strategy_name in body
        assert "SOLUSDT" in body


@pytest.mark.parametrize("strategy_name", ALL_RETAILBOT2_CANONICAL_NAMES)
def test_report_disabled_excluded_from_metrics_and_hourly_report(strategy_name):
    # Simulates SQL WHERE COALESCE(c.report_enabled, TRUE) = TRUE excluding `strategy_name`
    # while keeping another visible strategy/pair in the hourly report.
    class _Cursor:
        def __init__(self):
            self.queries = []
            self._call = 0

        def execute(self, sql, params=None):
            self.queries.append(sql)
            self._call += 1

        def fetchone(self):
            return {
                "total_trades": 2,
                "wins": 2,
                "losses": 0,
                "sum_r": 4.0,
                "avg_r": 2.0,
                "net_pnl": 100.0,
            }

        def fetchall(self):
            return [{
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1h",
                "strategy_name": "trend_ema_v1",
                "config_id": 1,
                "ml_generator_model_id": None,
                "ml_model_id": None,
                "total_trades": 2,
                "wins": 2,
                "losses": 0,
                "sum_r": 4.0,
                "avg_r": 2.0,
                "net_pnl": 100.0,
            }]

        def close(self):
            pass

    cursor = _Cursor()

    class _Conn:
        def cursor(self, dictionary=False):
            return cursor

        def close(self):
            pass

    class _Pool:
        def get_connection(self):
            return _Conn()

    with patch.object(pe, "get_pool", return_value=_Pool()):
        metrics = pe.get_paper_metrics()
        report_html = format_paper_report()

    for query in cursor.queries:
        assert "WHERE COALESCE(c.report_enabled, TRUE) = TRUE" in query
    assert all(row["strategy"] != strategy_name for row in metrics["byStrategy"])
    assert all(row["strategy"] != strategy_name for row in metrics["byStrategySymbol"])
    assert strategy_name not in report_html
    assert "BTCUSDT" in report_html
    assert "trend_ema_v1" in report_html


def test_get_paper_metrics_and_report_aggregate_by_strategy_and_symbol():
    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return {
                "total_trades": 5,
                "wins": 3,
                "losses": 2,
                "sum_r": 2.5,
                "avg_r": 0.5,
                "net_pnl": 175.0,
            }

        def fetchall(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "timeframe": "1h",
                    "strategy_name": "BB_REV_INV",
                    "config_id": 10,
                    "ml_generator_model_id": None,
                    "ml_model_id": None,
                    "total_trades": 3,
                    "wins": 2,
                    "losses": 1,
                    "sum_r": 1.5,
                    "avg_r": 0.5,
                    "net_pnl": 100.0,
                },
                {
                    "symbol": "BTCUSDT",
                    "market": "futures",
                    "timeframe": "4h",
                    "strategy_name": "BB_REV_INV",
                    "config_id": 11,
                    "ml_generator_model_id": None,
                    "ml_model_id": None,
                    "total_trades": 2,
                    "wins": 1,
                    "losses": 1,
                    "sum_r": 1.0,
                    "avg_r": 0.5,
                    "net_pnl": 75.0,
                },
            ]

        def close(self):
            pass

    class _Conn:
        def cursor(self, dictionary=False):
            return _Cursor()

        def close(self):
            pass

    class _Pool:
        def get_connection(self):
            return _Conn()

    with patch.object(pe, "get_pool", return_value=_Pool()):
        metrics = pe.get_paper_metrics()
        report_html = format_paper_report()

    assert metrics["byStrategySymbol"] == [{
        "strategy": "BB_REV_INV",
        "symbol": "BTCUSDT",
        "totalTrades": 5,
        "wins": 3,
        "losses": 2,
        "winRatePct": 60.0,
        "cumulativeR": 2.5,
        "expectancyR": 0.5,
        "netPnl": 175.0,
    }]
    assert "BTCUSDT" in report_html
    assert "BB_REV_INV" in report_html
    assert "5 closed · Wins 3 · Losses 2 · 60% win rate" in report_html


def test_migration_helper_dry_run_and_apply_preserve_rdt_tables(capsys):
    lifecycle_rows = [
        {"strategy_id": "BB_REV", "mode": "shadow", "evaluation_enabled": 1, "telegram_enabled": 0, "report_enabled": 1},
        {"strategy_id": "BB_REV", "mode": "inverse", "evaluation_enabled": 1, "telegram_enabled": 1, "report_enabled": 0},
        {"strategy_id": "BB_REV_INV", "mode": "inverse", "evaluation_enabled": 1, "telegram_enabled": 1, "report_enabled": 0},
        {"strategy_id": "COMPOSITE", "mode": "shadow", "evaluation_enabled": 0, "telegram_enabled": 0, "report_enabled": 1},
    ]
    existing_configs = [
        {
            "id": 5,
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "BB_REV",
            "is_active": 0,
            "allocated_capital": 8000.0,
            "risk_per_trade_pct": 1.5,
            "ml_model_id": None,
            "ml_generator_model_id": None,
            "telegram_enabled": 1,
            "report_enabled": 0,
            "archived_at": None,
        }
    ]
    executed = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchall(self):
            sql = executed[-1][0]
            if "FROM rdt_strategy_lifecycle" in sql:
                return lifecycle_rows
            if "FROM paper_configs" in sql:
                return existing_configs
            return []

        def close(self):
            pass

    class _Conn:
        def __init__(self):
            self.commits = 0

        def cursor(self, dictionary=False):
            return _Cursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            pass

        def close(self):
            pass

    class _Pool:
        def __init__(self, conn):
            self.conn = conn

        def get_connection(self):
            return self.conn

    # 1. Dry-run (default): prints plan and writes nothing
    conn_dry = _Conn()
    with patch.object(mig, "get_pool", return_value=_Pool(conn_dry)):
        rc = mig.main(["--pairs", "BTCUSDT,ETHUSDT:futures:4h"])
    assert rc == 0
    assert conn_dry.commits == 0
    out_dry = capsys.readouterr().out
    assert "DRY RUN: no changes written" in out_dry
    assert "UPDATE" in out_dry and "CREATE" in out_dry
    assert not any(
        word in sql.upper()
        for sql, _ in executed
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER")
    )

    assert mig.map_lifecycle_strategy_name("BB_REV_INV", "inverse") == "BB_REV_INV"
    # 2. --apply: writes ONLY to paper_configs, never mutates any rdt_* table
    executed.clear()
    conn_apply = _Conn()
    with patch.object(mig, "get_pool", return_value=_Pool(conn_apply)):
        rc_apply = mig.main(["--pairs", "BTCUSDT,ETHUSDT:futures:4h", "--apply"])
    assert rc_apply == 0
    assert conn_apply.commits == 1
    out_apply = capsys.readouterr().out
    assert "Applied: {'created': 7, 'updated': 1, 'unchanged': 0, 'skipped': 0}" in out_apply

    write_sqls = [
        sql for sql, _ in executed
        if any(sql.lstrip().upper().startswith(verb) for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER"))
    ]
    assert len(write_sqls) == 8
    assert all("paper_configs" in sql for sql in write_sqls)
    assert not any("rdt_" in sql.lower() for sql in write_sqls)
