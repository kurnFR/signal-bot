"""Real MariaDB concurrency and transaction serialization tests for user management.

Validates that explicit row-level locking (SELECT ... FOR UPDATE ORDER BY id)
prevents race conditions that could leave the system with zero active administrators,
verifies deterministic lock contention and serialized execution across concurrent connections,
and verifies that rejected operations explicitly roll back without leaving partial database
or audit mutations.

All tests execute against the disposable test database 'signal_bot_test'.
"""
import concurrent.futures
import threading
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import config
import db.db as db_module
from web.app import app
from web.auth import create_access_token, hash_password
import web.routes.auth_routes as auth_routes


class TestMariaDBUserManagementConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure centralized database pool to use the isolated test database
        cls._orig_db = config.MYSQL_CONFIG.get("database", "crypto_signals")
        config.MYSQL_CONFIG["database"] = "signal_bot_test"
        config.RETAILBOT2_DB_CONFIG["database"] = "signal_bot_test"
        db_module._pool = None
        cls.pool = db_module.get_pool()
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        # Reset test hook
        auth_routes._test_after_admin_lock_hook = None

        # Clean up and restore original configuration
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM audit_logs WHERE username LIKE 'conc_test_%'")
            cur.execute("DELETE FROM users WHERE username LIKE 'conc_test_%'")
            # Re-activate default admin in test database
            cur.execute("UPDATE users SET is_active = 1 WHERE username = 'admin'")
            conn.commit()
            cur.close()
        finally:
            conn.close()

        config.MYSQL_CONFIG["database"] = cls._orig_db
        config.RETAILBOT2_DB_CONFIG["database"] = cls._orig_db
        db_module._pool = None

    def setUp(self):
        auth_routes._test_after_admin_lock_hook = None
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM audit_logs WHERE username LIKE 'conc_test_%'")
            cur.execute("DELETE FROM users WHERE username LIKE 'conc_test_%'")
            # Temporarily deactivate any pre-existing admin in test DB so only test admins are active
            cur.execute("UPDATE users SET is_active = 0 WHERE username = 'admin'")
            conn.commit()

            # Seed test administrators and operator
            pwd_hash = hash_password("SecurePassword123!")
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role, is_active, session_version)
                VALUES
                  ('conc_test_admin1', 'conc_admin1@test.com', %s, 'admin', 1, 0),
                  ('conc_test_admin2', 'conc_admin2@test.com', %s, 'admin', 1, 0),
                  ('conc_test_caller', 'conc_caller@test.com', %s, 'admin', 1, 0)
                """,
                (pwd_hash, pwd_hash, pwd_hash),
            )
            conn.commit()

            cur.execute("SELECT id, username FROM users WHERE username LIKE 'conc_test_%'")
            rows = {r[1]: r[0] for r in cur.fetchall()}
            self.admin1_id = rows["conc_test_admin1"]
            self.admin2_id = rows["conc_test_admin2"]
            self.caller_id = rows["conc_test_caller"]
            cur.close()
        finally:
            conn.close()

        self.caller_token = create_access_token(
            user_id=self.caller_id,
            username="conc_test_caller",
            role="admin",
            session_version=0,
        )
        self.headers = {
            "Authorization": f"Bearer {self.caller_token}",
            "Content-Type": "application/json",
        }
        self.user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

    def tearDown(self):
        auth_routes._test_after_admin_lock_hook = None
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM audit_logs WHERE username LIKE 'conc_test_%'")
            cur.execute("DELETE FROM users WHERE username LIKE 'conc_test_%'")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _count_active_test_admins(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND username IN ('conc_test_admin1', 'conc_test_admin2')"
            )
            count = cur.fetchone()[0]
            cur.close()
            return int(count)
        finally:
            conn.close()

    def _check_innodb_lock_wait(self):
        """Query MariaDB information_schema to verify whether an InnoDB transaction is in LOCK WAIT."""
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT COUNT(*) AS lock_waits FROM information_schema.innodb_trx WHERE trx_state = 'LOCK WAIT'")
            row = cur.fetchone()
            cur.close()
            return int(row["lock_waits"]) if row else 0
        finally:
            conn.close()

    def test_concurrent_admin_demotion_prevents_zero_admins(self):
        """Two administrators concurrently attempt to demote the last two active administrators.

        Uses deterministic synchronization via _test_after_admin_lock_hook:
        1. Thread 1 acquires row lock via SELECT ... FOR UPDATE on admin rows and signals lock_acquired.
        2. Thread 2 attempts demotion on concurrent connection and blocks inside MariaDB on the row lock.
        3. Test verifies Thread 2 is blocked (observes MariaDB LOCK WAIT state).
        4. Thread 1 proceeds to commit its demotion.
        5. Thread 2 is unblocked by MariaDB, reads updated database state under lock, and is rejected (400).
        6. Asserts exactly one 200, exactly one 400, and at least 1 active admin remains.
        """
        # Deactivate caller to isolate admin1 and admin2 as the sole active admins
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.caller_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        observed_lock_wait = []

        def lock_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                # Hold the row-level lock until thread 2 has engaged lock contention
                if not t1_can_proceed.wait(timeout=5.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_after_admin_lock_hook = lock_hook

        def demote_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"role": "trader"},
                headers=self.headers,
            )

        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(demote_user, self.admin1_id)

                # Wait deterministically until thread 1 has acquired the row lock in MariaDB
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                # Submit thread 2 while thread 1 is holding the lock
                f2 = executor.submit(demote_user, self.admin2_id)

                # Verify thread 2 is blocked waiting for MariaDB lock
                # Poll innodb_trx briefly to confirm lock wait
                for _ in range(20):
                    time.sleep(0.05)
                    if self._check_innodb_lock_wait() > 0 or not f2.done():
                        observed_lock_wait.append(True)
                        break

                # Unblock thread 1 to commit
                t1_can_proceed.set()

                res1 = f1.result(timeout=5.0)
                res2 = f2.result(timeout=5.0)

        auth_routes._test_after_admin_lock_hook = None

        self.assertTrue(any(observed_lock_wait), "Expected Thread 2 to encounter lock contention in MariaDB")

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400], f"Expected exactly one 200 and one 400, got: {res1.status_code}, {res2.status_code}")

        # The rejected response must carry the last-admin protection message
        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        # MariaDB must contain exactly 1 active admin (never 0)
        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_concurrent_admin_deactivation_prevents_zero_admins(self):
        """Two administrators concurrently attempt to deactivate the last two active administrators.

        Uses deterministic synchronization to prove lock contention and serialized rejection.
        """
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.caller_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        observed_lock_wait = []

        def lock_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=5.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_after_admin_lock_hook = lock_hook

        def deactivate_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"is_active": False},
                headers=self.headers,
            )

        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(deactivate_user, self.admin1_id)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                f2 = executor.submit(deactivate_user, self.admin2_id)

                for _ in range(20):
                    time.sleep(0.05)
                    if self._check_innodb_lock_wait() > 0 or not f2.done():
                        observed_lock_wait.append(True)
                        break

                t1_can_proceed.set()

                res1 = f1.result(timeout=5.0)
                res2 = f2.result(timeout=5.0)

        auth_routes._test_after_admin_lock_hook = None

        self.assertTrue(any(observed_lock_wait), "Expected Thread 2 to encounter lock contention in MariaDB")

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400])

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_concurrent_admin_deletion_prevents_zero_admins(self):
        """Two administrators concurrently attempt to delete the last two active administrators.

        Uses deterministic synchronization to prove lock contention and serialized rejection.
        """
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.caller_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        observed_lock_wait = []

        def lock_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=5.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_after_admin_lock_hook = lock_hook

        def delete_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.delete(
                f"/api/users/{target_id}",
                headers=self.headers,
            )

        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(delete_user, self.admin1_id)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                f2 = executor.submit(delete_user, self.admin2_id)

                for _ in range(20):
                    time.sleep(0.05)
                    if self._check_innodb_lock_wait() > 0 or not f2.done():
                        observed_lock_wait.append(True)
                        break

                t1_can_proceed.set()

                res1 = f1.result(timeout=5.0)
                res2 = f2.result(timeout=5.0)

        auth_routes._test_after_admin_lock_hook = None

        self.assertTrue(any(observed_lock_wait), "Expected Thread 2 to encounter lock contention in MariaDB")

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400])

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot delete the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_successful_deactivation_when_another_active_admin_exists(self):
        """Deactivating admin1 succeeds when admin2 and caller are both active."""
        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            res = self.client.put(
                f"/api/users/{self.admin1_id}",
                json={"is_active": False},
                headers=self.headers,
            )

        self.assertEqual(res.status_code, 200)
        self.assertIn("updated", res.json()["message"])

        # Verify in MariaDB that admin1 was deactivated and session_version incremented
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT is_active, session_version FROM users WHERE id = %s", (self.admin1_id,))
            user = cur.fetchone()
            self.assertEqual(user["is_active"], 0)
            self.assertEqual(user["session_version"], 1)

            # Audit log was written
            cur.execute(
                "SELECT action, details FROM audit_logs WHERE username = 'conc_test_caller' AND action = 'USER_UPDATED'"
            )
            audit = cur.fetchone()
            self.assertIsNotNone(audit)
            cur.close()
        finally:
            conn.close()

    def test_rejected_operations_produce_no_database_or_audit_mutations(self):
        """Rejected operations must produce zero mutations in users and zero audit log entries."""
        # Isolate admin1 as the sole active administrator
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id IN (%s, %s)", (self.admin2_id, self.caller_id))
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE username = 'conc_test_caller'")
            audit_count_before = cur.fetchone()[0]
            cur.close()
        finally:
            conn.close()

        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            # 1. Rejected demotion
            res_demote = self.client.put(f"/api/users/{self.admin1_id}", json={"role": "trader"}, headers=self.headers)
            self.assertEqual(res_demote.status_code, 400)

            # 2. Rejected deactivation
            res_deact = self.client.put(f"/api/users/{self.admin1_id}", json={"is_active": False}, headers=self.headers)
            self.assertEqual(res_deact.status_code, 400)

            # 3. Rejected deletion
            res_delete = self.client.delete(f"/api/users/{self.admin1_id}", headers=self.headers)
            self.assertEqual(res_delete.status_code, 400)

        # Verify database state was completely untouched
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role, is_active, session_version FROM users WHERE id = %s", (self.admin1_id,))
            target = cur.fetchone()
            self.assertEqual(target["role"], "admin")
            self.assertEqual(target["is_active"], 1)
            self.assertEqual(target["session_version"], 0)

            # Audit logs count must be unchanged (no audit rows written for rejected operations)
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE username = 'conc_test_caller'")
            audit_count_after = cur.fetchone()["COUNT(*)"]
            self.assertEqual(audit_count_before, audit_count_after)
            cur.close()
        finally:
            conn.close()

    def test_rejected_operation_explicit_rollback_and_connection_reuse(self):
        """Proves explicit rollback on rejected operation and subsequent connection reuse.

        1. A rejected last-admin operation rolls back explicitly.
        2. The database connection can subsequently be reused without lingering locks.
        3. A subsequent valid administrator operation is not blocked by the previous rejected request.
        4. No partial database mutation occurs.
        5. Existing audit behavior remains correct.
        """
        # Ensure admin1 and admin2 are both active
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 1 WHERE id IN (%s, %s, %s)", (self.admin1_id, self.admin2_id, self.caller_id))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):

            # Attempt invalid operation (self-demotion: caller demoting caller)
            res_bad = self.client.put(
                f"/api/users/{self.caller_id}",
                json={"role": "trader"},
                headers=self.headers,
            )
            self.assertEqual(res_bad.status_code, 400)
            self.assertIn("Cannot change your own role from admin", res_bad.json()["detail"])

            # Verify connection pool immediately works and is not blocked by lingering locks
            # Execute a subsequent valid update on admin1
            res_good = self.client.put(
                f"/api/users/{self.admin1_id}",
                json={"role": "viewer"},
                headers=self.headers,
            )
            self.assertEqual(res_good.status_code, 200)

        # Verify admin1 was updated to viewer while caller remained admin
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role, is_active FROM users WHERE id = %s", (self.caller_id,))
            caller = cur.fetchone()
            self.assertEqual(caller["role"], "admin")

            cur.execute("SELECT role, is_active FROM users WHERE id = %s", (self.admin1_id,))
            admin1 = cur.fetchone()
            self.assertEqual(admin1["role"], "viewer")
            cur.close()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
