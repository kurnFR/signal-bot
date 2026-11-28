from decimal import Decimal
from types import SimpleNamespace



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


def test_telegram_performance_stats_respect_strategy_report_visibility(monkeypatch):
    """Step 3: DatabaseManager.get_stats() is deleted with the worker. The
    same visibility rule now lives in db.get_retailbot2_stats(), which the
    archive stats endpoint and the paper report read. Port the test to it:
    the per-strategy query must still join rdt_strategy_lifecycle and hide
    report_enabled=False rows, and the visible row must still aggregate."""
    from db import db as db_module

    rows = [
        {
            "strategy": "BB_REV_INV",
            "mode": "inverse",
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
            # get_retailbot2_stats runs 3 queries: per-strategy rows, open
            # counts, per-mode aggregates. Only the first returns rows here.
            result = [rows, [], []][self.fetchall_index]
            self.fetchall_index += 1
            return result

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

    monkeypatch.setattr(db_module, "get_retailbot2_pool", lambda: _Pool())

    stats = db_module.get_retailbot2_stats()

    row = next(r for r in stats["by_strategy"] if r["strategy"] == "BB_REV_INV")
    assert row["total_trades"] == 10
    assert row["total_pnl"] == 125.5
    strategy_query = cursor.queries[0]
    assert "LEFT JOIN rdt_strategy_lifecycle" in strategy_query
    # Visibility is scoped to the exact strategy/mode pair: hiding inverse
    # results must not hide the same strategy in shadow mode (or vice versa).
    assert "ON l.strategy_id = t.strategy AND l.mode = t.mode" in strategy_query
    assert "COALESCE(l.report_enabled, TRUE) = TRUE" in strategy_query


def test_lifecycle_database_readback_parses_string_flags_without_truthiness(monkeypatch):
    from db import db as db_module

    rows = [{
        "strategy_id": "BB_REV_INV",
        "mode": "inverse",
        "evaluation_enabled": "0",
        "paper_enabled": "false",
        "telegram_enabled": "1",
        "report_enabled": "false",
        "auto_promote_enabled": "1",
        "updated_at": None,
    }, {
        "strategy_id": "DBL_PAT",
        "mode": "shadow",
        "evaluation_enabled": "unexpected",
        "paper_enabled": None,
        "telegram_enabled": "true",
        "report_enabled": "0",
        "auto_promote_enabled": "false",
        "updated_at": None,
    }]

    class Cursor:
        def execute(self, query, params=None):
            self.query = query

        def fetchall(self):
            return [dict(row) for row in rows]

        def close(self):
            pass

    class Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    class Pool:
        def get_connection(self):
            return Connection()

    monkeypatch.setattr(db_module, "get_retailbot2_pool", lambda: Pool())

    settings = db_module.get_retailbot2_strategy_lifecycle_settings()
    by_key = {(row["strategy_id"], row["mode"]): row for row in settings}

    assert by_key[("BB_REV_INV", "inverse")]["evaluation_enabled"] is False
    assert by_key[("BB_REV_INV", "inverse")]["paper_enabled"] is False
    assert by_key[("BB_REV_INV", "inverse")]["telegram_enabled"] is True
    assert by_key[("BB_REV_INV", "inverse")]["report_enabled"] is False
    assert by_key[("BB_REV_INV", "inverse")]["auto_promote_enabled"] is True
    assert by_key[("DBL_PAT", "shadow")]["evaluation_enabled"] is False
    assert by_key[("DBL_PAT", "shadow")]["paper_enabled"] is False
    assert by_key[("DBL_PAT", "shadow")]["telegram_enabled"] is True
    assert by_key[("DBL_PAT", "shadow")]["report_enabled"] is False
    assert by_key[("DBL_PAT", "shadow")]["auto_promote_enabled"] is False


def test_report_mode_aggregates_exclude_hidden_strategies_but_keep_open_counts(monkeypatch):
    from db import db as db_module

    class Cursor:
        def __init__(self):
            self.queries = []
            self.fetchall_index = 0

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            rows = [
                [{
                    "strategy": "VISIBLE_STRATEGY", "mode": "inverse",
                    "total_trades": 2, "wins": 1, "losses": 1,
                    "total_pnl": Decimal("15.00"), "last_updated": None,
                }],
                [{"mode": "inverse", "open_count": 1}],
                [{
                    "mode": "inverse", "realized_pnl": Decimal("15.00"),
                    "closed_count": 2, "wins": 1,
                }],
            ]
            result = rows[self.fetchall_index]
            self.fetchall_index += 1
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

    monkeypatch.setattr(db_module, "get_retailbot2_pool", lambda: Pool())

    result = db_module.get_retailbot2_stats()

    # Report-level mode aggregates must use the same visibility rule as the
    # per-strategy rows and visible open-position counts.
    open_query = next(query for query in cursor.queries if "AS open_count" in query)
    assert "COALESCE(l.report_enabled, TRUE) = TRUE" in open_query
    mode_query = next(
        query for query in cursor.queries
        if "realized_pnl" in query and "closed_count" in query
    )
    assert "LEFT JOIN rdt_strategy_lifecycle" in mode_query
    assert "ON l.strategy_id = t.strategy AND l.mode = t.mode" in mode_query
    assert "COALESCE(l.report_enabled, TRUE) = TRUE" in mode_query
    assert result["by_mode"]["inverse"]["realized_pnl"] == 15.0
    assert result["by_mode"]["inverse"]["closed_count"] == 2
    assert result["by_mode"]["inverse"]["open_count"] == 1



def test_recent_closed_trade_rows_respect_strategy_report_visibility(monkeypatch):
    from db import db as db_module

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            return [{
                "id": 1, "symbol": "BTC/USDT", "side": "LONG", "mode": "inverse",
                "entry_price": Decimal("100"), "stop_price": Decimal("95"),
                "target_price": Decimal("110"), "units": Decimal("1"),
                "strategy": "BB_REV_INV", "confluence_score": 1, "status": "CLOSED",
                "exit_price": Decimal("110"), "exit_reason": "TAKE_PROFIT",
                "net_pnl": Decimal("10"), "pnl_percent": Decimal("10"),
                "entry_time": None, "exit_time": None,
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

    monkeypatch.setattr(db_module, "get_retailbot2_pool", lambda: Pool())

    rows = db_module.get_retailbot2_recent_trades(limit=20)

    assert len(rows) == 1
    # Lifecycle schema is provisioned by the migration/startup path. The
    # recent-trades read itself is one joined query, so visibility is applied
    # atomically with the closed-trade selection (no separate setup query).
    assert len(cursor.queries) == 1
    recent_query = cursor.queries[0]
    assert "SELECT t.id, t.symbol, t.side, t.mode" in recent_query
    assert "LEFT JOIN rdt_strategy_lifecycle" in recent_query
    assert "ON l.strategy_id = t.strategy AND l.mode = t.mode" in recent_query
    assert "COALESCE(l.report_enabled, TRUE) = TRUE" in recent_query
