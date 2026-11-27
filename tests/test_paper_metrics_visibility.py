from unittest.mock import patch

from paper.engine import get_paper_metrics


def test_paper_metrics_headline_uses_same_report_visibility_as_strategy_rows():
    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchone(self):
            return {
                "total_trades": 2,
                "wins": 1,
                "losses": 1,
                "sum_r": 0.5,
                "avg_r": 0.25,
                "net_pnl": 15.0,
            }

        def fetchall(self):
            if "GROUP BY t.exit_reason" in self.queries[-1]:
                return [{
                    "exit_reason": "TP", "trades": 1, "wins": 1, "losses": 0,
                    "net_pnl": 20.0, "sum_r": 2.0,
                }, {
                    "exit_reason": "TRAIL", "trades": 1, "wins": 0, "losses": 0,
                    "net_pnl": -5.0, "sum_r": -0.5,
                }]
            return [{
                "strategy_name": "visible_strategy", "config_id": 11,
                "ml_generator_model_id": "generator-1", "ml_model_id": None,
                "total_trades": 2,
                "wins": 1,
                "losses": 1,
                "sum_r": 0.5,
                "avg_r": 0.25,
                "net_pnl": 15.0,
                "pure_trades": 1, "pure_wins": 1, "pure_losses": 0,
                "pure_sum_r": 2.0, "pure_net_pnl": 20.0,
                "pure_win_sum_r": 2.0, "pure_loss_sum_r": 0.0,
            }]

        def close(self):
            pass

    cursor = Cursor()

    class Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return cursor

        def close(self):
            pass

    class Pool:
        def get_connection(self):
            return Connection()

    with patch("paper.engine.get_pool", return_value=Pool()):
        metrics = get_paper_metrics()

    assert len(cursor.queries) == 3
    summary_query, strategy_query, breakdown_query = cursor.queries
    for query in (summary_query, strategy_query, breakdown_query):
        assert "LEFT JOIN paper_configs c ON c.id = t.config_id" in query
        assert "WHERE COALESCE(c.report_enabled, TRUE) = TRUE" in query
    assert "GROUP BY t.exit_reason" in breakdown_query
    # The breakdown query reuses the same report-visibility filter.

    assert metrics["totalTrades"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["winRatePct"] == 50.0
    assert metrics["netPnl"] == 15.0
    assert [row["strategy"] for row in metrics["byStrategy"]] == ["visible_strategy"]
    assert metrics["byStrategy"][0]["configId"] == 11
    assert metrics["byStrategy"][0]["ml_generator_model_id"] == "generator-1"
    # Pure aggregates count only the TP row; the TRAIL row is excluded.
    assert metrics["byStrategy"][0]["pureTrades"] == 1
    assert metrics["byStrategy"][0]["pureWins"] == 1
    assert metrics["byStrategy"][0]["pureWinRatePct"] == 100.0
    breakdown = {row["exitReason"]: row for row in metrics["exitReasonBreakdown"]}
    assert breakdown["TP"]["trades"] == 1
    assert breakdown["TRAIL"]["trades"] == 1
    assert breakdown["SL"]["trades"] == 0



def test_recent_paper_trades_respect_report_visibility(monkeypatch):
    from paper import engine

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            return [{
                "id": 7, "position_id": 12, "symbol": "BTC/USDT", "market": "spot",
                "timeframe": "1h", "strategy_name": "hidden_strategy", "direction": "LONG",
                "entry_time_wib": None, "exit_time_wib": None, "created_at": None,
            }]

        def close(self):
            pass

    cursor = Cursor()

    class Connection:
        def cursor(self, dictionary=False):
            assert dictionary is True
            return cursor

        def close(self):
            pass

    class Pool:
        def get_connection(self):
            return Connection()

    monkeypatch.setattr(engine, "get_pool", lambda: Pool())

    rows = engine.get_paper_trades(limit=20)

    assert len(rows) == 1
    assert "LEFT JOIN paper_configs c ON c.id = t.config_id" in cursor.queries[0]
    assert "COALESCE(c.report_enabled, TRUE) = TRUE" in cursor.queries[0]
