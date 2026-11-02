import unittest
from unittest.mock import MagicMock, patch

from wallet_tracker import store


class TestWalletTrackerSchemaBootstrap(unittest.TestCase):
    def _pool(self, tables, columns):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [tables, columns]
        pool = MagicMock()
        pool.get_connection.return_value = connection
        return pool, connection, cursor

    def test_schema_check_is_read_only(self):
        tables = [{"table_name": name} for name in store._REQUIRED_TABLES]
        columns = [
            {
                "table_name": "tracked_wallets",
                "column_name": "status",
                "column_type": "enum('ACTIVE','IGNORED','INACTIVE')",
            },
            {
                "table_name": "wallet_tracker_settings",
                "column_name": "birdeye_api_key",
                "column_type": "varchar(255)",
            },
        ]
        pool, connection, cursor = self._pool(tables, columns)

        with patch("wallet_tracker.store.get_pool", return_value=pool):
            store.ensure_tables()

        self.assertEqual(cursor.execute.call_count, 2)
        for call in cursor.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().upper().startswith("SELECT"))
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()

    def test_missing_tables_fail_with_migration_guidance_without_writes(self):
        pool, connection, cursor = self._pool([], [])

        with patch("wallet_tracker.store.get_pool", return_value=pool):
            with self.assertRaisesRegex(RuntimeError, "python -m db.migrate"):
                store.ensure_tables()

        self.assertEqual(cursor.execute.call_count, 1)
        self.assertTrue(cursor.execute.call_args.args[0].lstrip().upper().startswith("SELECT"))
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()

    def test_outdated_columns_fail_with_migration_guidance_without_writes(self):
        tables = [{"table_name": name} for name in store._REQUIRED_TABLES]
        columns = [
            {
                "table_name": "tracked_wallets",
                "column_name": "status",
                "column_type": "enum('ACTIVE','IGNORED')",
            }
        ]
        pool, connection, cursor = self._pool(tables, columns)

        with patch("wallet_tracker.store.get_pool", return_value=pool):
            with self.assertRaisesRegex(RuntimeError, "birdeye_api_key"):
                store.ensure_tables()

        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
