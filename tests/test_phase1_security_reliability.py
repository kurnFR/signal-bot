import json
import unittest
import hashlib
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import HTTPException

from web.app import app
from web.routes import auth_routes
from web.auth import (
    create_access_token,
    authenticate_request,
    verify_access_token,
    is_token_revoked,
    revoke_token,
    revoke_token_cursor,
    _revoked_tokens_cache,
    cache_revoked_token,
    _is_cached_revoked_token,
    _decode_token_payload_unverified,
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

    def test_local_revocation_cache_expires_with_signed_token(self):
        token = create_access_token(user_id=10, username="trader_alice", role="trader")
        payload = _decode_token_payload_unverified(token)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        cache_revoked_token(token)
        self.assertTrue(_is_cached_revoked_token(token_hash))

        with patch("web.auth.time.time", return_value=payload["exp"] + 1):
            self.assertFalse(_is_cached_revoked_token(token_hash))

        self.assertNotIn(token_hash, _revoked_tokens_cache)

    def test_authenticate_request_rejects_stale_session_version(self):
        token = create_access_token(
            user_id=10, username="trader_alice", role="trader", session_version=0
        )
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {token}"
        request.cookies.get.return_value = None

        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            "id": 10, "username": "trader_alice", "email": None, "role": "trader",
            "is_active": 1, "must_change_password": 0, "session_version": 1,
        }
        with patch("web.auth.get_pool") as pool, patch("web.auth.is_token_revoked", return_value=False):
            pool.return_value.get_connection.return_value = conn
            with self.assertRaises(HTTPException) as ctx:
                authenticate_request(request)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Session expired", ctx.exception.detail)
        conn.close.assert_called_once()

    def test_authenticate_request_accepts_matching_session_version(self):
        token = create_access_token(
            user_id=10, username="trader_alice", role="trader", session_version=4
        )
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {token}"
        request.cookies.get.return_value = None

        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        user = {
            "id": 10, "username": "trader_alice", "email": None, "role": "trader",
            "is_active": 1, "must_change_password": 0, "session_version": 4,
        }
        cur.fetchone.return_value = user
        with patch("web.auth.get_pool") as pool, patch("web.auth.is_token_revoked", return_value=False):
            pool.return_value.get_connection.return_value = conn
            self.assertEqual(authenticate_request(request), user)

    def test_revocation_insert_prunes_expired_rows_in_bounded_batch(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [(1,), (1,)]
        token = create_access_token(user_id=10, username="trader_alice", role="trader")

        token_hash = revoke_token_cursor(cur, token)

        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any("DELETE FROM revoked_tokens" in sql for sql in sql_calls))
        delete_call = next(
            call for call in cur.execute.call_args_list
            if "DELETE FROM revoked_tokens" in call.args[0]
        )
        self.assertIn("LIMIT %s", delete_call.args[0])
        self.assertEqual(delete_call.args[1], (1000,))
        self.assertTrue(any("RELEASE_LOCK" in sql for sql in sql_calls))
        self.assertEqual(len(token_hash), 64)

    def test_revocation_cleanup_skips_when_another_worker_holds_lock(self):
        cur = MagicMock()
        cur.fetchone.return_value = (0,)
        token = create_access_token(user_id=10, username="trader_alice", role="trader")

        revoke_token_cursor(cur, token)

        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertFalse(any("DELETE FROM revoked_tokens" in sql for sql in sql_calls))
        self.assertFalse(any("RELEASE_LOCK" in sql for sql in sql_calls))

    def test_revoke_token_raises_when_persistence_fails(self):
        token = create_access_token(user_id=10, username="trader_alice", role="trader")
        with patch("web.auth.get_pool", side_effect=RuntimeError("Disk write failed")):
            with self.assertRaises(RuntimeError) as ctx:
                revoke_token(token)
            self.assertIn("Failed to persist revoked token to database", str(ctx.exception))

    def _logout_request(self, token):
        from starlette.requests import Request
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/logout",
            "raw_path": b"/api/auth/logout",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        })

    def test_logout_returns_503_when_audit_database_connection_is_unavailable(self):
        token = create_access_token(user_id=1, username="admin", role="admin")
        with patch("web.routes.auth_routes.get_pool", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.logout(
                    self._logout_request(token),
                    {"id": 1, "username": "admin", "role": "admin"},
                )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_logout_returns_503_when_revocation_cannot_be_persisted(self):
        token = create_access_token(user_id=1, username="admin", role="admin")
        mock_conn = MagicMock()
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.revoke_token_cursor", side_effect=RuntimeError("DB cluster split-brain")):
            pool.return_value.get_connection.return_value = mock_conn
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.logout(
                    self._logout_request(token),
                    {"id": 1, "username": "admin", "role": "admin"},
                )
        self.assertEqual(ctx.exception.status_code, 503)
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

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

    def test_logout_rolls_back_token_revocation_when_audit_fails(self):
        token = create_access_token(user_id=1, username="admin", role="admin")
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.revoke_token_cursor", return_value="revoked-hash"), \
             patch("web.routes.auth_routes.log_audit_cursor", side_effect=RuntimeError("audit unavailable")):
            pool.return_value.get_connection.return_value = mock_conn
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.logout(
                    self._logout_request(token),
                    {"id": 1, "username": "admin", "role": "admin"},
                )
        self.assertEqual(ctx.exception.status_code, 503)
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
        self.assertNotIn("revoked-hash", _revoked_tokens_cache)

    def test_logout_commits_revocation_and_audit_before_caching(self):
        token = create_access_token(user_id=1, username="admin", role="admin")
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        app.dependency_overrides[auth_routes.get_current_user] = lambda: {
            "id": 1, "username": "admin", "role": "admin"
        }
        try:
            with patch("web.routes.auth_routes.get_pool") as pool, \
                 patch("web.routes.auth_routes.revoke_token_cursor", return_value="revoked-hash") as revoke, \
                 patch("web.routes.auth_routes.cache_revoked_token") as cache:
                cache.side_effect = lambda _token: self.assertTrue(mock_conn.commit.called)
                pool.return_value.get_connection.return_value = mock_conn
                response = self.client.post(
                    "/api/auth/logout", headers={"Authorization": f"Bearer {token}"}
                )
        finally:
            app.dependency_overrides.pop(auth_routes.get_current_user, None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_conn.commit.assert_called_once()
        revoke.assert_called_once_with(mock_cur, token)
        cache.assert_called_once_with(token)
        self.assertTrue(any(
            "INSERT INTO audit_logs" in call.args[0]
            for call in mock_cur.execute.call_args_list
        ))

    def _assert_audit_failure_rolls_back(self, invoke, mock_conn):
        with patch("web.routes.auth_routes.log_audit_cursor", side_effect=RuntimeError("audit store unavailable")):
            with self.assertRaises(HTTPException) as ctx:
                invoke()
        self.assertEqual(ctx.exception.status_code, 503)
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_password_change_rotates_session_and_returns_replacement_token(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {"password_hash": "old-hash"}
        request = MagicMock()
        request.client.host = "127.0.0.1"
        current_user = {
            "id": 7, "username": "trader", "role": "trader", "session_version": 4,
        }
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.verify_password", side_effect=[True, False]), \
             patch("web.routes.auth_routes._validate_new_password"), \
             patch("web.routes.auth_routes.hash_password", return_value="new-hash"):
            pool.return_value.get_connection.return_value = conn
            response = auth_routes.change_password(
                auth_routes.ChangePasswordRequest(
                    old_password="old", new_password="Strong-New-Pass!"
                ),
                request,
                current_user,
            )

        update_sql = next(
            call.args[0] for call in cur.execute.call_args_list
            if "UPDATE users SET password_hash" in call.args[0]
        )
        self.assertIn("session_version = session_version + 1", update_sql)
        self.assertEqual(_decode_token_payload_unverified(response["token"])["session_version"], 5)
        conn.commit.assert_called_once()

    def test_password_change_rolls_back_when_audit_insert_fails(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {"password_hash": "old-hash"}
        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.verify_password", side_effect=[True, False]), \
             patch("web.routes.auth_routes._validate_new_password"), \
             patch("web.routes.auth_routes.hash_password", return_value="new-hash"):
            pool.return_value.get_connection.return_value = conn
            self._assert_audit_failure_rolls_back(
                lambda: auth_routes.change_password(
                    auth_routes.ChangePasswordRequest(old_password="old", new_password="Strong-New-Pass!"),
                    request, {"id": 7, "username": "trader", "role": "trader", "session_version": 0},
                ), conn,
            )

    def test_user_create_reset_update_and_delete_roll_back_when_audit_fails(self):
        admin = {"id": 1, "username": "admin"}
        cases = [
            ("create", lambda: auth_routes.create_user(
                auth_routes.CreateUserRequest(username="new_trader", password="Strong-New-Pass!", role="trader"), admin
            ), {"fetchone": None, "lastrowid": 77}),
            ("reset", lambda: auth_routes.reset_user_password(
                8, auth_routes.ResetPasswordRequest(new_password="Another-Strong-Pass!"), admin
            ), {"fetchone": {"id": 8, "username": "target"}}),
            ("update", lambda: auth_routes.update_user(
                8, auth_routes.UpdateUserRequest(role="viewer"), admin
            ), {"fetchone": {"id": 8, "username": "target", "role": "trader", "is_active": 1}}),
            ("delete", lambda: auth_routes.delete_user(8, admin), {"fetchone": {"username": "target"}}),
        ]
        for name, invoke, setup in cases:
            with self.subTest(action=name):
                conn, cur = MagicMock(), MagicMock()
                conn.cursor.return_value = cur
                cur.fetchone.return_value = setup.get("fetchone")
                if "lastrowid" in setup:
                    cur.lastrowid = setup["lastrowid"]
                with patch("web.routes.auth_routes.get_pool") as pool, \
                     patch("web.routes.auth_routes._validate_new_password"), \
                     patch("web.routes.auth_routes.hash_password", return_value="hashed"):
                    pool.return_value.get_connection.return_value = conn
                    self._assert_audit_failure_rolls_back(invoke, conn)

    def test_security_mutation_returns_503_when_db_connection_cannot_be_acquired(self):
        admin = {"id": 1, "username": "admin"}
        with patch("web.routes.auth_routes.get_pool", side_effect=RuntimeError("database unavailable")):
            with patch("web.routes.auth_routes._validate_new_password"):
                with patch("web.routes.auth_routes.hash_password", return_value="hashed"):
                    with self.assertRaises(HTTPException) as ctx:
                        auth_routes.create_user(
                            auth_routes.CreateUserRequest(
                                username="new_trader", password="Strong-New-Pass!", role="trader"
                            ),
                            admin,
                        )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(
            ctx.exception.detail,
            "Security audit storage is temporarily unavailable. Please retry shortly.",
        )

    def test_login_does_not_issue_token_when_success_audit_fails(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            "id": 9, "username": "trader", "email": None, "password_hash": "hash",
            "role": "trader", "is_active": 1, "must_change_password": 0,
        }
        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 101)), \
             patch("web.routes.auth_routes.finalize_login_attempt"), \
             patch("web.routes.auth_routes.verify_password", return_value=True), \
             patch("web.routes.auth_routes.log_audit", side_effect=RuntimeError("audit store unavailable")):
            pool.return_value.get_connection.return_value = conn
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.login(auth_routes.LoginRequest(username="trader", password="correct"), request)
        self.assertEqual(ctx.exception.status_code, 503)
        conn.close.assert_called_once()

    def test_unknown_and_inactive_login_return_503_when_required_audit_fails(self):
        request = MagicMock()
        request.client.host = "127.0.0.1"
        cases = [
            ("unknown", None),
            ("inactive", {
                "id": 10, "username": "disabled", "email": None,
                "password_hash": "hash", "role": "trader", "is_active": 0,
                "must_change_password": 0,
            }),
        ]
        for label, user in cases:
            with self.subTest(account_state=label):
                conn, cur = MagicMock(), MagicMock()
                conn.cursor.return_value = cur
                cur.fetchone.return_value = user
                with patch("web.routes.auth_routes.get_pool") as pool:
                    with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 101)):
                        with patch("web.routes.auth_routes.finalize_login_attempt", return_value=1):
                            with patch("web.routes.auth_routes.log_audit", side_effect=RuntimeError("audit store unavailable")) as audit_log:
                                pool.return_value.get_connection.return_value = conn
                                with self.assertRaises(HTTPException) as ctx:
                                    auth_routes.login(
                                        auth_routes.LoginRequest(
                                            username="missing" if user is None else "disabled",
                                            password="wrong",
                                        ),
                                        request,
                                    )
                self.assertEqual(ctx.exception.status_code, 503)
                audit_log.assert_called_once()
                self.assertEqual(audit_log.call_args.args[2], user["id"] if user else None)
                conn.close.assert_called_once()

    def test_failed_password_login_returns_503_when_required_audit_fails(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            "id": 9, "username": "trader", "email": None, "password_hash": "hash",
            "role": "trader", "is_active": 1, "must_change_password": 0,
        }
        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 101)), \
             patch("web.routes.auth_routes.finalize_login_attempt", return_value=1), \
             patch("web.routes.auth_routes.verify_password", return_value=False), \
             patch("web.routes.auth_routes.log_audit", side_effect=RuntimeError("audit store unavailable")):
            pool.return_value.get_connection.return_value = conn
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.login(auth_routes.LoginRequest(username="trader", password="wrong"), request)
        self.assertEqual(ctx.exception.status_code, 503)
        conn.close.assert_called_once()

    def test_rate_limited_login_returns_503_when_required_audit_fails(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {"id": 9, "username": "trader", "is_active": 1}
        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.admit_login_attempt", return_value=(False, 5, None)), \
             patch("web.routes.auth_routes.log_audit", side_effect=RuntimeError("audit store unavailable")):
            pool.return_value.get_connection.return_value = conn
            with self.assertRaises(HTTPException) as ctx:
                auth_routes.login(auth_routes.LoginRequest(username="trader", password="bad"), request)
        self.assertEqual(ctx.exception.status_code, 503)
        conn.close.assert_called_once()

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

    def test_login_embeds_database_session_version_in_access_token(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            "id": 10, "username": "trader", "email": None, "password_hash": "hash",
            "role": "trader", "is_active": 1, "must_change_password": 0,
            "session_version": 7,
        }
        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 101)), \
             patch("web.routes.auth_routes.finalize_login_attempt"), \
             patch("web.routes.auth_routes.verify_password", return_value=True), \
             patch("web.routes.auth_routes._write_required_audit"):
            pool.return_value.get_connection.return_value = conn
            response = auth_routes.login(
                auth_routes.LoginRequest(username="trader", password="Strong-Pass!"),
                request,
            )

        payload = _decode_token_payload_unverified(response["token"])
        self.assertEqual(payload["session_version"], 7)
        conn.close.assert_called_once()

    def test_admin_role_update_increments_session_version(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {
            "id": 8, "username": "target", "role": "trader", "is_active": 1,
        }
        with patch("web.routes.auth_routes.get_pool") as pool:
            pool.return_value.get_connection.return_value = conn
            auth_routes.update_user(
                8, auth_routes.UpdateUserRequest(role="viewer"),
                {"id": 1, "username": "admin"},
            )

        update_sql = next(
            call.args[0] for call in cur.execute.call_args_list
            if "UPDATE users SET" in call.args[0]
        )
        self.assertIn("session_version = session_version + 1", update_sql)
        conn.commit.assert_called_once()

    def test_admin_password_reset_increments_session_version(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = {"id": 8, "username": "target"}
        with patch("web.routes.auth_routes.get_pool") as pool, \
             patch("web.routes.auth_routes._validate_new_password"), \
             patch("web.routes.auth_routes.hash_password", return_value="new-hash"):
            pool.return_value.get_connection.return_value = conn
            auth_routes.reset_user_password(
                8,
                auth_routes.ResetPasswordRequest(new_password="Strong-New-Pass!"),
                {"id": 1, "username": "admin"},
            )

        update_sql = next(
            call.args[0] for call in cur.execute.call_args_list
            if "UPDATE users SET password_hash" in call.args[0]
        )
        self.assertIn("session_version = session_version + 1", update_sql)
        conn.commit.assert_called_once()

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

    def test_login_rate_limit_key_is_case_insensitive_like_username_lookup(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur

        events = MagicMock()
        with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 0, None)) as admit:
            with patch("web.routes.auth_routes.get_pool") as mock_pool:
                with patch("web.routes.auth_routes.log_audit"):
                    mock_pool.return_value.get_connection.return_value = mock_conn
                    events.attach_mock(mock_conn.close, "identity_connection_close")
                    events.attach_mock(admit, "rate_limit_admission")
                    response = self.client.post(
                        "/api/auth/login",
                        json={"username": "AdMiN", "password": "wrong-password"},
                    )

        self.assertEqual(response.status_code, 401)
        admit.assert_called_once_with("testclient:unknown:admin")
        mock_conn.close.assert_called_once()
        self.assertEqual(
            events.mock_calls[:2],
            [unittest.mock.call.identity_connection_close(), unittest.mock.call.rate_limit_admission("testclient:unknown:admin")],
        )

    def test_database_collation_equivalent_usernames_share_account_rate_limit(self):
        account = {
            "id": 42, "username": "cafe", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = account
        mock_conn.cursor.return_value = mock_cur

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 101)) as admit:
                with patch("web.routes.auth_routes.verify_password", return_value=False):
                    with patch("web.routes.auth_routes.finalize_login_attempt", return_value=1):
                        for spelling in ("café", "cafe"):
                            response = self.client.post(
                                "/api/auth/login",
                                json={"username": spelling, "password": "wrong-password"},
                            )
                            self.assertEqual(response.status_code, 401)

        self.assertEqual(
            [call.args[0] for call in admit.call_args_list],
            ["testclient:user:42", "testclient:user:42"],
        )

    def test_login_returns_503_when_shared_rate_limiter_is_unavailable(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 22, "username": "trader", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            with patch(
                "web.routes.auth_routes.admit_login_attempt",
                side_effect=RateLimitBackendUnavailable("database unavailable"),
            ):
                response = self.client.post(
                    "/api/auth/login",
                    json={"username": "trader", "password": "not-the-password"},
                )
        mock_conn.close.assert_called_once()
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

    def test_failed_password_finalizes_its_durable_reservation(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 22, "username": "trader", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 22)), \
             patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes.verify_password", return_value=False), \
             patch("web.routes.auth_routes.finalize_login_attempt", return_value=2) as finalize, \
             patch("web.routes.auth_routes.record_failed_login", side_effect=AssertionError("must use reservation")):
            mock_pool.return_value.get_connection.return_value = mock_conn
            response = self.client.post(
                "/api/auth/login",
                json={"username": "trader", "password": "wrong-password"},
            )
        self.assertEqual(response.status_code, 401)
        finalize.assert_called_once_with(
            "testclient:user:22", 22, success=False
        )

    def test_login_does_not_issue_token_when_reservation_finalization_fails(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 22, "username": "trader", "email": None,
            "password_hash": "hash", "role": "trader", "is_active": 1,
            "must_change_password": 0,
        }
        mock_conn.cursor.return_value = mock_cur
        with patch("web.routes.auth_routes.admit_login_attempt", return_value=(True, 1, 22)), \
             patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes.verify_password", return_value=True), \
             patch("web.routes.auth_routes.finalize_login_attempt", side_effect=RateLimitBackendUnavailable("database unavailable")):
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
