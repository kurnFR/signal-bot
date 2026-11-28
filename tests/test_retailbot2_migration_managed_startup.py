"""RetailBot2 archive schema is migration-managed, never mutated by runtime startup.

Step 3: the worker (paper/retailbot2.py) is deleted. The store checks below
now target paper/outbox_store.OutboxStore, the only remaining runtime
owner of the rdt_telegram_outbox table; the lifecycle-write test is gone
with set_retailbot2_strategy_lifecycle_setting."""
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from db import db as db_helpers

from db.migrate import MIGRATIONS
from paper.outbox_store import OutboxStore
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

    def test_outbox_store_startup_does_not_call_runtime_table_creator(self):
        source = inspect.getsource(OutboxStore._init_pool)
        self.assertNotIn("_create_tables(", source)
        self.assertIn("_verify_schema(", source)

    def test_outbox_store_verification_uses_only_read_queries(self):
        # The store verifies only its own table (rdt_telegram_outbox +
        # claim_token column); the other rdt_* tables are archive-only and
        # are verified by db.db.verify_retailbot2_schema().
        store = OutboxStore.__new__(OutboxStore)
        cursor = Mock()
        cursor.fetchone.return_value = {"table_name": "rdt_telegram_outbox"}
        conn = Mock()
        conn.cursor.return_value = cursor
        store.get_conn = Mock(return_value=conn)

        store._verify_schema()

        statements = [call.args[0].lstrip().upper() for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        self.assertFalse(any(statement.startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE")) for statement in statements))
        self.assertIn("INFORMATION_SCHEMA.TABLES", statements[0])
        self.assertIn("INFORMATION_SCHEMA.COLUMNS", statements[1])
        self.assertEqual(
            cursor.execute.call_args_list[1].args[1],
            ("rdt_telegram_outbox", "claim_token"),
        )
        conn.close.assert_called_once()
        cursor.close.assert_called_once()

    def test_legacy_ensure_helper_is_read_only_compatibility_alias(self):
        with patch.object(db_helpers, "verify_retailbot2_schema") as verify:
            db_helpers.ensure_retailbot2_lifecycle_table()
        verify.assert_called_once_with()

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
