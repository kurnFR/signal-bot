"""Real MariaDB concurrency tests for Wallet Tracker's atomic toggle.

These tests only run when WALLET_TRACKER_MARIADB_TESTS=1 is explicitly set.
CI points the app at a disposable wallet_tracker_test database; never point
this job at the CMS or any production database.
"""
import os
import threading
from datetime import timedelta
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



@unittest.skipUnless(ENABLED, "requires disposable MariaDB test database")
class TestMariaDBWalletAlertDigest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db.db as db_module

        db_module._pool = None
        cls.pool = store.get_pool()
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS tracked_wallets (
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
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS wallet_swaps (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    chain VARCHAR(20) NOT NULL,
                    wallet VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    pool_address VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    pair VARCHAR(80) NOT NULL,
                    side ENUM('buy','sell') NOT NULL,
                    usd_value DECIMAL(24,2) NOT NULL,
                    token_amount DECIMAL(40,12) NOT NULL,
                    price_usd DECIMAL(36,18) NOT NULL,
                    tx_hash VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    block_time DATETIME NOT NULL,
                    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_wswap (chain, tx_hash, pool_address, side, wallet),
                    INDEX idx_wswap_time (block_time),
                    INDEX idx_wswap_wallet_time (chain, wallet, block_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS wallet_tracker_alert_cursor (
                    id TINYINT PRIMARY KEY,
                    last_swap_id BIGINT NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS wallet_telegram_alerts (
                    swap_id BIGINT PRIMARY KEY,
                    delivery_status ENUM('PENDING','SENT','FAILED','CANCELLED') NOT NULL DEFAULT 'PENDING',
                    attempts INT NOT NULL DEFAULT 0,
                    next_attempt_at DATETIME NULL,
                    last_error VARCHAR(500) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    delivered_at DATETIME NULL,
                    INDEX idx_wallet_alert_due (delivery_status, next_attempt_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def setUp(self):
        super().setUp()
        import uuid

        self.wallet = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
        self.chain = "eth"
        self.saved_cursor = None
        self.swap_ids = []
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT last_swap_id FROM wallet_tracker_alert_cursor WHERE id = 1")
            row = cur.fetchone()
            self.saved_cursor = int(row[0]) if row else None
            cur.execute("SELECT COALESCE(MAX(id), 0) FROM wallet_swaps")
            start_id = int(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO wallet_tracker_alert_cursor (id, last_swap_id)
                   VALUES (1, %s)
                   ON DUPLICATE KEY UPDATE last_swap_id = VALUES(last_swap_id)""",
                (start_id,),
            )
            cur.execute(
                "INSERT INTO tracked_wallets (chain, wallet, source, status) VALUES (%s,%s,'manual','ACTIVE')",
                (self.chain, self.wallet),
            )
            now = store._utcnow() - timedelta(minutes=5)
            rows = []
            for index, (side, value) in enumerate((
                ("buy", 12000), ("buy", 25000), ("sell", 9000), ("buy", 14000)
            )):
                tx_hash = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
                rows.append((
                    self.chain, self.wallet, "0x" + uuid.uuid4().hex,
                    "ETH / USDC", side, value, value / 2000, 2000,
                    tx_hash, now + timedelta(seconds=index),
                ))
            cur.executemany(
                """INSERT INTO wallet_swaps
                   (chain,wallet,pool_address,pair,side,usd_value,token_amount,price_usd,tx_hash,block_time)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                rows,
            )
            cur.execute(
                "SELECT id FROM wallet_swaps WHERE chain=%s AND wallet=%s ORDER BY id",
                (self.chain, self.wallet),
            )
            self.swap_ids = [int(row[0]) for row in cur.fetchall()]
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def tearDown(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM wallet_telegram_alerts WHERE swap_id IN "
                "(SELECT id FROM wallet_swaps WHERE chain=%s AND wallet=%s)",
                (self.chain, self.wallet),
            )
            cur.execute("DELETE FROM wallet_swaps WHERE chain=%s AND wallet=%s", (self.chain, self.wallet))
            cur.execute("DELETE FROM tracked_wallets WHERE chain=%s AND wallet=%s", (self.chain, self.wallet))
            if self.saved_cursor is None:
                cur.execute("DELETE FROM wallet_tracker_alert_cursor WHERE id = 1")
            else:
                cur.execute(
                    "UPDATE wallet_tracker_alert_cursor SET last_swap_id=%s WHERE id=1",
                    (self.saved_cursor,),
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()
        super().tearDown()

    def test_burst_is_delivered_as_one_digest_and_later_swaps_wait_for_cooldown(self):
        from wallet_tracker.telegram import WalletTelegramNotifier

        queued = store.queue_new_wallet_alerts(10000, max_age_minutes=30)
        self.assertEqual(queued, 4)
        digest_rows = store.get_pending_wallet_alerts(cooldown_minutes=10)
        self.assertEqual(len(digest_rows), 1)
        digest = digest_rows[0]
        self.assertEqual(digest["swap_count"], 4)
        self.assertEqual(digest["usd_value"], 60000.0)
        self.assertEqual(digest["buy_usd_value"], 51000.0)
        self.assertEqual(digest["sell_usd_value"], 9000.0)
        message = WalletTelegramNotifier.format_message(digest)
        self.assertIn("<b>Swaps:</b> 4", message)
        self.assertIn("$60,000.00", message)

        self.assertTrue(store.mark_wallet_alert_attempt(digest, success=True))
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT delivery_status, COUNT(*) FROM wallet_telegram_alerts "
                "WHERE swap_id IN (%s,%s,%s,%s) GROUP BY delivery_status",
                tuple(self.swap_ids),
            )
            statuses = {row[0]: int(row[1]) for row in cur.fetchall()}
            self.assertEqual(statuses, {"SENT": 4})

            cur.execute(
                """INSERT INTO wallet_swaps
                   (chain,wallet,pool_address,pair,side,usd_value,token_amount,price_usd,tx_hash,block_time)
                   VALUES (%s,%s,%s,%s,'buy',%s,%s,%s,%s,%s)""",
                (
                    self.chain, self.wallet, "0x" + "d" * 40, "ETH / USDC",
                    50000, 25, 2000, "0x" + "e" * 64, store._utcnow(),
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.assertEqual(store.queue_new_wallet_alerts(10000, max_age_minutes=30), 1)
        self.assertEqual(store.get_pending_wallet_alerts(cooldown_minutes=10), [])

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """UPDATE wallet_telegram_alerts
                   SET delivered_at = UTC_TIMESTAMP() - INTERVAL 11 MINUTE
                   WHERE swap_id IN (%s,%s,%s,%s)""",
                tuple(self.swap_ids),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

        next_digest = store.get_pending_wallet_alerts(cooldown_minutes=10)
        self.assertEqual(len(next_digest), 1)
        self.assertEqual(next_digest[0]["swap_count"], 1)
        self.assertEqual(next_digest[0]["usd_value"], 50000.0)


if __name__ == "__main__":
    unittest.main()
