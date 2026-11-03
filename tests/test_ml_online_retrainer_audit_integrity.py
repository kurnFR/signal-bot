"""Retraining audit history must survive concurrent writers and invalid input."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerAuditIntegrity(unittest.TestCase):
    def test_append_preserves_existing_entries_and_prepends_new_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            lock_path = Path(tmp) / "audit.lock"
            existing = [{"key": "old", "status": "retrained"}]
            audit_path.write_text(json.dumps(existing), encoding="utf-8")

            with patch.object(online_retrainer, "AUDIT_LOG_PATH", audit_path), patch.object(
                online_retrainer, "AUDIT_LOCK_PATH", lock_path
            ):
                online_retrainer._append_audit_log({"key": "new", "status": "retrained"})

            self.assertEqual(
                json.loads(audit_path.read_text(encoding="utf-8")),
                [{"key": "new", "status": "retrained"}, *existing],
            )

    def test_corrupt_audit_log_is_preserved_instead_of_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            lock_path = Path(tmp) / "audit.lock"
            corrupt = b'{"broken":'
            audit_path.write_bytes(corrupt)

            with patch.object(online_retrainer, "AUDIT_LOG_PATH", audit_path), patch.object(
                online_retrainer, "AUDIT_LOCK_PATH", lock_path
            ):
                online_retrainer._append_audit_log({"key": "new", "status": "retrained"})

            self.assertEqual(audit_path.read_bytes(), corrupt)

    def test_wrong_audit_structure_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            lock_path = Path(tmp) / "audit.lock"
            invalid = json.dumps({"not": "a list"})
            audit_path.write_text(invalid, encoding="utf-8")

            with patch.object(online_retrainer, "AUDIT_LOG_PATH", audit_path), patch.object(
                online_retrainer, "AUDIT_LOCK_PATH", lock_path
            ):
                online_retrainer._append_audit_log({"key": "new", "status": "retrained"})

            self.assertEqual(audit_path.read_text(encoding="utf-8"), invalid)

    def test_invalid_utf8_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            registry_path.write_bytes(b"\xff\xfe")

            with patch.object(online_retrainer, "REGISTRY_PATH", registry_path):
                registry = online_retrainer._load_registry()

            self.assertIs(registry["enabled"], False)
            self.assertEqual(registry["registered_models"], {})

    def test_audit_lock_is_released_after_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            lock_path = Path(tmp) / "audit.lock"
            with patch.object(online_retrainer, "AUDIT_LOG_PATH", audit_path), patch.object(
                online_retrainer, "AUDIT_LOCK_PATH", lock_path
            ):
                with online_retrainer._audit_log_lock():
                    pass
                online_retrainer._append_audit_log({"key": "ok"})

            self.assertEqual(json.loads(audit_path.read_text(encoding="utf-8"))[0]["key"], "ok")


if __name__ == "__main__":
    unittest.main()
