"""Online retraining must reject duplicate cycles across workers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerCycleLock(unittest.TestCase):
    def test_second_cycle_is_skipped_while_first_holds_process_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "retraining_cycle.lock"
            with patch.object(online_retrainer, "CYCLE_LOCK_PATH", lock_path):
                with online_retrainer._retraining_cycle_lock() as acquired:
                    self.assertTrue(acquired)
                    with patch.object(online_retrainer, "_load_registry") as load_registry:
                        self.assertEqual(online_retrainer.execute_retraining_cycle(), [])
                    load_registry.assert_not_called()

    def test_lock_is_released_after_cycle_context_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "retraining_cycle.lock"
            with patch.object(online_retrainer, "CYCLE_LOCK_PATH", lock_path):
                with online_retrainer._retraining_cycle_lock() as first:
                    self.assertTrue(first)
                with online_retrainer._retraining_cycle_lock() as second:
                    self.assertTrue(second)


if __name__ == "__main__":
    unittest.main()
