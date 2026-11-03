from unittest.mock import patch

from notifications.telegram_notifier import send_signal_alert, send_trade_close_alert


def test_generator_model_is_identified_in_entry_alert_without_filter_metadata():
    with patch("notifications.telegram_notifier.send_telegram_message", return_value={"success": True}) as send:
        result = send_signal_alert(
            symbol="BTCUSDT",
            market="spot",
            timeframe="1h",
            strategy_name="ml_signal_generator_v1",
            direction="LONG",
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            ml_generator_model_id="generator-model-1",
        )

    assert result["success"] is True
    message = send.call_args.args[0]
    assert "Signal Generator Model" in message
    assert "generator-model-1" in message
    assert "Filter Model" not in message


def test_generator_model_is_identified_in_close_alert():
    with patch("notifications.telegram_notifier.send_telegram_message", return_value={"success": True}) as send:
        send_trade_close_alert(
            symbol="BTCUSDT",
            strategy_name="ml_signal_generator_v1",
            direction="LONG",
            entry_price=100.0,
            exit_price=105.0,
            exit_reason="TP",
            net_return_pct=4.5,
            r_multiple=1.0,
            holding_bars=3,
            ml_generator_model_id="generator-model-1",
        )

    message = send.call_args.args[0]
    assert "Signal Generator Model" in message
    assert "generator-model-1" in message
