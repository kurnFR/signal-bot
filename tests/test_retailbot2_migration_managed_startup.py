"""RetailBot2 schema is migration-managed, never mutated by runtime startup."""
import inspect
import unittest

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

    def test_legacy_runtime_table_creator_is_not_used_by_database_manager(self):
        source = inspect.getsource(DatabaseManager)
        self.assertIn("def _verify_schema(", source)
        self.assertNotIn("self._create_tables()", source)


if __name__ == "__main__":
    unittest.main()
