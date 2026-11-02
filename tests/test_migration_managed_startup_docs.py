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


if __name__ == "__main__":
    unittest.main()
