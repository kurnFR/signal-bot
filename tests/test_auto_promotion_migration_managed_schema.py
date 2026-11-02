"""Auto-promotion persistence relies on versioned migrations, not runtime DDL."""
import inspect
import unittest
from unittest.mock import Mock, patch

from db import db as db_helpers
from db.migrate import MIGRATIONS
from web.app import lifespan


_POLICY = {
    "id": 1,
    "enabled": 0,
    "min_test_trades": 30,
    "min_profit_factor": 1.2,
    "min_expectancy_r": 0.05,
    "max_drawdown_r": 8.0,
    "min_rank_score": 0.1,
    "require_holdout": 1,
    "send_telegram": 1,
    "updated_at": None,
}


def _db_connection(fetchone=None, fetchall=None):
    cursor = Mock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    conn = Mock()
    conn.cursor.return_value = cursor
    return conn, cursor


class TestAutoPromotionMigrationManagedSchema(unittest.TestCase):
    def test_policy_and_audit_tables_are_migration_managed(self):
        self.assertIn("018_strategy_auto_promotion", [m.version for m in MIGRATIONS])

    def test_web_startup_verifies_auto_promotion_schema(self):
        source = inspect.getsource(lifespan)
        self.assertIn("verify_strategy_auto_promotion_schema", source)

    def test_missing_policy_row_uses_safe_defaults(self):
        conn, cursor = _db_connection(fetchone=None)
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            policy = db_helpers.get_strategy_auto_promotion_policy()

        self.assertFalse(policy["enabled"])
        self.assertTrue(policy["require_holdout"])
        self.assertTrue(policy["send_telegram"])
        self.assertIsNone(policy["updated_at"])

    def test_policy_read_executes_select_only_and_does_not_commit(self):
        conn, cursor = _db_connection(fetchone=dict(_POLICY))
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            db_helpers.get_strategy_auto_promotion_policy()

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT"))
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    def test_policy_write_executes_only_the_policy_upsert(self):
        conn, cursor = _db_connection()
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            db_helpers.set_strategy_auto_promotion_policy({
                "enabled": True,
                "min_test_trades": 30,
                "min_profit_factor": 1.2,
                "min_expectancy_r": 0.05,
                "max_drawdown_r": 8.0,
                "min_rank_score": 0.1,
                "require_holdout": True,
                "send_telegram": False,
            })

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("INSERT"))
        conn.commit.assert_called_once()

    def test_decision_read_executes_select_only(self):
        conn, cursor = _db_connection(fetchone={"decision_hash": "a" * 64, "status": "rejected"})
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            db_helpers.get_strategy_promotion_decision("a" * 64)

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT"))
        conn.commit.assert_not_called()

    def test_promotion_decision_insert_does_not_initialize_schema(self):
        conn, cursor = _db_connection()
        cursor.rowcount = 1
        decision = {
            "decision_hash": "a" * 64,
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "data_segment": "holdout",
            "strategy_name": "trend_ema_v1",
            "status": "rejected",
            "reason": "below threshold",
            "metrics_json": "{}",
            "criteria_json": "{}",
        }
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            self.assertTrue(db_helpers.record_strategy_promotion_decision(decision))

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("INSERT"))
        conn.commit.assert_called_once()

    def test_audit_read_executes_select_only(self):
        conn, cursor = _db_connection(fetchall=[])
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            self.assertEqual(db_helpers.get_strategy_promotion_audit(), [])

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT"))
        conn.commit.assert_not_called()

    def test_schema_verifier_executes_information_schema_reads_only(self):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [
                {"table_name": "strategy_auto_promotion_policy"},
                {"table_name": "strategy_promotion_audit"},
            ],
            [
                {"table_name": "strategy_auto_promotion_policy", "column_name": column}
                for column in (
                    "id", "enabled", "min_test_trades", "min_profit_factor",
                    "min_expectancy_r", "max_drawdown_r", "min_rank_score",
                    "require_holdout", "send_telegram", "updated_at",
                )
            ] + [
                {"table_name": "strategy_promotion_audit", "column_name": column}
                for column in (
                    "decision_hash", "symbol", "market", "timeframe", "data_segment",
                    "strategy_name", "status", "reason", "metrics_json", "criteria_json",
                    "paper_config_id", "telegram_result", "created_at", "updated_at",
                )
            ],
        ]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch.object(db_helpers, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            db_helpers.verify_strategy_auto_promotion_schema()

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        conn.close.assert_called_once()
        cursor.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
