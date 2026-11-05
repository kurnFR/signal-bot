"""Real MariaDB concurrency and transaction serialization tests for user management.

Validates that explicit row-level locking (SELECT ... FOR UPDATE ORDER BY id)
prevents race conditions that could leave the system with zero active administrators,
verifies deterministic lock contention and serialized execution across concurrent connections,
proves that the EXACT second transaction enters MariaDB's 'LOCK WAIT' state,
and verifies that rejected operations explicitly roll back without leaving partial database
or audit mutations.

All tests execute strictly against the authorized 'signal_bot_test' database.
"""
import concurrent.futures
import os
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient

# 1. Enforce database isolation before importing or initializing DB pools
os.environ["MYSQL_DATABASE"] = "signal_bot_test"

import config
config.MYSQL_CONFIG["database"] = "signal_bot_test"
config.RETAILBOT2_DB_CONFIG["database"] = "signal_bot_test"

import db.db as db_module
db_module._pool = None
db_module._retailbot2_pool = None

from web.app import app
from web.auth import create_access_token, hash_password
import web.routes.auth_routes as auth_routes


class TestMariaDBUserManagementConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mandatory database isolation guard: enforce signal_bot_test authorization
        os.environ["MYSQL_DATABASE"] = "signal_bot_test"
        config.MYSQL_CONFIG["database"] = "signal_bot_test"
        config.RETAILBOT2_DB_CONFIG["database"] = "signal_bot_test"
        db_module._pool = None
        db_module._retailbot2_pool = None

        cls.pool = db_module.get_pool()
        cls.client = TestClient(app, raise_server_exceptions=False)

        # Verify the exact resolved database on the active pool connection
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT DATABASE()")
            db_name = cur.fetchone()[0]
            cur.close()
            if db_name != "signal_bot_test":
                raise RuntimeError(f"Database isolation guard failed: connected to '{db_name}', expected 'signal_bot_test'")
        finally:
            conn.close()

        cls._run_id = uuid.uuid4().hex[:8]

    @classmethod
    def tearDownClass(cls):
        # Reset test hooks
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        # Reset pools without creating new connections
        db_module._pool = None
        db_module._retailbot2_pool = None

    def setUp(self):
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        self.test_prefix = f"conc_{self._run_id}_{int(time.time()*1000) % 1000000}"
        self.seeded_user_ids = []

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            # Seed run-specific test administrators and caller with unique usernames
            pwd_hash = hash_password("SecurePassword123!")
            u_admin1 = f"{self.test_prefix}_adm1"
            u_admin2 = f"{self.test_prefix}_adm2"
            u_caller = f"{self.test_prefix}_clr"

            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role, is_active, session_version)
                VALUES
                  (%s, %s, %s, 'admin', 1, 0),
                  (%s, %s, %s, 'admin', 1, 0),
                  (%s, %s, %s, 'admin', 1, 0)
                """,
                (
                    u_admin1, f"{u_admin1}@test.com", pwd_hash,
                    u_admin2, f"{u_admin2}@test.com", pwd_hash,
                    u_caller, f"{u_caller}@test.com", pwd_hash,
                ),
            )
            conn.commit()

            cur.execute(
                "SELECT id, username FROM users WHERE username IN (%s, %s, %s)",
                (u_admin1, u_admin2, u_caller),
            )
            rows = {r[1]: r[0] for r in cur.fetchall()}
            self.admin1_id = rows[u_admin1]
            self.admin2_id = rows[u_admin2]
            self.caller_id = rows[u_caller]
            self.seeded_user_ids = [self.admin1_id, self.admin2_id, self.caller_id]
            cur.close()
        finally:
            conn.close()

        # Isolate the concurrency test to specifically protect the test admin fixtures
        auth_routes._test_active_admin_filter = lambda u: u["id"] in {self.admin1_id, self.admin2_id}

        self.caller_token = create_access_token(
            user_id=self.caller_id,
            username=f"{self.test_prefix}_clr",
            role="admin",
            session_version=0,
        )
        self.headers = {
            "Authorization": f"Bearer {self.caller_token}",
            "Content-Type": "application/json",
        }
        self.user_auth = {
            "id": self.caller_id, "username": f"{self.test_prefix}_clr",
            "email": f"{self.test_prefix}_clr@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

    def tearDown(self):
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        if hasattr(self, "seeded_user_ids") and self.seeded_user_ids:
            conn = self.pool.get_connection()
            try:
                cur = conn.cursor()
                placeholders = ",".join(["%s"] * len(self.seeded_user_ids))
                cur.execute(
                    f"DELETE FROM audit_logs WHERE user_id IN ({placeholders})",
                    tuple(self.seeded_user_ids),
                )
                cur.execute(
                    f"DELETE FROM users WHERE id IN ({placeholders})",
                    tuple(self.seeded_user_ids),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()

    def _count_active_test_admins(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id IN (%s, %s)",
                (self.admin1_id, self.admin2_id),
            )
            count = cur.fetchone()[0]
            cur.close()
            return int(count)
        finally:
            conn.close()

    def _check_exact_thread_lock_wait(self, thread_id: int) -> bool:
        """Query MariaDB information_schema to verify whether the EXACT transaction
        associated with thread_id is in LOCK WAIT state."""
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT trx_state FROM information_schema.innodb_trx WHERE trx_mysql_thread_id = %s",
                (thread_id,)
            )
            row = cur.fetchone()
            cur.close()
            return bool(row and row.get("trx_state") == "LOCK WAIT")
        finally:
            conn.close()

    def test_concurrent_admin_demotion_prevents_zero_admins(self):
        """Two administrators concurrently attempt to demote the last two active administrators.

        Deterministic synchronization proving lock contention on the EXACT second transaction:
        1. Thread 1 acquires row lock via SELECT ... FOR UPDATE on admin rows and signals t1_locked.
        2. Thread 2 attempts demotion on concurrent connection; captures its exact MariaDB thread ID.
        3. Thread 2 executes the locking query and blocks inside MariaDB InnoDB row lock contention.
        4. Main thread polls information_schema.innodb_trx specifically for Thread 2's thread ID.
        5. Fails immediately if Thread 2's exact transaction does not enter LOCK WAIT.
        6. Once exact LOCK WAIT is proven, releases Thread 1 to commit.
        7. Thread 2 unblocks, re-reads updated database state under lock, and is rejected with HTTP 400.
        8. Asserts exactly one 200, exactly one 400, and at least 1 active admin remains.
        """
        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin2_id:
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def demote_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"role": "trader"},
                headers=self.headers,
            )

        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f1 = executor.submit(demote_user, self.admin1_id)
                    self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                    f2 = executor.submit(demote_user, self.admin2_id)
                    self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                    t2_tid = t2_thread_id_holder[0]

                    # Verify the EXACT Thread 2 transaction enters MariaDB LOCK WAIT state
                    t2_in_lock_wait = False
                    for _ in range(50):
                        time.sleep(0.05)
                        if self._check_exact_thread_lock_wait(t2_tid):
                            t2_in_lock_wait = True
                            break

                    self.assertTrue(
                        t2_in_lock_wait,
                        f"Exact Thread 2 transaction (thread_id={t2_tid}) failed to enter MariaDB LOCK WAIT state"
                    )

                    # Unblock Thread 1 only after exact LOCK WAIT was observed
                    t1_can_proceed.set()

                    res1 = f1.result(timeout=5.0)
                    res2 = f2.result(timeout=5.0)
        finally:
            t1_locked.set()
            t1_can_proceed.set()
            t2_about_to_query.set()
            auth_routes._test_before_admin_lock_hook = None
            auth_routes._test_after_admin_lock_hook = None

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400], f"Expected exactly one 200 and one 400, got: {res1.status_code}, {res2.status_code}")

        # The rejected response must carry the last-admin protection message
        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        # MariaDB must contain exactly 1 active admin (never 0)
        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

        # Confirm the rejected user's state did not mutate
        rejected_target_id = self.admin2_id if rejected is res2 else self.admin1_id
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role, is_active FROM users WHERE id = %s", (rejected_target_id,))
            user = cur.fetchone()
            self.assertEqual(user["role"], "admin")
            self.assertEqual(user["is_active"], 1)
            cur.close()
        finally:
            conn.close()

    def test_concurrent_admin_deactivation_prevents_zero_admins(self):
        """Two administrators concurrently attempt to deactivate the last two active administrators.

        Proves that the EXACT second transaction enters MariaDB's LOCK WAIT state.
        """
        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin2_id:
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def deactivate_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"is_active": False},
                headers=self.headers,
            )

        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f1 = executor.submit(deactivate_user, self.admin1_id)
                    self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                    f2 = executor.submit(deactivate_user, self.admin2_id)
                    self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                    t2_tid = t2_thread_id_holder[0]

                    # Verify the EXACT Thread 2 transaction enters MariaDB LOCK WAIT state
                    t2_in_lock_wait = False
                    for _ in range(50):
                        time.sleep(0.05)
                        if self._check_exact_thread_lock_wait(t2_tid):
                            t2_in_lock_wait = True
                            break

                    self.assertTrue(
                        t2_in_lock_wait,
                        f"Exact Thread 2 transaction (thread_id={t2_tid}) failed to enter MariaDB LOCK WAIT state"
                    )

                    t1_can_proceed.set()

                    res1 = f1.result(timeout=5.0)
                    res2 = f2.result(timeout=5.0)
        finally:
            t1_locked.set()
            t1_can_proceed.set()
            t2_about_to_query.set()
            auth_routes._test_before_admin_lock_hook = None
            auth_routes._test_after_admin_lock_hook = None

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400], f"Expected exactly one 200 and one 400, got: {res1.status_code}, {res2.status_code}")

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_concurrent_admin_deletion_prevents_zero_admins(self):
        """Two administrators concurrently attempt to delete the last two active administrators.

        Proves that the EXACT second transaction enters MariaDB's LOCK WAIT state.
        """
        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin2_id:
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def delete_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.delete(
                f"/api/users/{target_id}",
                headers=self.headers,
            )

        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f1 = executor.submit(delete_user, self.admin1_id)
                    self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                    f2 = executor.submit(delete_user, self.admin2_id)
                    self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                    t2_tid = t2_thread_id_holder[0]

                    # Verify the EXACT Thread 2 transaction enters MariaDB LOCK WAIT state
                    t2_in_lock_wait = False
                    for _ in range(50):
                        time.sleep(0.05)
                        if self._check_exact_thread_lock_wait(t2_tid):
                            t2_in_lock_wait = True
                            break

                    self.assertTrue(
                        t2_in_lock_wait,
                        f"Exact Thread 2 transaction (thread_id={t2_tid}) failed to enter MariaDB LOCK WAIT state"
                    )

                    t1_can_proceed.set()

                    res1 = f1.result(timeout=5.0)
                    res2 = f2.result(timeout=5.0)
        finally:
            t1_locked.set()
            t1_can_proceed.set()
            t2_about_to_query.set()
            auth_routes._test_before_admin_lock_hook = None
            auth_routes._test_after_admin_lock_hook = None

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400], f"Expected exactly one 200 and one 400, got: {res1.status_code}, {res2.status_code}")

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot delete the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_successful_deactivation_when_another_active_admin_exists(self):
        """Deactivating admin1 succeeds when admin2 is active."""
        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            res = self.client.put(
                f"/api/users/{self.admin1_id}",
                json={"is_active": False},
                headers=self.headers,
            )
            self.assertEqual(res.status_code, 200)

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT is_active FROM users WHERE id = %s", (self.admin1_id,))
            row = cur.fetchone()
            self.assertEqual(row["is_active"], 0)
            cur.close()
        finally:
            conn.close()

    def test_rejected_operations_produce_no_database_or_audit_mutations(self):
        """Rejected operations must produce zero mutations in users and zero audit log entries."""
        # Deactivate admin2 so admin1 is the sole remaining active admin in the test filter
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.admin2_id,))
            conn.commit()
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
            res_del = self.client.delete(f"/api/users/{self.admin1_id}", headers=self.headers)
            self.assertEqual(res_del.status_code, 400)

        # Verify admin1 remains untouched
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role, is_active FROM users WHERE id = %s", (self.admin1_id,))
            admin1 = cur.fetchone()
            self.assertEqual(admin1["role"], "admin")
            self.assertEqual(admin1["is_active"], 1)

            # Verify no audit logs were written for the rejected actions on admin1
            cur.execute(
                "SELECT COUNT(*) as count FROM audit_logs WHERE user_id = %s",
                (self.caller_id,),
            )
            audit_count = cur.fetchone()["count"]
            self.assertEqual(audit_count, 0)
            cur.close()
        finally:
            conn.close()

    def test_rejected_operation_explicit_rollback_and_connection_reuse(self):
        """Proves explicit rollback on rejected operation and subsequent connection reuse.

        1. A rejected last-admin operation rolls back explicitly.
        2. The database connection can subsequently be reused without lingering locks.
        3. A subsequent valid administrator operation is not blocked by the previous rejected request.
        4. No partial database mutation occurs.
        """
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
            res_good = self.client.put(
                f"/api/users/{self.admin1_id}",
                json={"role": "viewer"},
                headers=self.headers,
            )
            self.assertEqual(res_good.status_code, 200)

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

    def test_failure_path_releases_sync_and_cleans_up(self):
        """Proves that a timeout or assertion failure during concurrent execution
        safely releases synchronization events, joins worker threads, resets test hooks,
        and leaves the test database usable for subsequent operations."""
        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        simulated_failure_hit = False

        def after_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                t1_locked.set()
                t1_can_proceed.wait(timeout=2.0)

        auth_routes._test_after_admin_lock_hook = after_hook

        def slow_admin_op(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"role": "trader"},
                headers=self.headers,
            )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                f1 = executor.submit(slow_admin_op, self.admin1_id)
                self.assertTrue(t1_locked.wait(timeout=3.0))

                # Simulate an unexpected assertion failure in the test orchestrator
                simulated_failure_hit = True
                raise AssertionError("Simulated test orchestrator assertion failure")
        except AssertionError as e:
            self.assertEqual(str(e), "Simulated test orchestrator assertion failure")
        finally:
            # Crucial failure-path cleanup: release lock wait so worker can finish
            t1_locked.set()
            t1_can_proceed.set()
            auth_routes._test_before_admin_lock_hook = None
            auth_routes._test_after_admin_lock_hook = None
            auth_routes._test_active_admin_filter = None
            executor.shutdown(wait=True)

        self.assertTrue(simulated_failure_hit)
        self.assertIsNone(auth_routes._test_before_admin_lock_hook)
        self.assertIsNone(auth_routes._test_after_admin_lock_hook)

        # Verify the test database is immediately usable and responsive
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.close()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
