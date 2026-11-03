from unittest.mock import Mock, patch

from paper.engine import set_paper_config


def test_set_paper_config_returns_persisted_config_id():
    cursor = Mock()
    cursor.fetchone.return_value = {"id": 42}
    connection = Mock()
    connection.cursor.return_value = cursor
    pool = Mock()
    pool.get_connection.return_value = connection

    with patch("paper.engine.get_pool", return_value=pool):
        result = set_paper_config(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1d",
            strategy_name="trend_ema_v1",
            is_active=True,
            allocated_capital=5000.0,
            risk_per_trade_pct=1.0,
            telegram_enabled=False,
            report_enabled=True,
        )

    assert result["id"] == 42
    select_call = next(
        call for call in cursor.execute.call_args_list
        if "SELECT id FROM paper_configs" in call.args[0]
    )
    assert select_call.args[1] == ("BTCUSDT", "spot", "1d", "trend_ema_v1", None, None)
    connection.commit.assert_called_once()
    connection.close.assert_called_once()

def test_set_paper_config_resolves_generator_config_by_both_model_ids():
    cursor = Mock()
    cursor.fetchone.return_value = {"id": 84}
    connection = Mock()
    connection.cursor.return_value = cursor
    pool = Mock()
    pool.get_connection.return_value = connection

    with patch("paper.engine.get_pool", return_value=pool):
        result = set_paper_config(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1h",
            strategy_name="ml_signal_generator_v1",
            is_active=False,
            ml_generator_model_id="generator-model-1",
            telegram_enabled=False,
            report_enabled=True,
        )

    assert result["id"] == 84
    select_call = next(
        call for call in cursor.execute.call_args_list
        if "SELECT id FROM paper_configs" in call.args[0]
    )
    assert select_call.args[1] == (
        "BTCUSDT", "spot", "1h", "ml_signal_generator_v1", None, "generator-model-1"
    )
    insert_call = next(
        call for call in cursor.execute.call_args_list
        if "INSERT INTO paper_configs" in call.args[0]
    )
    assert "ml_generator_model_id" in insert_call.args[0]
    assert "generator-model-1" in insert_call.args[1]

