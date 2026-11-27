"""Research control singleton writes must persist even if the seed row is absent.

Step 3: the RetailBot2 control setter (set_retailbot2_control) is deleted
with the worker; only the live screener control remains."""
import unittest
from unittest.mock import Mock, patch

from db import db as db_helpers


class TestResearchControlPersistence(unittest.TestCase):
    def _assert_singleton_upsert(self, patch_target, setter, enabled, overrides):
        conn = Mock()
        cursor = Mock()
        conn.cursor.return_value = cursor
        with patch(patch_target) as pool:
            pool.return_value.get_connection.return_value = conn
            setter(enabled, overrides)

        self.assertEqual(cursor.execute.call_count, 1)
        statement = cursor.execute.call_args.args[0].upper()
        self.assertTrue(statement.lstrip().startswith("INSERT"))
        self.assertIn("ON DUPLICATE KEY UPDATE", statement)
        self.assertIn("ENABLED=VALUES(ENABLED)", statement)
        self.assertIn("OVERRIDES_JSON=VALUES(OVERRIDES_JSON)", statement)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_screener_control_uses_singleton_upsert(self):
        self._assert_singleton_upsert(
            "db.db.get_pool",
            db_helpers.set_screener_control,
            False,
            {"min_quality_score": 75},
        )


if __name__ == "__main__":
    unittest.main()
