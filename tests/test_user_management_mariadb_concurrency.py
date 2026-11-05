"""Real MariaDB concurrency and transaction serialization tests for user management.

Validates that explicit row-level locking (SELECT ... FOR UPDATE ORDER BY id)
prevents race conditions that could leave the system with zero active administrators,
and verifies that rejected operations produce zero database or audit mutations.
All tests run against the disposable test database 'signal_bot_test'.
"""
import concurrent.futures
import os
import unittest
from fastapi.testclient import TestClient

import config
import db.db as db_module
from web.app import app
from web.auth import create_access_token, hash_password


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

    def tearDown(self):
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

    def test_concurrent_admin_demotion_prevents_zero_admins(self):
        # Caller attempts to demote BOTH admin1 and admin2 concurrently.
        # Serialization must ensure exactly one succeeds and one is rejected.
        def demote_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"role": "trader"},
                headers=self.headers,
            )

        # First, deactivate caller to make admin1 and admin2 the only active admins
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.caller_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        # Re-verify initial state: exactly 2 active test admins
        self.assertEqual(self._count_active_test_admins(), 2)

        # Mock authentication for caller while queries execute against real MariaDB
        user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

        from unittest.mock import patch
        with patch("web.app.authenticate_request", return_value=user_auth), \
             patch("web.auth.authenticate_request", return_value=user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(demote_user, self.admin1_id)
                f2 = executor.submit(demote_user, self.admin2_id)
                res1 = f1.result()
                res2 = f2.result()

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400], f"Expected exactly one 200 and one 400, got: {res1.status_code}, {res2.status_code}")

        # The rejected response must carry the last-admin protection message
        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        # MariaDB must contain at least 1 active admin (never 0)
        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_concurrent_admin_deactivation_prevents_zero_admins(self):
        # Caller attempts to deactivate BOTH admin1 and admin2 concurrently.
        def deactivate_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.put(
                f"/api/users/{target_id}",
                json={"is_active": False},
                headers=self.headers,
            )

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

        user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

        from unittest.mock import patch
        with patch("web.app.authenticate_request", return_value=user_auth), \
             patch("web.auth.authenticate_request", return_value=user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(deactivate_user, self.admin1_id)
                f2 = executor.submit(deactivate_user, self.admin2_id)
                res1 = f1.result()
                res2 = f2.result()

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400])

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot demote or deactivate the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_concurrent_admin_deletion_prevents_zero_admins(self):
        # Caller attempts to delete BOTH admin1 and admin2 concurrently.
        def delete_user(target_id):
            client = TestClient(app, raise_server_exceptions=False)
            return client.delete(
                f"/api/users/{target_id}",
                headers=self.headers,
            )

        # Deactivate caller to isolate admin1 and admin2
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (self.caller_id,))
            conn.commit()
            cur.close()
        finally:
            conn.close()

        self.assertEqual(self._count_active_test_admins(), 2)

        user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

        from unittest.mock import patch
        with patch("web.app.authenticate_request", return_value=user_auth), \
             patch("web.auth.authenticate_request", return_value=user_auth):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(delete_user, self.admin1_id)
                f2 = executor.submit(delete_user, self.admin2_id)
                res1 = f1.result()
                res2 = f2.result()

        status_codes = sorted([res1.status_code, res2.status_code])
        self.assertEqual(status_codes, [200, 400])

        rejected = res1 if res1.status_code == 400 else res2
        self.assertIn("Cannot delete the last active administrator", rejected.json()["detail"])

        remaining_admins = self._count_active_test_admins()
        self.assertEqual(remaining_admins, 1)

    def test_successful_deactivation_when_another_active_admin_exists(self):
        # Deactivating admin1 succeeds when admin2 and caller are both active
        user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }
        from unittest.mock import patch
        with patch("web.app.authenticate_request", return_value=user_auth), \
             patch("web.auth.authenticate_request", return_value=user_auth):
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

        user_auth = {
            "id": self.caller_id, "username": "conc_test_caller",
            "email": "conc_caller@test.com", "role": "admin",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }
        from unittest.mock import patch
        with patch("web.app.authenticate_request", return_value=user_auth), \
             patch("web.auth.authenticate_request", return_value=user_auth):
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


if __name__ == "__main__":
    unittest.main()
