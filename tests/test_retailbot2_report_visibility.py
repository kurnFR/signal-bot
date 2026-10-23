from decimal import Decimal
from types import SimpleNamespace

from paper.retailbot2 import DatabaseManager


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.closed = False

    def execute(self, query, params=None):
        self.query = query

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, rows):
        self.cur = _Cursor(rows)
        self.closed = False

    def cursor(self, dictionary=False):
        assert dictionary is True
        return self.cur

    def close(self):
        self.closed = True


def test_telegram_performance_stats_respect_strategy_report_visibility():
    connection = _Connection([
        {
            "strategy": "BB_REV_INV",
            "mode": "inverse",
            "total_trades": 10,
            "wins": 6,
            "losses": 4,
            "total_pnl": Decimal("125.50"),
        }
    ])
    db = object.__new__(DatabaseManager)
    db.logger = SimpleNamespace(error=lambda *args, **kwargs: None)
    db.get_conn = lambda: connection

    stats = db.get_stats()

    assert stats["BB_REV_INV_inverse"]["total"] == 10
    assert stats["BB_REV_INV_inverse"]["net_pnl"] == 125.5
    assert "LEFT JOIN rdt_strategy_lifecycle" in connection.cur.query
    assert "COALESCE(l.report_enabled, TRUE) = TRUE" in connection.cur.query
    assert connection.cur.closed is True
    assert connection.closed is True
