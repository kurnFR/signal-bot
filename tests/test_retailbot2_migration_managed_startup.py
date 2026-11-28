"""Paper Trading archive schema is migration-managed, never mutated by runtime startup.

Migration 041 drops the retired RetailBot2 `rdt_*` tables: the store
checks below now target paper/outbox_store.OutboxStore, the only
remaining runtime owner of the Telegram outbox table (now
`telegram_outbox`), and the archive-schema check is
`db.db.verify_paper_archive_schema()` over `paper_trades`,
`paper_positions`, `paper_configs` and `telegram_outbox`. The
`rdt_strategy_lifecycle` / `bot_control` reads and the legacy ensure
helper are gone with the tables."""
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
        self.assertIn("041_remove_rdt_tables", versions)

    def test_web_lifespan_verifies_paper_archive_schema_and_never_runs_ddl_helper(self):
        source = inspect.getsource(lifespan)
        self.assertNotIn("DatabaseManager(", source)
        self.assertNotIn("ensure_retailbot2_lifecycle_table", source)
        self.assertNotIn("verify_retailbot2_schema", source)
        self.assertIn("verify_paper_archive_schema", source)

    def test_outbox_store_startup_does_not_call_runtime_table_creator(self):
        source = inspect.getsource(OutboxStore._init_pool)
        self.assertNotIn("_create_tables(", source)
        self.assertIn("_verify_schema(", source)

    def test_outbox_store_verification_uses_only_read_queries(self):
        # The store verifies only its own table (telegram_outbox +
        # claim_token column); the other paper tables are archive-only and
        # are verified by db.db.verify_paper_archive_schema().
        store = OutboxStore.__new__(OutboxStore)
        cursor = Mock()
        cursor.fetchone.return_value = {"table_name": "telegram_outbox"}
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
            ("telegram_outbox", "claim_token"),
        )
        conn.close.assert_called_once()
        cursor.close.assert_called_once()

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

    def test_migration_041_renames_outbox_and_drops_rdt_tables(self):
        migration = Path("db/migrate_remove_rdt_tables.sql").read_text(encoding="utf-8")
        self.assertIn("RENAME TABLE rdt_telegram_outbox TO telegram_outbox", migration)
        for table in ("rdt_trades", "rdt_strategy_stats", "rdt_strategy_lifecycle", "bot_control"):
            self.assertIn(table, migration)
        self.assertIn("DROP TABLE IF EXISTS rdt_trades, rdt_strategy_stats, rdt_strategy_lifecycle, bot_control", migration)
        # Fail-closed guard: a pre-existing rename target must abort the run.
        self.assertIn("telegram_outbox", migration)
        self.assertIn("CHECK (ok = 1)", migration)
        # Pre-migration backup prerequisite must be stated in the file.
        self.assertIn("mysqldump", migration)
        self.assertIn("rdt_archive_pre_removal.sql", migration)

    def test_verify_paper_archive_schema_requires_all_tables(self):
        pool = Mock()
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value = cursor
        pool.get_connection.return_value = conn

        with patch.object(db_helpers, "get_pool", return_value=pool):
            # 1. All four tables present: no error.
            cursor.fetchall.return_value = [
                {"table_name": "paper_trades"},
                {"table_name": "paper_positions"},
                {"table_name": "paper_configs"},
                {"table_name": "telegram_outbox"},
            ]
            db_helpers.verify_paper_archive_schema()

            # 2. Missing telegram_outbox fails closed.
            cursor.fetchall.return_value = [
                {"table_name": "paper_trades"},
                {"table_name": "paper_positions"},
                {"table_name": "paper_configs"},
            ]
            with self.assertRaises(RuntimeError) as ctx:
                db_helpers.verify_paper_archive_schema()
            self.assertIn("telegram_outbox", str(ctx.exception))
            self.assertIn("python -m db.migrate", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
