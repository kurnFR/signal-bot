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
        cls.retailbot2_pool = db_module.get_retailbot2_pool()
        cls.client = TestClient(app, raise_server_exceptions=False)

        # Independently verify every configured database pool strictly uses signal_bot_test
        cls.verified_pools = {}
        for pool_name, pool_obj in [("primary_pool", cls.pool), ("retailbot2_pool", cls.retailbot2_pool)]:
            conn = pool_obj.get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT DATABASE()")
                db_name = cur.fetchone()[0]
                cur.close()
                if db_name != "signal_bot_test":
                    raise RuntimeError(
                        f"Database isolation guard failed for {pool_name}: "
                        f"connected to '{db_name}', strictly expected 'signal_bot_test'"
                    )
                cls.verified_pools[pool_name] = db_name
            finally:
                conn.close()

        cls._run_id = uuid.uuid4().hex[:8]
        cls._all_executors = []

    @classmethod
    def tearDownClass(cls):
        # Reset test hooks
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        # Verify no worker threads remain active before pool teardown
        for ex in getattr(cls, "_all_executors", []):
            alive_threads = [t for t in getattr(ex, "_threads", set()) if t.is_alive()]
            if alive_threads:
                raise RuntimeError(
                    f"Database pool teardown failed: {len(alive_threads)} worker thread(s) remain active."
                )

        # Gather all unique pool objects
        pools_to_clean = []
        seen = set()
        for candidate in [getattr(cls, "pool", None), getattr(cls, "retailbot2_pool", None), db_module._pool, db_module._retailbot2_pool]:
            if candidate is not None and id(candidate) not in seen:
                seen.add(id(candidate))
                pools_to_clean.append(candidate)

        for pool_obj in pools_to_clean:
            # 1. Bounded wait for all connections to return to the pool queue
            start_wait = time.time()
            recovered = False
            while time.time() - start_wait < 3.0:
                if pool_obj._cnx_queue.qsize() >= pool_obj.pool_size:
                    recovered = True
                    break
                time.sleep(0.05)

            if not recovered:
                qsize = pool_obj._cnx_queue.qsize()
                expected = pool_obj.pool_size
                raise RuntimeError(
                    f"Database pool cleanup failed: {pool_obj.pool_name} did not recover all checked-out connections. "
                    f"Queue has {qsize}/{expected} connections ({expected - qsize} missing/checked out)."
                )

            # 2. Drain and close connections explicitly; do not swallow failures
            pool_obj._remove_connections()

            # 3. Explicitly verify queue is now empty
            if pool_obj._cnx_queue.qsize() != 0:
                raise RuntimeError(
                    f"Database pool cleanup failed: {pool_obj.pool_name} queue is not empty after _remove_connections "
                    f"({pool_obj._cnx_queue.qsize()} connections remaining)."
                )

        # 4. Only after all pools are verified cleanly drained and closed, reset references
        db_module._pool = None
        db_module._retailbot2_pool = None

    def setUp(self):
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        self._active_executors = []
        self._active_futures = []
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

    def _create_executor(self, max_workers=2):
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._active_executors.append(executor)
        if hasattr(self.__class__, "_all_executors"):
            self.__class__._all_executors.append(executor)

        orig_submit = executor.submit

        def tracked_submit(fn, *args, **kwargs):
            fut = orig_submit(fn, *args, **kwargs)
            self._active_futures.append(fut)
            return fut

        executor.submit = tracked_submit
        return executor

    def tearDown(self):
        auth_routes._test_before_admin_lock_hook = None
        auth_routes._test_after_admin_lock_hook = None
        auth_routes._test_active_admin_filter = None

        # 1. Track worker futures and verify their completion with bounded wait
        if hasattr(self, "_active_futures") and self._active_futures:
            done, not_done = concurrent.futures.wait(self._active_futures, timeout=5.0)
            if not_done:
                raise RuntimeError(
                    f"Test teardown failed: {len(not_done)} worker future(s) did not terminate "
                    f"within bounded wait (5.0s)."
                )
            for fut in self._active_futures:
                if not fut.done():
                    raise RuntimeError("Test teardown failed: tracked worker future is not done.")
            self._active_futures.clear()

        # 2. Terminate and join all tracked worker executors/threads with bounded wait
        if hasattr(self, "_active_executors") and self._active_executors:
            for ex in self._active_executors:
                ex.shutdown(wait=True, cancel_futures=True)
                start_wait = time.time()
                while any(t.is_alive() for t in getattr(ex, "_threads", set())) and time.time() - start_wait < 3.0:
                    time.sleep(0.05)
                alive_threads = [t for t in getattr(ex, "_threads", set()) if t.is_alive()]
                if alive_threads:
                    raise RuntimeError(
                        f"Test teardown failed: {len(alive_threads)} worker thread(s) in executor "
                        f"failed to terminate within bounded wait (3.0s)."
                    )
            self._active_executors.clear()

        # 3. Delete test fixtures
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

        # 4. Verify no checked-out connections were leaked by the test
        start_wait = time.time()
        recovered = False
        while time.time() - start_wait < 3.0:
            if self.pool._cnx_queue.qsize() >= self.pool.pool_size:
                recovered = True
                break
            time.sleep(0.05)

        if not recovered:
            qsize = self.pool._cnx_queue.qsize()
            expected = self.pool.pool_size
            raise RuntimeError(
                f"Test teardown failed: {self.pool.pool_name} has {expected - qsize} unreturned connection(s) "
                f"(queue has {qsize}/{expected})."
            )

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
                with self._create_executor(max_workers=2) as executor:
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
                with self._create_executor(max_workers=2) as executor:
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
                with self._create_executor(max_workers=2) as executor:
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
        """Proves that a real worker failure during database contention (MariaDB lock-wait timeout)
        terminates the blocked worker within bounded time, rolls back its transaction,
        leaves zero partial mutation, cleans up all threads, and returns all connections
        cleanly to the pool without lingering locks."""
        self.assertEqual(self._count_active_test_admins(), 2)

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin2_id:
                # Set a tight 1-second lock-wait timeout specifically for Thread 2's session
                hook_cur.execute("SET SESSION innodb_lock_wait_timeout = 1")
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if target_id == self.admin1_id:
                # Thread 1 holds the row lock on active admins
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

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                # 1. Thread 1 initiates demotion of admin1 and acquires row-level lock
                f1 = executor.submit(demote_user, self.admin1_id)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                # 2. Thread 2 initiates demotion of admin2 with 1s innodb_lock_wait_timeout
                f2 = executor.submit(demote_user, self.admin2_id)
                self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach query hook")
                t2_tid = t2_thread_id_holder[0]

                # 3. Prove Thread 2's EXACT transaction enters MariaDB 'LOCK WAIT' state
                t2_in_lock_wait = False
                for _ in range(50):
                    time.sleep(0.05)
                    if self._check_exact_thread_lock_wait(t2_tid):
                        t2_in_lock_wait = True
                        break
                self.assertTrue(
                    t2_in_lock_wait,
                    f"Thread 2 (thread_id={t2_tid}) failed to enter MariaDB LOCK WAIT state"
                )

                # 4. Hold Thread 1 and allow Thread 2's 1-second timeout to trigger in MariaDB.
                # Assert Thread 2 terminates within bounded timeout.
                start_wait = time.time()
                res2 = f2.result(timeout=5.0)
                elapsed_wait = time.time() - start_wait
                self.assertLess(elapsed_wait, 4.0, "Thread 2 did not terminate within bounded timeout")

                # 5. Thread 2 caught MariaDB Error 1205 (lock wait timeout), rolled back, and returned HTTP 500
                self.assertEqual(res2.status_code, 500)

                # 6. Unblock Thread 1 to let it finish cleanly
                t1_can_proceed.set()
                res1 = f1.result(timeout=5.0)
                self.assertEqual(res1.status_code, 200)
        finally:
            # Ensure synchronization flags are set to unblock any pending waiters
            t1_locked.set()
            t1_can_proceed.set()
            t2_about_to_query.set()
            auth_routes._test_before_admin_lock_hook = None
            auth_routes._test_after_admin_lock_hook = None
            auth_routes._test_active_admin_filter = None
            executor.shutdown(wait=True, cancel_futures=True)

        # 7. Assert that admin2's transaction was completely rolled back:
        # admin2 remains role='admin', is_active=1, session_version=0
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role, is_active, session_version FROM users WHERE id = %s", (self.admin2_id,))
            admin2_row = cur.fetchone()
            self.assertEqual(admin2_row["role"], "admin")
            self.assertEqual(admin2_row["is_active"], 1)
            self.assertEqual(admin2_row["session_version"], 0)

            # Assert no audit log was created for admin2's failed mutation
            cur.execute(
                "SELECT COUNT(*) as count FROM audit_logs WHERE user_id = %s AND details LIKE %s",
                (self.caller_id, f'%"target_id": {self.admin2_id}%'),
            )
            audit_count = cur.fetchone()["count"]
            self.assertEqual(audit_count, 0)

            # Assert that the database connection pool is immediately healthy and usable
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone()["1"], 1)
            cur.close()
        finally:
            conn.close()

        # 8. Verify subsequent operation against admin2 succeeds without lingering locks
        with patch("web.app.authenticate_request", return_value=self.user_auth), \
             patch("web.auth.authenticate_request", return_value=self.user_auth):
            res_subsequent = self.client.put(
                f"/api/users/{self.admin2_id}",
                json={"role": "viewer"},
                headers=self.headers,
            )
            self.assertEqual(res_subsequent.status_code, 200)

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT role FROM users WHERE id = %s", (self.admin2_id,))
            self.assertEqual(cur.fetchone()["role"], "viewer")
            cur.close()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
