import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from paper.retailbot2 import DatabaseManager, RetailDeathTrapBot


class FakeCursor:
    def __init__(self, fail_on_execute=None, rowcount=1):
        self.fail_on_execute = fail_on_execute
        self.rowcount = rowcount
        self.calls = 0
        self.execute_calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls += 1
        self.execute_calls.append((sql, params))
        if self.fail_on_execute == self.calls:
            raise RuntimeError(f"execute failure {self.calls}")

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail_on_execute=None, rowcount=1):
        self.cursor_obj = FakeCursor(fail_on_execute, rowcount)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def make_db(connection):
    db = object.__new__(DatabaseManager)
    db.logger = Mock()
    db.get_conn = Mock(return_value=connection)
    return db


def make_bot(db, trade, equity_shadow=1000.0, equity_inverse=2000.0):
    bot = object.__new__(RetailDeathTrapBot)
    bot.db = db
    bot.logger = Mock()
    bot.tg = Mock()
    bot.active_shadow = {1: dict(trade)}
    bot.active_inverse = {}
    bot.equity_shadow = equity_shadow
    bot.equity_inverse = equity_inverse
    return bot


class TestRetailBotDatabaseConfig(unittest.TestCase):
    def test_default_database_is_crypto_signals_even_when_generic_db_name_is_set(self):
        with patch.dict(os.environ, {"DB_NAME": "Binance", "RETAILBOT2_DB_NAME": "Binance", "MYSQL_DATABASE": "crypto_signals"}, clear=False):
            from paper.retailbot2 import BotConfig
            config = BotConfig()
        self.assertEqual(config.db_name, "crypto_signals")



class StatsCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None
        self.closed = False

    def execute(self, sql, params=None):
        self.executed_sql = sql

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class StatsConnection:
    def __init__(self, rows):
        self.cursor_obj = StatsCursor(rows)
        self.closed = False

    def cursor(self, dictionary=False):
        self.dictionary = dictionary
        return self.cursor_obj

    def close(self):
        self.closed = True


class TestRetailBotLedgerStats(unittest.TestCase):
    def test_get_stats_aggregates_closed_trade_ledger_not_summary_cache(self):
        conn = StatsConnection([
            {
                "strategy": "RSI_CTR",
                "mode": "shadow",
                "total_trades": 3,
                "wins": 2,
                "losses": 1,
                "total_pnl": Decimal("12.50"),
            }
        ])
        db = make_db(conn)

        stats = db.get_stats()

        self.assertEqual(
            stats,
            {
                "RSI_CTR_shadow": {
                    "total": 3,
                    "wins": 2,
                    "losses": 1,
                    "net_pnl": 12.5,
                }
            },
        )
        self.assertIn("FROM rdt_trades", conn.cursor_obj.executed_sql)
        self.assertIn("WHERE status='CLOSED'", conn.cursor_obj.executed_sql)
        self.assertNotIn("FROM rdt_strategy_stats", conn.cursor_obj.executed_sql)
        self.assertTrue(conn.closed)
        self.assertTrue(conn.cursor_obj.closed)


class TestRetailBotAtomicClose(unittest.TestCase):
    def test_successful_close_commits_trade_and_stats_atomically(self):
        conn = FakeConnection()
        db = make_db(conn)

        ok = db.close_trade_and_update_stats(
            7, 110.0, "TAKE_PROFIT", 100.0, 10.0, "RSI_CTR", "shadow"
        )

        self.assertTrue(ok)
        self.assertEqual(conn.cursor_obj.calls, 2)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertTrue(conn.cursor_obj.closed)
        self.assertTrue(conn.closed)

    def test_trade_update_failure_rolls_back_without_commit(self):
        conn = FakeConnection(fail_on_execute=1)
        db = make_db(conn)

        ok = db.close_trade_and_update_stats(
            7, 110.0, "TAKE_PROFIT", 100.0, 10.0, "RSI_CTR", "shadow"
        )

        self.assertFalse(ok)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.cursor_obj.calls, 1)

    def test_stats_update_failure_rolls_back_trade_update(self):
        conn = FakeConnection(fail_on_execute=2)
        db = make_db(conn)

        ok = db.close_trade_and_update_stats(
            7, 110.0, "TAKE_PROFIT", 100.0, 10.0, "RSI_CTR", "shadow"
        )

        self.assertFalse(ok)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.cursor_obj.calls, 2)

    def test_close_requires_open_trade_and_matching_mode(self):
        conn = FakeConnection(rowcount=0)
        db = make_db(conn)

        ok = db.close_trade_and_update_stats(
            7, 110.0, "TAKE_PROFIT", 100.0, 10.0, "RSI_CTR", "inverse"
        )

        self.assertFalse(ok)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.cursor_obj.calls, 1)
        sql, params = conn.cursor_obj.execute_calls[0]
        self.assertIn("status='OPEN'", sql)
        self.assertIn("mode=%s", sql)
        self.assertEqual(params[-2:], (7, "inverse"))

    def test_close_rejects_missing_trade_row_without_commit(self):
        conn = FakeConnection(rowcount=0)
        db = make_db(conn)

        ok = db.close_trade_and_update_stats(
            7, 110.0, "TAKE_PROFIT", 100.0, 10.0, "RSI_CTR", "shadow"
        )

        self.assertFalse(ok)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.cursor_obj.calls, 1)

    def test_equity_changes_only_after_persistence_commit(self):
        db = Mock()
        db.close_trade_and_update_stats.return_value = True
        trade = {
            "units": 10.0,
            "side": "LONG",
            "entry": 100.0,
            "strategy": "RSI_CTR",
            "symbol": "BTC/USDT",
        }
        bot = make_bot(db, trade)

        bot._close_trade(1, 110.0, "TAKE_PROFIT", "shadow")

        self.assertEqual(bot.equity_shadow, 1100.0)
        self.assertNotIn(1, bot.active_shadow)
        db.close_trade_and_update_stats.assert_called_once()

    def test_persistence_failure_preserves_active_trade_and_equity(self):
        db = Mock()
        db.close_trade_and_update_stats.return_value = False
        trade = {
            "units": 10.0,
            "side": "LONG",
            "entry": 100.0,
            "strategy": "RSI_CTR",
            "symbol": "BTC/USDT",
        }
        bot = make_bot(db, trade)

        bot._close_trade(1, 110.0, "TAKE_PROFIT", "shadow")

        self.assertEqual(bot.equity_shadow, 1000.0)
        self.assertIn(1, bot.active_shadow)
        bot.tg.send_exit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
