"""RetailBot2 schema is migration-managed, never mutated by runtime startup."""
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock

from db.migrate import MIGRATIONS
from paper.retailbot2 import DatabaseManager
from web.app import lifespan


class TestRetailBot2MigrationManagedStartup(unittest.TestCase):
    def test_retailbot2_schema_migration_is_registered(self):
        versions = [migration.version for migration in MIGRATIONS]
        self.assertIn("022_retailbot2_runtime_schema", versions)

    def test_web_lifespan_does_not_construct_schema_manager_or_run_ddl_helper(self):
        source = inspect.getsource(lifespan)
        self.assertNotIn("DatabaseManager(", source)
        self.assertNotIn("ensure_retailbot2_lifecycle_table", source)

    def test_database_manager_startup_does_not_call_runtime_table_creator(self):
        source = inspect.getsource(DatabaseManager._init_pool)
        self.assertNotIn("_create_tables(", source)
        self.assertIn("_verify_schema(", source)

    def test_database_manager_verification_uses_only_read_queries(self):
        manager = DatabaseManager.__new__(DatabaseManager)
        cursor = Mock()
        cursor.fetchall.return_value = [
            {"table_name": "rdt_trades"},
            {"table_name": "rdt_strategy_stats"},
            {"table_name": "bot_control"},
            {"table_name": "rdt_strategy_lifecycle"},
        ]
        cursor.fetchone.return_value = {"column_name": "telegram_enabled"}
        conn = Mock()
        conn.cursor.return_value = cursor
        manager.get_conn = Mock(return_value=conn)

        manager._verify_schema()

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        self.assertFalse(any(statement.startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE")) for statement in statements))
        conn.close.assert_called_once()
        cursor.close.assert_called_once()

    def test_migration_defines_all_runtime_tables_and_legacy_column(self):
        migration = Path("db/migrate_retailbot2_runtime_schema.sql").read_text(encoding="utf-8")
        for table in ("rdt_trades", "rdt_strategy_stats", "bot_control", "rdt_strategy_lifecycle"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        self.assertIn("ADD COLUMN telegram_enabled", migration)


if __name__ == "__main__":
    unittest.main()
