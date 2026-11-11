import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_all


class TestApplyMigrations(unittest.TestCase):
    def test_runs_the_migration_module_and_succeeds(self):
        calls = []
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "", "SIGNAL_BOT_AUTO_MIGRATE": "1"}):
            ok = run_all.apply_migrations(runner=lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
        self.assertTrue(ok)
        self.assertEqual(calls[0][1:], ["-m", "db.migrate"])

    def test_failure_blocks_startup(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "", "SIGNAL_BOT_AUTO_MIGRATE": "1"}):
            self.assertFalse(run_all.apply_migrations(runner=lambda cmd: SimpleNamespace(returncode=1)))

    def test_skip_flag_does_not_run_anything(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "1", "SIGNAL_BOT_AUTO_MIGRATE": "1"}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertTrue(ok)

    def test_automatic_migrations_are_disabled_by_default(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "", "SIGNAL_BOT_AUTO_MIGRATE": ""}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertTrue(ok)

    def test_skip_zero_with_auto_migration_unset_keeps_migrations_disabled(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "0", "SIGNAL_BOT_AUTO_MIGRATE": ""}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertTrue(ok)

    def test_invalid_auto_migration_value_fails_closed(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "", "SIGNAL_BOT_AUTO_MIGRATE": "maybe"}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertFalse(ok)

    def test_invalid_skip_value_fails_closed(self):
        with patch.dict(os.environ, {"SKIP_DB_MIGRATE": "maybe", "SIGNAL_BOT_AUTO_MIGRATE": "1"}):
            ok = run_all.apply_migrations(runner=lambda cmd: self.fail("must not run"))
        self.assertFalse(ok)

    def test_migration_failure_terminates_tracked_embedded_database_child(self):
        class FakeMariaDB:
            pid = 12345

            def __init__(self):
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.killed = True

        child = FakeMariaDB()
        from argparse import Namespace
        with patch.object(run_all.argparse.ArgumentParser, "parse_args", return_value=Namespace(skip=[], only=[])), \\
             patch.object(run_all, "ensure_database_running"), \\
             patch.object(run_all, "apply_migrations", return_value=False), \\
             patch.object(run_all, "_children", {"mariadb": child}):
            run_all._shutdown.clear()
            try:
                with self.assertRaises(SystemExit) as raised:
                    run_all.main()
            finally:
                run_all._shutdown.clear()
        self.assertEqual(raised.exception.code, 1)
        self.assertTrue(child.terminated)
        self.assertFalse(child.killed)

    def test_migration_failure_triggers_supervisor_cleanup(self):
        from argparse import Namespace
        with patch.object(run_all.argparse.ArgumentParser, "parse_args", return_value=Namespace(skip=[], only=[])), \
             patch.object(run_all, "ensure_database_running") as ensure_db, \
             patch.object(run_all, "apply_migrations", return_value=False), \
             patch.object(run_all, "_shutdown_all") as shutdown:
            with self.assertRaises(SystemExit) as raised:
                run_all.main()
        self.assertEqual(raised.exception.code, 1)
        ensure_db.assert_called_once()
        shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
