"""Real MariaDB concurrency tests for Wallet Tracker's atomic toggle.

These tests only run when WALLET_TRACKER_MARIADB_TESTS=1 is explicitly set.
CI points the app at a disposable wallet_tracker_test database; never point
this job at the CMS or any production database.
"""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from wallet_tracker import store

ENABLED = os.getenv("WALLET_TRACKER_MARIADB_TESTS") == "1"
W1 = "0x" + "a" * 40
W2 = "0x" + "b" * 40


@unittest.skipUnless(ENABLED, "requires disposable MariaDB test database")
class TestMariaDBAtomicWalletToggle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The CI job supplies MYSQL_* before Python starts. Reset the shared
        # singleton so this test uses that isolated database configuration.
        import db.db as db_module

        db_module._pool = None
        cls.pool = store.get_pool()
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_wallets (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    chain VARCHAR(20) NOT NULL,
                    wallet VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    label VARCHAR(80) NULL,
                    source ENUM('auto','manual') NOT NULL DEFAULT 'auto',
                    status ENUM('ACTIVE','IGNORED','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
                    last_net_usd DECIMAL(24,2) NULL,
                    first_flagged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_tracked (chain, wallet)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def setUp(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM tracked_wallets")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _insert_wallet(self, wallet, status="ACTIVE"):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tracked_wallets (chain, wallet, source, status) VALUES (%s,%s,'manual',%s)",
                ("eth", wallet, status),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _status(self, wallet):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM tracked_wallets WHERE chain=%s AND wallet=%s",
                ("eth", wallet),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            conn.close()

    def _concurrent_toggles(self, wallet, label=None):
        barrier = threading.Barrier(2)

        def toggle():
            barrier.wait(timeout=10)
            return store.toggle_wallet_tracking("eth", wallet, label)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(toggle), executor.submit(toggle)]
            return [future.result(timeout=20) for future in futures]

    def test_two_concurrent_toggles_of_existing_active_wallet_are_serialized(self):
        self._insert_wallet(W1, "ACTIVE")

        results = self._concurrent_toggles(W1, "concurrency-test")

        self.assertCountEqual(results, [False, True])
        # Two serialized toggles return the row to its starting state.
        self.assertEqual(self._status(W1), "ACTIVE")
        self.assertEqual(self._status(W2), None)

    def test_two_concurrent_adds_of_missing_wallet_apply_two_toggles(self):
        results = self._concurrent_toggles(W1, "new-wallet-test")

        # One INSERT IGNORE creates the row; the second request observes that
        # committed row and performs the second toggle.
        self.assertCountEqual(results, [False, True])
        self.assertEqual(self._status(W1), "IGNORED")
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM tracked_wallets WHERE chain=%s AND wallet=%s",
                ("eth", W1),
            )
            self.assertEqual(cur.fetchone()[0], 1)
            cur.close()
        finally:
            conn.close()

    def test_update_failure_rolls_back_and_does_not_change_other_wallets(self):
        self._insert_wallet(W1, "ACTIVE")
        self._insert_wallet(W2, "INACTIVE")
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DROP TRIGGER IF EXISTS wallet_tracker_test_fail_update")
            cur.execute(
                """
                CREATE TRIGGER wallet_tracker_test_fail_update
                BEFORE UPDATE ON tracked_wallets
                FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'forced integration-test failure'
                """
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

        try:
            with self.assertRaises(Exception):
                store.toggle_wallet_tracking("eth", W1)
        finally:
            conn = self.pool.get_connection()
            try:
                cur = conn.cursor()
                cur.execute("DROP TRIGGER IF EXISTS wallet_tracker_test_fail_update")
                conn.commit()
                cur.close()
            finally:
                conn.close()

        self.assertEqual(self._status(W1), "ACTIVE")
        self.assertEqual(self._status(W2), "INACTIVE")


if __name__ == "__main__":
    unittest.main()
