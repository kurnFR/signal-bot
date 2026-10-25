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
    assert select_call.args[1] == ("BTCUSDT", "spot", "1d", "trend_ema_v1")
    connection.commit.assert_called_once()
    connection.close.assert_called_once()
