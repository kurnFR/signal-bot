import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_all


class TestApplyMigrations(unittest.TestCase):
    def test_runs_the_migration_module_and_succeeds(self):
        calls = []
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": ""}):
            ok = run_all.apply_migrations(runner=lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
        self.assertTrue(ok)
        self.assertEqual(calls[0][1:], ["-m", "db.migrate"])

    def test_failure_blocks_startup(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": ""}):
            self.assertFalse(run_all.apply_migrations(runner=lambda cmd: SimpleNamespace(returncode=1)))

    def test_skip_flag_does_not_run_anything(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "1"}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
