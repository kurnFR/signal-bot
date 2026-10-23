from datetime import datetime, timedelta
from types import SimpleNamespace

from paper.retailbot2 import RetailDeathTrapBot


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
