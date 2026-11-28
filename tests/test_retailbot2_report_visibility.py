from decimal import Decimal


def test_paper_trade_stats_respect_deployment_report_visibility(monkeypatch):
    """The per-deployment report-visibility rule moved with migration 041:
    `paper_trades` rows join `paper_configs` on `config_id` and hide
    `report_enabled=False` deployments, replacing the old
    `rdt_strategy_lifecycle` join. The visible row must still aggregate,
    and the join must be scoped to the deployment (config_id), so hiding
    one deployment never hides the same strategy on another pair."""
    from db import db as db_module

    rows = [
        {
            "strategy": "BB_REV_INV",
            "mode": "paper",
            "total_trades": 10,
            "wins": 6,
            "losses": 4,
            "total_pnl": Decimal("125.50"),
            "last_updated": None,
        }
    ]

    class _Cursor:
        def __init__(self):
            self.queries = []
            self.fetchall_index = 0

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            # get_paper_trade_stats runs 3 queries: per-strategy rows, open
            # counts, per-mode aggregates. Only the first returns rows here.
            result = [rows, [], []][self.fetchall_index]
            self.fetchall_index += 1
            return result

        def fetchone(self):
            # Open-counts query returns a single row (no rows in this fixture).
            if len(self.queries) == 2:
                return {"open_count": 0}
            return {"realized_pnl": Decimal("0"), "closed_count": 0, "wins": 0}

        def close(self):
            self.closed = True

    cursor = _Cursor()

    class _Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return cursor

        def close(self):
            self.closed = True

    class _Pool:
        def get_connection(self):
            return _Connection()

    monkeypatch.setattr(db_module, "get_pool", lambda: _Pool())

    stats = db_module.get_paper_trade_stats()

    row = next(r for r in stats["by_strategy"] if r["strategy"] == "BB_REV_INV")
    assert row["total_trades"] == 10
    assert row["total_pnl"] == 125.5
    assert row["mode"] == "paper"
    strategy_query = cursor.queries[0]
    assert "FROM paper_trades t" in strategy_query
    assert "LEFT JOIN paper_configs c ON c.id = t.config_id" in strategy_query
    assert "COALESCE(c.report_enabled, TRUE) = TRUE" in strategy_query
    assert stats["by_mode"]["paper"]["open_count"] == 0


def test_report_mode_aggregates_exclude_hidden_deployments_but_keep_open_counts(monkeypatch):
    from db import db as db_module

    class Cursor:
        def __init__(self):
            self.queries = []
            self.fetchall_index = 0
            self.fetchone_index = 0

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            rows = [
                [{
                    "strategy": "VISIBLE_STRATEGY", "mode": "paper",
                    "total_trades": 2, "wins": 1, "losses": 1,
                    "total_pnl": Decimal("15.00"), "last_updated": None,
                }],
            ]
            result = rows[self.fetchall_index]
            self.fetchall_index += 1
            return result

        def fetchone(self):
            results = [
                {"open_count": 1},
                {"realized_pnl": Decimal("15.00"), "closed_count": 2, "wins": 1},
            ]
            result = results[self.fetchone_index]
            self.fetchone_index += 1
            return result

        def close(self):
            pass

    cursor = Cursor()

    class Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return cursor

        def commit(self):
            pass

        def close(self):
            pass

    class Pool:
        def get_connection(self):
            return Connection()

    monkeypatch.setattr(db_module, "get_pool", lambda: Pool())

    result = db_module.get_paper_trade_stats()

    # Report-level mode aggregates must use the same visibility rule as the
    # per-strategy rows and visible open-position counts.
    open_query = next(query for query in cursor.queries if "AS open_count" in query)
    assert "FROM paper_positions p" in open_query
    assert "LEFT JOIN paper_configs c ON c.id = p.config_id" in open_query
    assert "COALESCE(c.report_enabled, TRUE) = TRUE" in open_query
    mode_query = next(
        query for query in cursor.queries
        if "realized_pnl" in query and "closed_count" in query
    )
    assert "FROM paper_trades t" in mode_query
    assert "LEFT JOIN paper_configs c ON c.id = t.config_id" in mode_query
    assert "COALESCE(c.report_enabled, TRUE) = TRUE" in mode_query
    assert result["by_mode"]["paper"]["realized_pnl"] == 15.0
    assert result["by_mode"]["paper"]["closed_count"] == 2
    assert result["by_mode"]["paper"]["open_count"] == 1



def test_recent_closed_trade_rows_respect_deployment_report_visibility(monkeypatch):
    from db import db as db_module

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            return [{
                "id": 1, "symbol": "BTCUSDT", "market": "spot", "timeframe": "1h",
                "direction": "LONG",
                "entry_time": 1792452000000, "entry_price": Decimal("100"),
                "stop_loss": Decimal("95"), "take_profit": Decimal("110"),
                "quantity": Decimal("1"), "strategy_name": "BB_REV_INV",
                "exit_time": 1792455600000, "exit_price": Decimal("110"),
                "exit_reason": "TP", "net_pnl": Decimal("10"),
                "net_return_pct": Decimal("10"),
            }]

        def close(self):
            pass

    cursor = Cursor()

    class Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return cursor

        def commit(self):
            self.committed = True

        def close(self):
            pass

    class Pool:
        def get_connection(self):
            return Connection()

    monkeypatch.setattr(db_module, "get_pool", lambda: Pool())

    rows = db_module.get_paper_recent_trades(limit=20)

    assert len(rows) == 1
    assert rows[0]["mode"] == "paper"
    assert rows[0]["market"] == "spot"
    # The recent-trades read itself is one joined query, so visibility is
    # applied atomically with the closed-trade selection (no separate
    # setup query).
    assert len(cursor.queries) == 1
    recent_query = cursor.queries[0]
    assert "FROM paper_trades t" in recent_query
    assert "LEFT JOIN paper_configs c ON c.id = t.config_id" in recent_query
    assert "COALESCE(c.report_enabled, TRUE) = TRUE" in recent_query
