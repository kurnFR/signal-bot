"""Cross-platform file locking: ml.file_lock helpers and retrainer locks.

POSIX-verifiable only. This Linux VM cannot run the Windows
``msvcrt.locking`` paths, so those are covered by code review, not by
these tests — no Windows coverage is faked here. What *is* verified
here runs identically on both platforms at the Python level:

- a blocking lock excludes a second open file description;
- ``unlock`` releases it;
- the ``exclusive_lock`` context manager locks and unlocks, even on
  exception;
- a file object opened in ``"a"`` mode is seeked to byte 0 before
  locking (on POSIX ``flock`` locks the whole file regardless of
  position, so the observable proxy for the seek is ``tell() == 0``
  after locking; on Windows that same seek is what makes
  ``msvcrt.locking`` cover byte 0 instead of the EOF position);
- the online retrainer's four locks (registry, cycle, audit, journal)
  serialise the same way through the shared helpers.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import file_lock, online_retrainer
from ml.file_lock import exclusive_lock, lock_exclusive, lock_exclusive_nonblocking, unlock


class TestFileLockHelpers(unittest.TestCase):
    def test_blocking_lock_excludes_second_open_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.lock")
            fd1 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            fd2 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                lock_exclusive(fd1)
                self.assertFalse(lock_exclusive_nonblocking(fd2))
                unlock(fd1)
                self.assertTrue(lock_exclusive_nonblocking(fd2))
                unlock(fd2)
            finally:
                os.close(fd1)
                os.close(fd2)

    def test_unlock_releases_for_another_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.lock")
            fd1 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            fd2 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                lock_exclusive(fd1)
                unlock(fd1)
                self.assertTrue(lock_exclusive_nonblocking(fd2))
                unlock(fd2)
            finally:
                os.close(fd1)
                os.close(fd2)

    def test_exclusive_lock_context_manager_locks_and_unlocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.lock")
            fd1 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            fd2 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                with exclusive_lock(fd1):
                    self.assertFalse(lock_exclusive_nonblocking(fd2))
                self.assertTrue(lock_exclusive_nonblocking(fd2))
                unlock(fd2)
            finally:
                os.close(fd1)
                os.close(fd2)

    def test_exclusive_lock_releases_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.lock")
            fd1 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            fd2 = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                with self.assertRaises(RuntimeError):
                    with exclusive_lock(fd1):
                        raise RuntimeError("boom")
                self.assertTrue(lock_exclusive_nonblocking(fd2))
                unlock(fd2)
            finally:
                os.close(fd1)
                os.close(fd2)

    def test_append_mode_file_object_locks_byte_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.lock"
            path.write_bytes(b"existing-content")
            with path.open("a", encoding="utf-8") as lock_file:
                # "a" mode positions at EOF; the seek hardening must move
                # the position to byte 0 before locking.
                self.assertEqual(lock_file.tell(), len(b"existing-content"))
                lock_exclusive(lock_file)
                self.assertEqual(lock_file.tell(), 0)
                with path.open("a", encoding="utf-8") as other:
                    self.assertFalse(lock_exclusive_nonblocking(other))
                unlock(lock_file)
            with path.open("a", encoding="utf-8") as other:
                self.assertTrue(lock_exclusive_nonblocking(other))
                unlock(other)


class TestRetrainerLocksUseSharedHelpers(unittest.TestCase):
    def test_online_retrainer_no_longer_imports_fcntl_directly(self):
        # feed2591 fixed this class of bug for model_store but missed
        # online_retrainer; the module must not carry an fcntl attribute.
        self.assertFalse(hasattr(online_retrainer, "fcntl"))

    def test_registry_lock_serialises_across_open_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "retraining_registry.lock"
            with patch.object(online_retrainer, "REGISTRY_LOCK_PATH", lock_path):
                with online_retrainer._registry_lock():
                    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                    try:
                        self.assertFalse(lock_exclusive_nonblocking(fd))
                    finally:
                        os.close(fd)
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    self.assertTrue(lock_exclusive_nonblocking(fd))
                    unlock(fd)
                finally:
                    os.close(fd)

    def test_cycle_lock_yields_true_then_false_while_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "retraining_cycle.lock"
            with patch.object(online_retrainer, "CYCLE_LOCK_PATH", lock_path):
                with online_retrainer._retraining_cycle_lock() as first:
                    self.assertTrue(first)
                    with online_retrainer._retraining_cycle_lock() as second:
                        self.assertFalse(second)
                    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                    try:
                        self.assertFalse(lock_exclusive_nonblocking(fd))
                    finally:
                        os.close(fd)
                with online_retrainer._retraining_cycle_lock() as third:
                    self.assertTrue(third)

    def test_audit_and_journal_locks_serialise(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "retraining_audit.lock"
            journal_path = Path(tmp) / "retraining_journal.lock"
            with patch.object(online_retrainer, "AUDIT_LOCK_PATH", audit_path):
                with online_retrainer._audit_log_lock():
                    fd = os.open(audit_path, os.O_CREAT | os.O_RDWR, 0o600)
                    try:
                        self.assertFalse(lock_exclusive_nonblocking(fd))
                    finally:
                        os.close(fd)
            with patch.object(online_retrainer, "JOURNAL_LOCK_PATH", journal_path):
                with online_retrainer._journal_lock():
                    fd = os.open(journal_path, os.O_CREAT | os.O_RDWR, 0o600)
                    try:
                        self.assertFalse(lock_exclusive_nonblocking(fd))
                    finally:
                        os.close(fd)


if __name__ == "__main__":
    unittest.main()
