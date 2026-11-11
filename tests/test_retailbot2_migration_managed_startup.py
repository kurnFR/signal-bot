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
        self.assertIn("026_retailbot2_telegram_outbox", versions)
        self.assertIn("027_retailbot2_outbox_claim_token", versions)
        self.assertIn("028_retailbot2_safe_control_defaults", versions)

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
        self.assertEqual(len(statements), 3)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        self.assertFalse(any(statement.startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE")) for statement in statements))
        self.assertIn("INFORMATION_SCHEMA.COLUMNS", statements[2])
        self.assertEqual(
            cursor.execute.call_args_list[2].args[1],
            ("rdt_telegram_outbox", "claim_token"),
        )
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
        # Migration 022 must seed trading disabled by default
        self.assertIn("enabled BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("VALUES (1, FALSE, NULL)", migration)

    def test_migration_028_safely_transitions_legacy_control_defaults(self):
        migration = Path("db/migrate_retailbot2_safe_control_defaults.sql").read_text(encoding="utf-8")
        normalized = " ".join(migration.split()).upper()
        self.assertIn("ALTER TABLE BOT_CONTROL MODIFY COLUMN ENABLED BOOLEAN NOT NULL DEFAULT FALSE", normalized)
        self.assertIn("UPDATE BOT_CONTROL SET ENABLED = FALSE WHERE ID = 1", normalized)

    def test_get_retailbot2_control_defaults_to_disabled_when_empty_or_legacy_unconfigured(self):
        pool = Mock()
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value = cursor
        pool.get_connection.return_value = conn

        with patch.object(db_helpers, "get_retailbot2_pool", return_value=pool):
            # 1. Missing row defaults to disabled
            cursor.fetchone.return_value = None
            ctrl = db_helpers.get_retailbot2_control()
            self.assertFalse(ctrl["enabled"])

            # 2. Legacy unconfigured row (overrides_json IS NULL) defaults to disabled
            cursor.fetchone.return_value = {"enabled": 1, "overrides_json": None, "updated_at": None}
            ctrl_legacy = db_helpers.get_retailbot2_control()
            self.assertFalse(ctrl_legacy["enabled"])

            # 3. Explicitly configured row preserves enabled=True
            cursor.fetchone.return_value = {"enabled": 1, "overrides_json": '{"risk_per_trade": 0.02}', "updated_at": None}
            ctrl_saved = db_helpers.get_retailbot2_control()
            self.assertTrue(ctrl_saved["enabled"])
            self.assertEqual(ctrl_saved["overrides"], {"risk_per_trade": 0.02})

            # 4. Corrupted/malformed JSON fails closed
            cursor.fetchone.return_value = {"enabled": 1, "overrides_json": '{"risk_per_trade": corrupted', "updated_at": None}
            ctrl_corrupt = db_helpers.get_retailbot2_control()
            self.assertFalse(ctrl_corrupt["enabled"])
            self.assertEqual(ctrl_corrupt["overrides"], {})

            # 5. Non-dict JSON fails closed
            cursor.fetchone.return_value = {"enabled": 1, "overrides_json": '"scalar_string"', "updated_at": None}
            ctrl_nondict = db_helpers.get_retailbot2_control()
            self.assertFalse(ctrl_nondict["enabled"])
            self.assertEqual(ctrl_nondict["overrides"], {})


if __name__ == "__main__":
    unittest.main()
