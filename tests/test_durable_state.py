import os
import time
import unittest
from unittest.mock import MagicMock, patch

from web import auth, job_store


class TestRateLimitFallback(unittest.TestCase):
    def setUp(self):
        auth._login_attempts.clear()
        auth._durable_rate_limit = False

    def tearDown(self):
        auth._durable_rate_limit = False
        auth._login_attempts.clear()

    def test_db_failure_falls_back_to_memory_and_still_locks_out(self):
        auth._durable_rate_limit = True
        with patch("web.auth.get_pool", side_effect=RuntimeError("db down")):
            for _ in range(auth.RATE_LIMIT_MAX_ATTEMPTS):
                self.assertTrue(auth.check_login_rate_limit("k"))
                auth.record_failed_login("k")
            self.assertFalse(auth.check_login_rate_limit("k"))

    def test_enable_returns_false_and_stays_in_memory_without_table(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("no db")):
            self.assertFalse(auth.enable_durable_rate_limit())
        self.assertFalse(auth._durable_rate_limit)


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
        auth._login_attempts.clear()
        self.assertTrue(auth.enable_durable_rate_limit(), "login_attempts table missing; run python -m db.migrate")
        auth.reset_login_attempts("it-key")

    def tearDown(self):
        auth.reset_login_attempts("it-key")
        auth._durable_rate_limit = False

    def test_lockout_is_shared_through_the_database_not_process_memory(self):
        for _ in range(auth.RATE_LIMIT_MAX_ATTEMPTS):
            auth.record_failed_login("it-key")
        auth._login_attempts.clear()  # simulate a restart / another worker
        self.assertFalse(auth.check_login_rate_limit("it-key"))
        auth.reset_login_attempts("it-key")
        self.assertTrue(auth.check_login_rate_limit("it-key"))

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
