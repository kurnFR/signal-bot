import json
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from web.app import app
from web.auth import (
    create_access_token,
    verify_access_token,
    is_token_revoked,
    revoke_token,
    _revoked_tokens_cache,
    log_audit,
    log_audit_cursor,
    get_rate_limiter_health,
    record_failed_login,
    reset_login_attempts,
    _login_attempts,
    RateLimitBackendUnavailable,
)


class TestPhase1SecurityAndReliability(unittest.TestCase):
    def setUp(self):
        _revoked_tokens_cache.clear()
        _login_attempts.clear()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        _revoked_tokens_cache.clear()
        _login_attempts.clear()

    # ------------------------------------------------------------------------
    # 1. Token Revocation Fails Closed on Database Errors
    # ------------------------------------------------------------------------
    def test_token_revocation_fails_closed_when_storage_fails(self):
        token = create_access_token(user_id=10, username="trader_alice", role="trader")
        self.assertIsNotNone(verify_access_token(token))

        # When database fails during lookup:
        with patch("web.auth.get_pool", side_effect=RuntimeError("MariaDB connection refused")):
            # is_token_revoked must return True (fail-closed)
            self.assertTrue(is_token_revoked(token))
            # verify_access_token must reject the token
            self.assertIsNone(verify_access_token(token))

    def test_revoke_token_raises_when_persistence_fails(self):
        token = create_access_token(user_id=10, username="trader_alice", role="trader")
        with patch("web.auth.get_pool", side_effect=RuntimeError("Disk write failed")):
            with self.assertRaises(RuntimeError) as ctx:
                revoke_token(token)
            self.assertIn("Failed to persist revoked token to database", str(ctx.exception))

    def test_logout_returns_503_when_revocation_cannot_be_persisted(self):
        token = create_access_token(user_id=1, username="admin", role="admin")

        # Mock revoke_token specifically to fail as if the insert into revoked_tokens failed
        with patch("web.routes.auth_routes.revoke_token", side_effect=RuntimeError("DB cluster split-brain")):
            response = self.client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            # Must not tell the client logout succeeded when server-side persistence failed
            self.assertEqual(response.status_code, 503)
            self.assertIn("failed to revoke session token", response.json()["detail"])

    # ------------------------------------------------------------------------
    # 2. Durable Audit Logging & Atomic User Deletion
    # ------------------------------------------------------------------------
    def test_log_audit_raise_on_error_option(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("Connection dropped")):
            # Default swallows and returns False
            result = log_audit("TEST_ACTION", "admin", user_id=1, raise_on_error=False)
            self.assertFalse(result)

            # raise_on_error=True raises RuntimeError
            with self.assertRaises(RuntimeError) as ctx:
                log_audit("CRITICAL_ACTION", "admin", user_id=1, raise_on_error=True)
            self.assertIn("Critical audit logging failed", str(ctx.exception))

    def test_delete_user_writes_audit_log_atomically(self):
        admin_token = create_access_token(user_id=1, username="admin", role="admin")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"username": "target_user"}
        mock_conn.cursor.return_value = mock_cur

        with patch("web.routes.auth_routes.get_pool") as mock_get_pool:
            mock_get_pool.return_value.get_connection.return_value = mock_conn

            response = self.client.delete(
                "/api/users/42",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")

            # Check that DELETE FROM users and INSERT INTO audit_logs were executed
            executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
            self.assertTrue(any("DELETE FROM users WHERE id" in sql for sql in executed_sqls))
            self.assertTrue(any("INSERT INTO audit_logs" in sql for sql in executed_sqls))

            # Verify action was USER_DELETED
            audit_call = [call for call in mock_cur.execute.call_args_list if "INSERT INTO audit_logs" in call[0][0]][0]
            params = audit_call[0][1]
            self.assertIn("USER_DELETED", params)
            self.assertEqual(params[1], "admin")  # admin username

            # Must be committed
            mock_conn.commit.assert_called_once()

    # ------------------------------------------------------------------------
    # 3. Password Policy Enforcement for Admin Creation & Reset
    # ------------------------------------------------------------------------
    def test_admin_create_user_rejects_weak_dictionary_passwords(self):
        admin_token = create_access_token(user_id=1, username="admin", role="admin")

        # Weak password: password1234
        res = self.client.post(
            "/api/users",
            json={"username": "new_user", "password": "password1234", "role": "trader"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Password is too common or easy to guess", res.json()["detail"])

        # Repetitive / sequence password
        res2 = self.client.post(
            "/api/users",
            json={"username": "new_user2", "password": "1234567890", "role": "trader"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(res2.status_code, 400)
        self.assertIn("Choose something less predictable", res2.json()["detail"])

    def test_admin_reset_password_rejects_weak_passwords(self):
        admin_token = create_access_token(user_id=1, username="admin", role="admin")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"id": 5, "username": "existing_user"}
        mock_conn.cursor.return_value = mock_cur

        with patch("web.routes.auth_routes.get_pool") as mock_get_pool:
            mock_get_pool.return_value.get_connection.return_value = mock_conn

            res = self.client.post(
                "/api/users/5/reset-password",
                json={"new_password": "password1234"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            self.assertEqual(res.status_code, 400)
            self.assertIn("Password is too common or easy to guess", res.json()["detail"])

    # ------------------------------------------------------------------------
    # 4. GET /api/users Contract Parity (must_change_password)
    # ------------------------------------------------------------------------
    def test_list_users_includes_must_change_password(self):
        admin_token = create_access_token(user_id=1, username="admin", role="admin")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {
                "id": 1,
                "username": "admin",
                "email": "admin@example.com",
                "role": "admin",
                "is_active": 1,
                "must_change_password": 0,
                "created_at": "2026-10-01 00:00:00",
                "updated_at": "2026-10-01 00:00:00",
            },
            {
                "id": 2,
                "username": "trader",
                "email": None,
                "role": "trader",
                "is_active": 1,
                "must_change_password": 1,
                "created_at": "2026-10-02 00:00:00",
                "updated_at": "2026-10-02 00:00:00",
            },
        ]
        mock_conn.cursor.return_value = mock_cur

        with patch("web.routes.auth_routes.get_pool") as mock_get_pool:
            mock_get_pool.return_value.get_connection.return_value = mock_conn

            res = self.client.get(
                "/api/users",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            self.assertEqual(res.status_code, 200)
            users = res.json()["users"]
            self.assertEqual(len(users), 2)
            self.assertIn("must_change_password", users[0])
            self.assertIs(users[0]["must_change_password"], False)
            self.assertIn("must_change_password", users[1])
            self.assertIs(users[1]["must_change_password"], True)

    # ------------------------------------------------------------------------
    # 5. Shared Rate Limiter Health and Degraded State Observability
    # ------------------------------------------------------------------------
    def test_rate_limiter_health_reporting(self):
        health = get_rate_limiter_health()
        self.assertIn("durable_enabled", health)
        self.assertIn("degraded", health)
        self.assertIn("mode", health)

    def test_login_returns_503_when_shared_rate_limiter_is_unavailable(self):
        with patch(
            "web.routes.auth_routes.admit_login_attempt",
            side_effect=RateLimitBackendUnavailable("database unavailable"),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "trader", "password": "not-the-password"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Login protection is temporarily unavailable. Please retry shortly.",
        )

    def test_login_returns_503_when_failed_attempt_cannot_be_persisted(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 22, "username": "trader", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 0, None)), \
             patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes.verify_password", return_value=False), \
             patch("web.routes.auth_routes.record_failed_login", side_effect=RateLimitBackendUnavailable("database unavailable")):
            mock_pool.return_value.get_connection.return_value = mock_conn
            response = self.client.post(
                "/api/auth/login",
                json={"username": "trader", "password": "wrong-password"},
            )
        self.assertEqual(response.status_code, 503)
        mock_conn.close.assert_called_once()

    def test_login_does_not_issue_token_when_counter_reset_fails(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 22, "username": "trader", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 0, False)), \
             patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes.verify_password", return_value=True), \
             patch("web.routes.auth_routes.reset_login_attempts", side_effect=RateLimitBackendUnavailable("database unavailable")):
            mock_pool.return_value.get_connection.return_value = mock_conn
            response = self.client.post(
                "/api/auth/login",
                json={"username": "trader", "password": "correct-password"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("token", response.json())
        mock_conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
