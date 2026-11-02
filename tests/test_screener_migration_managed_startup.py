"""Smart Money Screener schema is migration-managed, not created by startup."""
import inspect
import unittest
from unittest.mock import Mock

from db.migrate import MIGRATIONS
from Screen import screening
from web.app import lifespan


class TestScreenerMigrationManagedStartup(unittest.TestCase):
    def test_screener_runtime_schema_migration_is_registered(self):
        self.assertIn("023_screener_runtime_schema", [m.version for m in MIGRATIONS])

    def test_web_and_worker_startup_only_verify_screener_schema(self):
        app_source = inspect.getsource(lifespan)
        worker_source = inspect.getsource(screening.main)
        self.assertIn("verify_signal_schema", app_source)
        self.assertIn("verify_signal_schema", worker_source)
        self.assertNotIn("ensure_signal_table", app_source)
        self.assertNotIn("ensure_signal_table", worker_source)

    def test_screener_verifier_executes_only_information_schema_reads(self):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [
                {"table_name": "smart_money_signals"},
                {"table_name": "screener_control"},
                {"table_name": "screener_heartbeat"},
            ],
            [
                {"table_name": "smart_money_signals", "column_name": name, "column_type": col_type}
                for name, col_type in (
                    ("quality_score", "decimal(6,2)"),
                    ("direction", "varchar(10)"),
                    ("taker_buy_ratio", "decimal(8,6)"),
                    ("htf_alignment", "tinyint(4)"),
                    ("htf_context", "varchar(64)"),
                    ("delivery_status", "varchar(16)"),
                    ("delivery_attempts", "int(11)"),
                    ("next_attempt_at", "datetime"),
                    ("delivery_updated_at", "datetime"),
                    ("delivered_at", "datetime"),
                    ("last_delivery_error", "varchar(500)"),
                    ("candle_time", "varchar(32)"),
                )
            ] + [
                {"table_name": "screener_heartbeat", "column_name": "timeframe", "column_type": "varchar(20)"}
            ],
        ]
        conn = Mock()
        conn.cursor.return_value = cursor
        with unittest.mock.patch.object(screening, "get_pool") as get_pool:
            get_pool.return_value.get_connection.return_value = conn
            screening.verify_signal_schema()

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        conn.close.assert_called_once()
        cursor.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
