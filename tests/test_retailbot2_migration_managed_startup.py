"""RetailBot2 schema is migration-managed, never mutated by runtime startup."""
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from db import db as db_helpers

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
            {"table_name": "rdt_telegram_outbox"},
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

    def test_legacy_ensure_helper_is_read_only_compatibility_alias(self):
        with patch.object(db_helpers, "verify_retailbot2_schema") as verify:
            db_helpers.ensure_retailbot2_lifecycle_table()
        verify.assert_called_once_with()

    def test_strategy_lifecycle_save_uses_migration_managed_table_and_commits(self):
        pool = Mock()
        cursor = Mock()
        conn = Mock()
        conn.cursor.return_value = cursor
        pool.get_connection.return_value = conn
        settings = {
            "evaluation_enabled": True,
            "paper_enabled": True,
            "telegram_enabled": True,
            "report_enabled": True,
            "auto_promote_enabled": False,
        }

        with patch.object(db_helpers, "get_retailbot2_pool", return_value=pool):
            db_helpers.set_retailbot2_strategy_lifecycle_setting(
                "BB_REV_INV", "inverse", settings
            )

        self.assertEqual(cursor.execute.call_count, 1)
        sql, params = cursor.execute.call_args.args
        normalized_sql = " ".join(sql.split()).upper()
        self.assertIn("INSERT INTO RDT_STRATEGY_LIFECYCLE", normalized_sql)
        self.assertIn("TELEGRAM_ENABLED", normalized_sql)
        self.assertNotIn("CREATE TABLE", normalized_sql)
        self.assertEqual(
            params,
            ("BB_REV_INV", "inverse", True, True, True, True, False),
        )
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        cursor.close.assert_called_once()
        conn.close.assert_called_once()

        # Catch regressions that reintroduce a missing runtime DDL helper.
        source = inspect.getsource(db_helpers.set_retailbot2_strategy_lifecycle_setting)
        self.assertNotIn("_ensure_retailbot2_strategy_lifecycle_table", source)

    def test_migration_defines_all_runtime_tables_and_legacy_column(self):
        migration = Path("db/migrate_retailbot2_runtime_schema.sql").read_text(encoding="utf-8")
        for table in ("rdt_trades", "rdt_strategy_stats", "bot_control", "rdt_strategy_lifecycle"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        self.assertIn("ADD COLUMN telegram_enabled", migration)


if __name__ == "__main__":
    unittest.main()
