"""Keep operator guidance aligned with migration-managed service schemas."""
from pathlib import Path
import unittest


class TestMigrationManagedStartupDocs(unittest.TestCase):
    def test_security_guide_separates_migration_privileges_from_runtime(self):
        text = Path("docs/SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("run_all.py", text)
        self.assertIn("read-only", text)
        self.assertNotIn("wallet tracker startup runs an ALTER TABLE", text)

    def test_wallet_tracker_guide_requires_migrations_before_standalone_worker(self):
        text = Path("docs/WALLET_TRACKER.md").read_text(encoding="utf-8")
        self.assertIn("versioned migrations", text)
        self.assertIn("standalone worker requires migrations", text)
        self.assertNotIn("Tables are created automatically at startup.", text)

    def test_wallet_runtime_schema_migration_documents_read_only_verification(self):
        text = Path("db/migrate_wallet_tracker_runtime_schema.sql").read_text(encoding="utf-8")
        self.assertIn("read-only", text)

    def test_retailbot2_outbox_runbook_documents_authoritative_advisory_lock(self):
        from paper.retailbot2 import OUTBOX_LOCK_NAME
        self.assertEqual(OUTBOX_LOCK_NAME, "rdt_outbox_worker_lock")

        release_gates = Path("docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md").read_text(encoding="utf-8")
        self.assertIn("OUTBOX_LOCK_NAME = \"rdt_outbox_worker_lock\"", release_gates)
        self.assertIn("SELECT IS_USED_LOCK('rdt_outbox_worker_lock');", release_gates)
        self.assertIn("WHERE ID = IS_USED_LOCK('rdt_outbox_worker_lock');", release_gates)

        arch_doc = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("`rdt_outbox_worker_lock`", arch_doc)

        # Confirm no lock query in docs mistakenly refers to retailbot2_telegram_outbox
        for doc_path in Path("docs").glob("*.md"):
            doc_text = doc_path.read_text(encoding="utf-8")
            self.assertNotIn("IS_USED_LOCK('retailbot2_telegram_outbox')", doc_text)
            self.assertNotIn("GET_LOCK('retailbot2_telegram_outbox'", doc_text)



if __name__ == "__main__":
    unittest.main()
