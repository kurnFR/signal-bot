import os
import subprocess
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from web import auth, job_store


class TestRateLimitFailClosed(unittest.TestCase):
    def setUp(self):
        auth._login_attempts.clear()
        auth._durable_rate_limit = False
        auth._durable_rate_limit_degraded = False

    def tearDown(self):
        auth._durable_rate_limit = False
        auth._durable_rate_limit_degraded = False
        auth._login_attempts.clear()

    def test_db_failure_fails_closed_without_touching_process_local_counter(self):
        auth._durable_rate_limit = True
        with patch("web.auth.get_pool", side_effect=RuntimeError("db down")):
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.check_login_rate_limit("k")
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.record_failed_login("k")
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.reset_login_attempts("k")
        self.assertEqual(auth._login_attempts, {})
        self.assertTrue(auth.get_rate_limiter_health()["degraded"])
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "unavailable")

    def test_enable_failure_keeps_shared_backend_required_and_marks_unavailable(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("no db")):
            self.assertFalse(auth.enable_durable_rate_limit())
        self.assertTrue(auth._durable_rate_limit)
        self.assertTrue(auth.get_rate_limiter_health()["degraded"])
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "unavailable")

    def test_enable_requires_reservation_state_schema(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.execute.side_effect = RuntimeError("unknown column attempt_state")
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            self.assertFalse(auth.enable_durable_rate_limit())
        self.assertEqual(
            cur.execute.call_args.args[0],
            "SELECT attempt_state FROM login_attempts LIMIT 1",
        )
        self.assertTrue(auth._durable_rate_limit)
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "unavailable")
        conn.close.assert_called_once()

    def test_runtime_recovery_restores_database_mode(self):
        auth._durable_rate_limit = True
        auth._durable_rate_limit_degraded = True
        with patch("web.auth._db_count_attempts", return_value=0):
            self.assertTrue(auth.check_login_rate_limit("k"))
        self.assertFalse(auth.get_rate_limiter_health()["degraded"])
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "database")

    def test_atomic_admission_reserves_attempt_under_database_lock(self):
        auth._durable_rate_limit = True
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [(1,), (2,), (1,)]
        cur.lastrowid = 123
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            allowed, count, reserved = auth.admit_login_attempt("concurrent-key")
        self.assertEqual((allowed, count, reserved), (True, 3, 123))
        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("SELECT GET_LOCK(%s, %s)", sql_calls)
        self.assertTrue(any("INSERT INTO login_attempts" in sql for sql in sql_calls))
        self.assertIn("SELECT RELEASE_LOCK(%s)", sql_calls)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_successful_login_finalization_preserves_other_pending_reservations(self):
        auth._durable_rate_limit = True
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [(1,), (1,)]
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            self.assertEqual(auth.finalize_login_attempt("same-key", 101, success=True), 0)
        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn(
            "DELETE FROM login_attempts WHERE rate_key = %s AND attempt_state = 'failed'",
            sql_calls,
        )
        self.assertIn(
            "DELETE FROM login_attempts WHERE id = %s AND rate_key = %s AND attempt_state = 'pending'",
            sql_calls,
        )
        self.assertFalse(any(
            sql.strip() == "DELETE FROM login_attempts WHERE rate_key = %s"
            for sql in sql_calls
        ))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_failed_login_finalization_converts_only_its_own_reservation(self):
        auth._durable_rate_limit = True
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [(1,), (3,), (1,)]
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            self.assertEqual(auth.finalize_login_attempt("same-key", 202, success=False), 3)
        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any(
            "UPDATE login_attempts SET attempt_state = 'failed'" in sql
            and "WHERE id = %s AND rate_key = %s AND attempt_state = 'pending'" in sql
            for sql in sql_calls
        ))
        self.assertFalse(any("DELETE FROM login_attempts WHERE rate_key = %s AND attempt_state = 'failed'" in sql for sql in sql_calls))
        conn.commit.assert_called_once()

    def test_atomic_admission_lock_timeout_fails_closed(self):
        auth._durable_rate_limit = True
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = (0,)
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.admit_login_attempt("busy-key")
        self.assertFalse(any("INSERT INTO login_attempts" in call.args[0] for call in cur.execute.call_args_list))
        self.assertTrue(auth.get_rate_limiter_health()["degraded"])
        conn.close.assert_called_once()

    def test_atomic_admission_denies_when_threshold_reached(self):
        auth._durable_rate_limit = True
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.side_effect = [(1,), (auth.RATE_LIMIT_MAX_ATTEMPTS,), (1,)]
        with patch("web.auth.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            allowed, count, reserved = auth.admit_login_attempt("locked-key")
        self.assertEqual((allowed, count, reserved), (False, auth.RATE_LIMIT_MAX_ATTEMPTS, None))
        self.assertFalse(any("INSERT INTO login_attempts" in call.args[0] for call in cur.execute.call_args_list))
        self.assertIn("SELECT RELEASE_LOCK(%s)", [call.args[0] for call in cur.execute.call_args_list])

    def test_malformed_database_count_fails_closed(self):
        auth._durable_rate_limit = True
        with patch("web.auth._db_count_attempts", side_effect=TypeError("malformed count")):
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.check_login_rate_limit("k")
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "unavailable")


class TestJobStoreUnit(unittest.TestCase):
    def test_merge_prefers_live_and_sorts_newest_first(self):
        stored = [{"id": "a", "status": "running", "created_at": 1}, {"id": "b", "status": "completed", "created_at": 5}]
        live = [{"id": "a", "status": "completed", "created_at": 1}, {"id": "c", "status": "queued", "created_at": 9}]
        merged = job_store.merge_jobs(live, stored)
        self.assertEqual([j["id"] for j in merged], ["c", "b", "a"])
        self.assertEqual(merged[2]["status"], "completed")

    def test_merge_respects_limit(self):
        stored = [{"id": str(i), "created_at": i} for i in range(50)]
        self.assertEqual(len(job_store.merge_jobs([], stored, limit=30)), 30)

    def test_save_swallows_database_errors(self):
        def boom():
            raise RuntimeError("db down")
        self.assertFalse(job_store.save_job({"id": "x", "type": "backfill"}, connect=boom))
        self.assertIsNone(job_store.load_job("x", connect=boom))
        self.assertEqual(job_store.list_jobs(connect=boom), [])
        self.assertEqual(job_store.mark_interrupted(connect=boom), 0)

    def test_mark_interrupted_only_touches_other_processes_active_jobs(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.rowcount = 2
        self.assertEqual(job_store.mark_interrupted(connect=lambda: conn, pid=42), 2)
        sql, params = cur.execute.call_args[0]
        self.assertIn("'queued','running'", sql)
        self.assertIn("owner_pid <> %s", sql)
        self.assertEqual(params, (42,))

    def test_row_conversion_flattens_payload(self):
        job = job_store._row_to_job({"job_id": "j", "job_type": "backfill", "status": "completed", "progress": 100,
                                     "message": "ok", "payload": '{"symbol": "BTCUSDT", "candles_fetched": 5}',
                                     "created_at": 1.5, "updated_at": 2.5})
        self.assertEqual((job["id"], job["symbol"], job["candles_fetched"]), ("j", "BTCUSDT", 5))


@unittest.skipUnless(os.getenv("WALLET_TRACKER_MARIADB_TESTS") == "1", "opt-in MariaDB integration test")
class TestDurableStateOnMariaDB(unittest.TestCase):
    """Requires the migrations to be applied: python -m db.migrate"""

    def setUp(self):
        auth._durable_rate_limit = False
        auth._durable_rate_limit_degraded = False
        auth._login_attempts.clear()
        self.assertTrue(auth.enable_durable_rate_limit(), "login_attempts table missing; run python -m db.migrate")
        auth.reset_login_attempts("it-key")

    def tearDown(self):
        auth.reset_login_attempts("it-key")
        auth.reset_login_attempts("it-concurrent")
        auth.reset_login_attempts("it-finalize")
        auth._durable_rate_limit = False
        auth._durable_rate_limit_degraded = False

    def test_lockout_is_shared_through_the_database_not_process_memory(self):
        for _ in range(auth.RATE_LIMIT_MAX_ATTEMPTS):
            auth.record_failed_login("it-key")
        auth._login_attempts.clear()  # simulate a restart / another worker
        self.assertFalse(auth.check_login_rate_limit("it-key"))
        auth.reset_login_attempts("it-key")
        self.assertTrue(auth.check_login_rate_limit("it-key"))

    def test_lockout_is_enforced_in_a_separate_worker_process(self):
        for _ in range(auth.RATE_LIMIT_MAX_ATTEMPTS):
            auth.record_failed_login("it-key")
        script = (
            "from web import auth; "
            "assert auth.enable_durable_rate_limit(); "
            "assert not auth.check_login_rate_limit('it-key')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        auth.reset_login_attempts("it-key")

    def test_concurrent_worker_processes_cannot_exceed_shared_attempt_limit(self):
        auth.reset_login_attempts("it-concurrent")
        script = """
import json
from concurrent.futures import ThreadPoolExecutor
import time
from web import auth
assert auth.enable_durable_rate_limit()
time.sleep(1)  # allow the sibling worker process to start before contention
def attempt(_):
    return auth.admit_login_attempt('it-concurrent')[0]
with ThreadPoolExecutor(max_workers=5) as pool:
    allowed = sum(pool.map(attempt, range(5)))
print(json.dumps({"allowed": allowed}))
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=os.getcwd(),
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=45)
                self.assertEqual(process.returncode, 0, stderr)
                results.append(int(__import__("json").loads(stdout)["allowed"]))
            self.assertEqual(sum(results), auth.RATE_LIMIT_MAX_ATTEMPTS)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            auth.reset_login_attempts("it-concurrent")

    def test_successful_login_finalization_preserves_sibling_pending_attempt(self):
        auth.reset_login_attempts("it-finalize")
        allowed_a, _, reservation_a = auth.admit_login_attempt("it-finalize")
        allowed_b, _, reservation_b = auth.admit_login_attempt("it-finalize")
        self.assertTrue(allowed_a and allowed_b)
        self.assertIsNotNone(reservation_a)
        self.assertIsNotNone(reservation_b)

        auth.finalize_login_attempt("it-finalize", reservation_a, success=True)
        failed_count = auth.finalize_login_attempt("it-finalize", reservation_b, success=False)

        self.assertEqual(failed_count, 1)
        self.assertEqual(auth._db_count_attempts("it-finalize", time.time()), 1)
        auth.reset_login_attempts("it-finalize")

    def test_runtime_storage_recovery_reenables_login_checks(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("temporary outage")):
            with self.assertRaises(auth.RateLimitBackendUnavailable):
                auth.check_login_rate_limit("it-key")
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "unavailable")
        self.assertTrue(auth.check_login_rate_limit("it-key"))
        self.assertEqual(auth.get_rate_limiter_health()["mode"], "database")

    def test_job_round_trip_and_restart_interruption(self):
        jid = f"it{int(time.time()) % 100000}"
        job = {"id": jid, "type": "backfill", "status": "running", "progress": 40.0,
               "message": "half", "symbol": "BTCUSDT", "candles_fetched": 10, "created_at": time.time()}
        self.assertTrue(job_store.save_job(job))
        loaded = job_store.load_job(jid)
        self.assertEqual((loaded["status"], loaded["symbol"], loaded["candles_fetched"]), ("running", "BTCUSDT", 10))
        self.assertGreaterEqual(job_store.mark_interrupted(pid=os.getpid() + 1), 1)
        self.assertEqual(job_store.load_job(jid)["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
