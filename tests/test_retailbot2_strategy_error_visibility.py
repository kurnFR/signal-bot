from unittest.mock import Mock

from paper.retailbot2 import RetailStrategyEngine


def test_run_all_logs_strategy_exception_instead_of_treating_it_as_no_signal():
    engine = object.__new__(RetailStrategyEngine)
    engine.strat_rsi_counter_trend = Mock(side_effect=ValueError("bad indicator data"))

    with __import__("unittest").TestCase().assertLogs(
        "paper.retailbot2", level="ERROR"
    ) as captured:
        signals = engine.run_all(
            df=None,
            symbol="BTC/USDT",
            enabled_strategies={"RSI_CTR"},
        )

    assert signals == []
    assert any("strategy=RSI_CTR symbol=BTC/USDT" in line for line in captured.output)
    assert any("bad indicator data" in line for line in captured.output)
    engine.strat_rsi_counter_trend.assert_called_once_with(None, "BTC/USDT")


def test_run_all_does_not_call_disabled_strategy_functions():
    engine = object.__new__(RetailStrategyEngine)
    engine.strat_rsi_counter_trend = Mock(return_value=None)
    engine.strat_ma_crossover = Mock(return_value=None)

    signals = engine.run_all(
        df=None,
        symbol="BTC/USDT",
        enabled_strategies=set(),
    )

    assert signals == []
    engine.strat_rsi_counter_trend.assert_not_called()
    engine.strat_ma_crossover.assert_not_called()
