from types import SimpleNamespace

from paper.retailbot2 import RetailDeathTrapBot


class _StateDB:
    def __init__(self, trade):
        self.trade = trade

    def load_open_trades(self):
        return [self.trade]

    def get_realized_pnl_by_mode(self):
        return {}

    def get_last_trade_times(self):
        return {}


def _restore_trade(trade):
    bot = object.__new__(RetailDeathTrapBot)
    bot.db = _StateDB(trade)
    bot.active_shadow = {}
    bot.active_inverse = {}
    bot.config = SimpleNamespace(initial_capital=1000.0)
    bot.equity_shadow = 1000.0
    bot.equity_inverse = 1000.0
    bot.last_trade_time = {}
    bot.logger = SimpleNamespace(info=lambda *args, **kwargs: None)
    bot._load_state()
    return bot


def _trade(**overrides):
    row = {
        "id": 7,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "mode": "shadow",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "units": 1.0,
        "strategy": "BB_REV",
    }
    row.update(overrides)
    return row


def test_restored_legacy_trade_without_alert_decision_fails_closed():
    bot = _restore_trade(_trade())

    assert bot.active_shadow[7]["telegram_enabled"] is False


def test_restored_trade_preserves_explicit_telegram_decision():
    bot = _restore_trade(_trade(telegram_enabled=True))

    assert bot.active_shadow[7]["telegram_enabled"] is True
