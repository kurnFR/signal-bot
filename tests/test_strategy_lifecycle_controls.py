from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from paper.retailbot2 import RetailDeathTrapBot, RetailStrategyEngine


class FakeLifecycleDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.count_queries = []

    def get_strategy_lifecycle_settings(self):
        return self.rows

    def count_open(self, *, mode=None, symbol=None, strategy=None):
        self.count_queries.append((mode, symbol, strategy))
        return 0


def _bot_for_lifecycle(rows=None):
    bot = object.__new__(RetailDeathTrapBot)
    bot.db = FakeLifecycleDB(rows)
    bot.strategy_lifecycle = {}
    bot._strategy_lifecycle_reload_at = 0.0
    return bot


def test_strategy_lifecycle_defaults_keep_research_on_and_telegram_off():
    bot = _bot_for_lifecycle()

    settings = bot._get_strategy_lifecycle("BB_REV", "shadow")

    assert settings["evaluation_enabled"] is True
    assert settings["paper_enabled"] is True
    assert settings["report_enabled"] is True
    assert settings["telegram_enabled"] is False
    assert settings["auto_promote_enabled"] is False


def test_strategy_lifecycle_reads_saved_per_mode_promotion_flags():
    bot = _bot_for_lifecycle([{
        "strategy_id": "BB_REV_INV",
        "mode": "inverse",
        "evaluation_enabled": 1,
        "paper_enabled": 1,
        "telegram_enabled": 1,
        "report_enabled": 0,
        "auto_promote_enabled": 0,
    }])

    settings = bot._get_strategy_lifecycle("BB_REV_INV", "inverse")

    assert settings["telegram_enabled"] is True
    assert settings["report_enabled"] is False
    assert bot._get_strategy_lifecycle("BB_REV", "shadow")["telegram_enabled"] is False


def test_strategy_trade_limits_do_not_block_other_strategy_on_same_symbol():
    bot = object.__new__(RetailDeathTrapBot)
    bot.config = SimpleNamespace(
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=2,
    )
    bot.active_shadow = {
        1: {"symbol": "BTC/USDT", "strategy": "BB_REV"}
    }
    bot.active_inverse = {}
    bot.last_trade_time = {
        "BTC/USDT": {"shadow": {"BB_REV": datetime.now() - timedelta(minutes=10)}, "inverse": {}}
    }
    bot.db = FakeLifecycleDB()

    allowed, reason = bot._can_open("BTC/USDT", "shadow", "DBL_PAT")

    assert allowed is True
    assert reason == "OK"
    assert bot.db.count_queries == [("shadow", "BTC/USDT", "DBL_PAT")]


def test_same_strategy_symbol_trade_is_limited_independently():
    bot = object.__new__(RetailDeathTrapBot)
    bot.config = SimpleNamespace(
        max_open_trades_per_mode=8,
        max_trades_per_symbol=1,
        min_trade_interval_hours=2,
    )
    bot.active_shadow = {
        1: {"symbol": "BTC/USDT", "strategy": "BB_REV"}
    }
    bot.active_inverse = {}
    bot.last_trade_time = {"BTC/USDT": {"shadow": {}, "inverse": {}}}
    bot.db = FakeLifecycleDB()

    allowed, reason = bot._can_open("BTC/USDT", "shadow", "BB_REV")

    assert allowed is False
    assert "already has an open" in reason
    assert bot.db.count_queries == []


def test_run_all_skips_strategy_functions_disabled_in_all_modes():
    engine = object.__new__(RetailStrategyEngine)
    signal = {"strategy": "BB_REV", "side": "LONG"}
    method_names = (
        "strat_rsi_counter_trend",
        "strat_ma_crossover",
        "strat_sr_bounce",
        "strat_macd_divergence",
        "strat_bband_reversion",
        "strat_double_pattern",
        "strat_spike_fade",
    )
    mocks = {}
    for name in method_names:
        result = signal if name == "strat_bband_reversion" else None
        mocks[name] = Mock(return_value=result)
        setattr(engine, name, mocks[name])

    results = engine.run_all(object(), "BTC/USDT", enabled_strategies={"BB_REV"})

    assert results == [signal]
    assert mocks["strat_bband_reversion"].call_count == 1
    assert all(
        mock.call_count == 0
        for name, mock in mocks.items()
        if name != "strat_bband_reversion"
    )


def test_run_all_empty_enabled_set_evaluates_no_strategy_functions():
    engine = object.__new__(RetailStrategyEngine)
    mocks = []
    for name in (
        "strat_rsi_counter_trend",
        "strat_ma_crossover",
        "strat_sr_bounce",
        "strat_macd_divergence",
        "strat_bband_reversion",
        "strat_double_pattern",
        "strat_spike_fade",
    ):
        mock = Mock(return_value=None)
        setattr(engine, name, mock)
        mocks.append(mock)

    assert engine.run_all(object(), "BTC/USDT", enabled_strategies=set()) == []
    assert all(mock.call_count == 0 for mock in mocks)


def _bot_for_exit_alert(telegram_enabled_marker=None):
    bot = object.__new__(RetailDeathTrapBot)
    trade = {
        "id": 7,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "mode": "shadow",
        "entry": 100.0,
        "units": 2.0,
        "strategy": "BB_REV",
    }
    if telegram_enabled_marker is not None:
        trade["telegram_enabled"] = telegram_enabled_marker
    bot.active_shadow = {7: trade}
    bot.active_inverse = {}
    bot.db = Mock()
    bot.db.close_trade_and_update_stats.return_value = True
    bot.equity_shadow = 1000.0
    bot.equity_inverse = 1000.0
    bot.tg = Mock()
    bot.logger = Mock()
    return bot


def test_legacy_open_trade_without_persisted_telegram_decision_fails_closed():
    bot = _bot_for_exit_alert()

    bot._close_trade(7, 110.0, "target", "shadow")

    bot.tg.send_exit.assert_not_called()
    assert 7 not in bot.active_shadow
    assert bot.equity_shadow == 1020.0


def test_exit_alert_uses_persisted_entry_promotion_decision():
    bot = _bot_for_exit_alert(True)

    bot._close_trade(7, 110.0, "target", "shadow")

    bot.tg.send_exit.assert_called_once()

