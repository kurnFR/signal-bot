"""Atomic JSON persistence must preserve the last valid state on write failure."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer


class TestOnlineRetrainerAtomicState(unittest.TestCase):
    def test_atomic_write_replaces_complete_json_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            target.write_text('{"old": true}', encoding="utf-8")

            online_retrainer._atomic_write_json(target, {"new": [1, 2, 3]})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": [1, 2, 3]})
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_failed_replace_keeps_previous_file_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            target.write_text('{"last_valid": true}', encoding="utf-8")

            with patch.object(online_retrainer.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    online_retrainer._atomic_write_json(target, {"partial": "new"})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"last_valid": True})
            self.assertEqual(list(Path(tmp).glob(".state.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
