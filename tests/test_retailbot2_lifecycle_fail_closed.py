import logging
from unittest.mock import Mock

from paper.retailbot2 import RetailDeathTrapBot


def test_lifecycle_database_failure_fails_closed_even_with_stale_enabled_settings():
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.lifecycle")
    bot.db = Mock()
    bot.db.get_strategy_lifecycle_settings.side_effect = RuntimeError("database unavailable")
    bot.strategy_lifecycle = {
        ("RSI_CTR", "shadow"): {
            "evaluation_enabled": True,
            "paper_enabled": True,
            "telegram_enabled": True,
            "report_enabled": True,
            "auto_promote_enabled": True,
        }
    }
    bot._strategy_lifecycle_reload_at = 0.0
    bot._strategy_lifecycle_unavailable = False

    settings = bot._get_strategy_lifecycle("RSI_CTR", "shadow")

    assert settings["evaluation_enabled"] is False
    assert settings["paper_enabled"] is False
    assert settings["telegram_enabled"] is False
    assert settings["report_enabled"] is True
    assert settings["auto_promote_enabled"] is False
    assert bot._strategy_lifecycle_unavailable is True


def test_lifecycle_database_recovery_restores_saved_settings(monkeypatch):
    bot = object.__new__(RetailDeathTrapBot)
    bot.logger = logging.getLogger("test.retailbot2.lifecycle")
    bot.db = Mock()
    bot.db.get_strategy_lifecycle_settings.return_value = [{
        "strategy_id": "RSI_CTR",
        "mode": "shadow",
        "evaluation_enabled": True,
        "paper_enabled": True,
        "telegram_enabled": False,
        "report_enabled": False,
        "auto_promote_enabled": False,
    }]
    bot.strategy_lifecycle = {}
    bot._strategy_lifecycle_reload_at = 0.0
    bot._strategy_lifecycle_unavailable = True
    monkeypatch.setattr("paper.retailbot2.time.monotonic", lambda: 100.0)

    settings = bot._get_strategy_lifecycle("RSI_CTR", "shadow")

    assert settings["evaluation_enabled"] is True
    assert settings["paper_enabled"] is True
    assert settings["telegram_enabled"] is False
    assert settings["report_enabled"] is False
    assert bot._strategy_lifecycle_unavailable is False
