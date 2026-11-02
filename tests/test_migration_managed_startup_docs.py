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

    def test_wallet_migration_no_longer_claims_runtime_ddl(self):
        text = Path("db/migrate_wallet_tracking.sql").read_text(encoding="utf-8")
        self.assertIn("read-only schema verification", text)
        self.assertNotIn("same DDL is also", text)


if __name__ == "__main__":
    unittest.main()
