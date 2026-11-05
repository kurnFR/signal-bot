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
from unittest.mock import patch, Mock
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
        self._future_wait_timeout = 5.0
        self._thread_join_timeout = 3.0
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

        timeout_futures = getattr(self, "_future_wait_timeout", 5.0)
        timeout_threads = getattr(self, "_thread_join_timeout", 3.0)

        # 1. Bounded future wait & record unfinished futures without immediately raising
        unfinished_futures = []
        if hasattr(self, "_active_futures") and self._active_futures:
            done, not_done = concurrent.futures.wait(self._active_futures, timeout=timeout_futures)
            if not_done:
                unfinished_futures.extend(not_done)
            for fut in self._active_futures:
                if not fut.done() and fut not in unfinished_futures:
                    unfinished_futures.append(fut)

        # Retain unfinished future references until their completion is confirmed
        if hasattr(self, "_active_futures"):
            self._active_futures = [fut for fut in self._active_futures if not fut.done()]

        # 2. In all cases, signal shutdown on every tracked executor using shutdown(wait=False, cancel_futures=True)
        # and perform bounded joins with one strict shared deadline per executor
        executor_errors = []
        thread_errors = []
        surviving_threads = []
        surviving_executors = []

        if hasattr(self, "_active_executors") and self._active_executors:
            for ex in list(self._active_executors):
                ex_shutdown_failed = False
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                except Exception as exc:
                    ex_shutdown_failed = True
                    executor_errors.append(f"Executor shutdown failed: {exc}")

                start_wait = time.time()
                threads = list(getattr(ex, "_threads", set()))
                ex_surviving_threads = []

                for t in threads:
                    remaining = timeout_threads - (time.time() - start_wait)
                    try:
                        if remaining > 0:
                            t.join(timeout=remaining)
                        if t.is_alive():
                            ex_surviving_threads.append(t)
                    except Exception as exc:
                        thread_errors.append(f"Thread {getattr(t, 'name', 'unknown')} join raised: {exc}")
                        if t.is_alive():
                            ex_surviving_threads.append(t)

                if ex_surviving_threads:
                    surviving_threads.extend(ex_surviving_threads)
                    surviving_executors.append(ex)
                elif ex_shutdown_failed:
                    surviving_executors.append(ex)

            # Retain references to executors with surviving threads or shutdown failures
            self._active_executors = surviving_executors

        cleanup_failures = []
        if unfinished_futures:
            cleanup_failures.append(
                f"{len(unfinished_futures)} worker future(s) did not complete within bounded wait ({timeout_futures}s)"
            )
        if surviving_threads:
            t_names = ", ".join(t.name for t in surviving_threads)
            cleanup_failures.append(
                f"{len(surviving_threads)} worker thread(s) failed to terminate within bounded wait ({timeout_threads}s): {t_names}"
            )
        if executor_errors:
            cleanup_failures.extend(executor_errors)
        if thread_errors:
            cleanup_failures.extend(thread_errors)

        # 3. Never delete fixtures or drain database pools while any worker thread remains active
        if surviving_threads:
            err_msg = "; ".join(cleanup_failures)
            raise RuntimeError(
                f"Test teardown failed with surviving worker thread(s) (fixtures and pool untouched): {err_msg}"
            )

        # 4. If all worker threads have terminated, safely delete test fixtures
        fixture_error = None
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
                self.seeded_user_ids = []
            except Exception as exc:
                fixture_error = exc
            finally:
                conn.close()

        if fixture_error is not None:
            cleanup_failures.append(f"Fixture deletion failed: {fixture_error}")

        # 5. Verify no checked-out connections were leaked by the test
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
            cleanup_failures.append(
                f"{self.pool.pool_name} has {expected - qsize} unreturned connection(s) (queue has {qsize}/{expected})"
            )

        # 6. Raise visible errors for any unfinished futures, surviving threads, or cleanup failures
        if cleanup_failures:
            raise RuntimeError(f"Test teardown failed: {'; '.join(cleanup_failures)}")

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

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
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

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
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

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
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

    def test_concurrent_trader_promotion_and_deletion_serialization(self):
        """Proves transaction serialization between trader promotion and deletion.

        1. Thread 1 initiates trader promotion to admin and acquires row locks on active admins.
        2. Thread 2 attempts to delete the trader on concurrent connection.
        3. Thread 2 blocks in MariaDB LOCK WAIT on Thread 2's exact thread ID.
        4. Once exact LOCK WAIT is verified, Thread 1 commits.
        5. Thread 2 unblocks, executes serialized under lock, and cleanly completes.
        """
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            pwd_hash = hash_password("SecurePassword123!")
            u_trader = f"{self.test_prefix}_t_promo"
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, session_version) "
                "VALUES (%s, %s, %s, 'trader', 1, 0)",
                (u_trader, f"{u_trader}@test.com", pwd_hash),
            )
            conn.commit()
            cur.execute("SELECT id FROM users WHERE username = %s", (u_trader,))
            trader_id = cur.fetchone()[0]
            self.seeded_user_ids.append(trader_id)
            cur.close()
        finally:
            conn.close()

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if t1_locked.is_set():
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if not t1_locked.is_set():
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def promote_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"role": "admin"},
                headers=self.headers,
            )

        def delete_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.delete(
                f"/api/users/{target_id}",
                headers=self.headers,
            )

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                f1 = executor.submit(promote_user, trader_id)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                f2 = executor.submit(delete_user, trader_id)
                self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                t2_tid = t2_thread_id_holder[0]

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

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE id = %s", (trader_id,))
            self.assertIsNone(cur.fetchone())
            cur.close()
        finally:
            conn.close()

    def test_concurrent_admin_activation_and_deactivation_serialization(self):
        """Proves transaction serialization when activating an admin concurrently with deactivating an admin.

        1. Test database has exactly one active admin (admin1) and one inactive admin (admin3).
        2. Thread 1 activates admin3, acquiring lock on active admin1.
        3. Thread 2 attempts to deactivate admin1 (which would fail as last-admin if not serialized).
        4. Thread 2 enters MariaDB LOCK WAIT on Thread 2's exact thread ID.
        5. Thread 1 commits activation of admin3.
        6. Thread 2 unblocks, re-evaluates active admins under lock, finds admin3 active,
           and successfully deactivates admin1.
        7. Exactly one active admin (admin3) remains, never zero.
        """
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            pwd_hash = hash_password("SecurePassword123!")
            u_admin3 = f"{self.test_prefix}_adm3"
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.admin2_id,))
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, session_version) "
                "VALUES (%s, %s, %s, 'admin', 0, 0)",
                (u_admin3, f"{u_admin3}@test.com", pwd_hash),
            )
            conn.commit()
            cur.execute("SELECT id FROM users WHERE username = %s", (u_admin3,))
            admin3_id = cur.fetchone()[0]
            self.seeded_user_ids.append(admin3_id)
            cur.close()
        finally:
            conn.close()

        auth_routes._test_active_admin_filter = lambda u: u["id"] in {self.admin1_id, self.admin2_id, admin3_id}

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, target_id):
            if t1_locked.is_set():
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, target_id):
            if not t1_locked.is_set():
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def set_user_active(target_id, active: bool):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"is_active": active},
                headers=self.headers,
            )

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                f1 = executor.submit(set_user_active, admin3_id, True)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                f2 = executor.submit(set_user_active, self.admin1_id, False)
                self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                t2_tid = t2_thread_id_holder[0]

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

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT is_active FROM users WHERE id = %s", (admin3_id,))
            self.assertEqual(cur.fetchone()["is_active"], 1)

            cur.execute("SELECT is_active FROM users WHERE id = %s", (self.admin1_id,))
            self.assertEqual(cur.fetchone()["is_active"], 0)

            cur.execute(
                "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = 1 AND id IN (%s, %s, %s)",
                (self.admin1_id, self.admin2_id, admin3_id),
            )
            self.assertEqual(cur.fetchone()["cnt"], 1)
            cur.close()
        finally:
            conn.close()

    def test_concurrent_role_change_and_deletion_same_target(self):
        """Proves transaction serialization for concurrent role change and deletion on the same target.

        1. Thread 1 initiates deletion of target trader and acquires active admin lock.
        2. Thread 2 attempts role change to viewer on the same target.
        3. Thread 2 enters MariaDB LOCK WAIT on Thread 2's exact thread ID.
        4. Thread 1 commits deletion.
        5. Thread 2 unblocks, attempts to read target under lock, gets 404, and rolls back cleanly.
        6. Proves target is deleted and no unintended user or audit log mutations occur for Thread 2.
        """
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            pwd_hash = hash_password("SecurePassword123!")
            u_target = f"{self.test_prefix}_target"
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active, session_version) "
                "VALUES (%s, %s, %s, 'trader', 1, 0)",
                (u_target, f"{u_target}@test.com", pwd_hash),
            )
            conn.commit()
            cur.execute("SELECT id FROM users WHERE username = %s", (u_target,))
            target_id = cur.fetchone()[0]
            self.seeded_user_ids.append(target_id)
            cur.close()
        finally:
            conn.close()

        t1_locked = threading.Event()
        t1_can_proceed = threading.Event()
        t2_about_to_query = threading.Event()
        t2_thread_id_holder = []

        def before_hook(hook_conn, hook_cur, tid):
            if t1_locked.is_set():
                t2_thread_id_holder.append(hook_conn.connection_id)
                t2_about_to_query.set()

        def after_hook(hook_conn, hook_cur, tid):
            if not t1_locked.is_set():
                t1_locked.set()
                if not t1_can_proceed.wait(timeout=10.0):
                    raise TimeoutError("Test timed out waiting for t1_can_proceed")

        auth_routes._test_before_admin_lock_hook = before_hook
        auth_routes._test_after_admin_lock_hook = after_hook

        def delete_user(tid):
            client = TestClient(app, raise_server_exceptions=False)
            return client.delete(
                f"/api/users/{tid}",
                headers=self.headers,
            )

        def update_role(tid):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{tid}",
                json={"role": "viewer"},
                headers=self.headers,
            )

        executor = self._create_executor(max_workers=2)
        try:
            with patch("web.app.authenticate_request", return_value=self.user_auth), \
                 patch("web.auth.authenticate_request", return_value=self.user_auth):
                f1 = executor.submit(delete_user, target_id)
                self.assertTrue(t1_locked.wait(timeout=5.0), "Thread 1 failed to acquire row lock")

                f2 = executor.submit(update_role, target_id)
                self.assertTrue(t2_about_to_query.wait(timeout=5.0), "Thread 2 failed to reach locking query")
                t2_tid = t2_thread_id_holder[0]

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

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 404)
        self.assertIn("Target user not found", res2.json()["detail"])

        conn = self.pool.get_connection()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id FROM users WHERE id = %s", (target_id,))
            self.assertIsNone(cur.fetchone())

            cur.execute(
                "SELECT action, details FROM audit_logs WHERE user_id = %s",
                (self.caller_id,),
            )
            logs = cur.fetchall()
            delete_logs = [l for l in logs if l["action"] == "USER_DELETED" and f'"deleted_id": {target_id}' in l["details"]]
            update_logs = [l for l in logs if f'"target_id": {target_id}' in l["details"] or f'"user_id": {target_id}' in l["details"]]
            self.assertEqual(len(delete_logs), 1)
            self.assertEqual(len(update_logs), 0)
            cur.close()
        finally:
            conn.close()

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

    def test_unfinished_future_teardown_signals_shutdown_on_all_executors(self):
        """Regression test: proves tearDown() signals executor shutdown even on unfinished futures,
        retains surviving executors and unfinished futures, protects fixtures, and cleans up completely
        once workers terminate.

        Verifies:
        1. Both executors receive shutdown signals.
        2. Surviving executor references remain tracked after failed teardown.
        3. Unfinished future references are not discarded prematurely.
        4. Test fixtures are not deleted while worker threads remain active.
        5. After workers are released, a subsequent cleanup attempt confirms termination.
        6. All executors and worker threads are eventually cleaned up.
        7. Fixtures and connection pools are only cleaned after worker termination.
        """
        task_blocker = threading.Event()

        def slow_task():
            task_blocker.wait(timeout=5.0)

        # Create two separate executors to prove shutdown is signaled across all tracked executors
        ex1 = self._create_executor(max_workers=1)
        ex2 = self._create_executor(max_workers=1)

        fut1 = ex1.submit(slow_task)
        fut2 = ex2.submit(slow_task)

        # Save seeded user IDs before teardown attempt
        saved_user_ids = list(self.seeded_user_ids)

        # Use short bounded timeouts for the regression test
        self._future_wait_timeout = 0.1
        self._thread_join_timeout = 0.2

        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.tearDown()

            # 1. Confirm error message explicitly reports unfinished futures
            self.assertIn("worker future(s) did not complete within bounded wait", str(ctx.exception))

            # 2. Both executors received shutdown signals
            self.assertTrue(ex1._shutdown, "Executor 1 did not have shutdown signaled")
            self.assertTrue(ex2._shutdown, "Executor 2 did not have shutdown signaled")

            # 3. Surviving executor references remain tracked after failed teardown
            self.assertIn(ex1, self._active_executors)
            self.assertIn(ex2, self._active_executors)
            self.assertEqual(len(self._active_executors), 2)

            # 4. Unfinished future references are not discarded prematurely
            self.assertIn(fut1, self._active_futures)
            self.assertIn(fut2, self._active_futures)
            self.assertEqual(len(self._active_futures), 2)

            # 5. Fixtures were NOT deleted while worker threads were active
            conn = self.pool.get_connection()
            try:
                cur = conn.cursor()
                placeholders = ",".join(["%s"] * len(saved_user_ids))
                cur.execute(f"SELECT COUNT(*) FROM users WHERE id IN ({placeholders})", tuple(saved_user_ids))
                count_alive = cur.fetchone()[0]
                self.assertEqual(count_alive, len(saved_user_ids), "Fixtures were prematurely deleted while threads were active")
                cur.close()
            finally:
                conn.close()

            # 6. Unblock worker threads so they terminate
            task_blocker.set()
            fut1.result(timeout=2.0)
            fut2.result(timeout=2.0)
            self.assertTrue(fut1.done())
            self.assertTrue(fut2.done())

            # 7. Restore standard timeouts and perform subsequent cleanup attempt
            self._future_wait_timeout = 5.0
            self._thread_join_timeout = 3.0
            self.tearDown()

            # 8. All executors and worker threads are eventually cleaned up
            self.assertEqual(len(self._active_executors), 0, "Executors were not cleaned up after worker termination")
            self.assertEqual(len(self._active_futures), 0, "Futures were not cleaned up after worker termination")

            # 9. Fixtures are now confirmed deleted after worker termination
            conn = self.pool.get_connection()
            try:
                cur = conn.cursor()
                placeholders = ",".join(["%s"] * len(saved_user_ids))
                cur.execute(f"SELECT COUNT(*) FROM users WHERE id IN ({placeholders})", tuple(saved_user_ids))
                count_deleted = cur.fetchone()[0]
                self.assertEqual(count_deleted, 0, "Fixtures were not deleted after worker termination")
                cur.close()
            finally:
                conn.close()
        finally:
            task_blocker.set()
            self._future_wait_timeout = 5.0
            self._thread_join_timeout = 3.0

    def test_thread_join_exception_still_attempts_all_executors(self):
        """Proves that an exception during one thread's join does not prevent
        cleanup attempts on remaining threads and executors."""
        ex1 = self._create_executor(max_workers=1)
        ex2 = self._create_executor(max_workers=1)

        f1 = ex1.submit(lambda: None)
        f2 = ex2.submit(lambda: None)

        f1.result(timeout=2.0)
        f2.result(timeout=2.0)

        # Inject a failure into the first executor's thread join
        t1 = list(ex1._threads)[0]
        original_join = t1.join
        t1.join = Mock(side_effect=RuntimeError("Simulated thread join error"))

        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.tearDown()

            # The error must report the thread join failure
            self.assertIn("Simulated thread join error", str(ctx.exception))

            # Most importantly: ex2 must still have received shutdown signal and been attempted!
            self.assertTrue(ex1._shutdown, "Executor 1 did not have shutdown signaled")
            self.assertTrue(ex2._shutdown, "Executor 2 did not have shutdown signaled")
        finally:
            t1.join = original_join
            # Subsequent teardown should now be completely clean
            self.tearDown()
            self.assertEqual(len(self._active_executors), 0)


if __name__ == "__main__":
    unittest.main()
