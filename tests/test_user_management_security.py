"""User management backend security and RBAC contract tests.

Validates that /api/users endpoints enforce strict role gating, reject arbitrary
roles, prevent self-lockout/self-deletion, and write atomic audit entries.
"""
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestUserManagementSecurity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.admin_token = create_access_token(user_id=1, username="admin", role="admin")
        self.trader_token = create_access_token(user_id=2, username="trader_joe", role="trader")
        self.viewer_token = create_access_token(user_id=3, username="viewer_bob", role="viewer")

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _auth_user(self, user_id: int, username: str, role: str):
        return {
            "id": user_id, "username": username, "email": f"{username}@test.com",
            "role": role, "is_active": 1, "must_change_password": 0, "session_version": 0,
        }

    def test_list_users_role_gating(self):
        # 1. Unauthenticated is rejected (401)
        res_unauth = self.client.get("/api/users")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Trader is denied (403)
        with patch("web.app.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")):
            res_trader = self.client.get("/api/users", headers=self._headers(self.trader_token))
        self.assertEqual(res_trader.status_code, 403)

        # 3. Viewer is denied (403)
        with patch("web.app.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")):
            res_viewer = self.client.get("/api/users", headers=self._headers(self.viewer_token))
        self.assertEqual(res_viewer.status_code, 403)

        # 4. Admin is permitted (200)
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            {
                "id": 1, "username": "admin", "email": "admin@domain.com",
                "role": "admin", "is_active": 1, "must_change_password": 0,
                "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
            }
        ]
        with patch("web.app.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_admin = self.client.get("/api/users", headers=self._headers(self.admin_token))

        self.assertEqual(res_admin.status_code, 200)
        users = res_admin.json()["users"]
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "admin")

    def test_create_user_role_gating_and_validation(self):
        payload = {"username": "newtrader", "email": "t@test.com", "password": "SecurePassword123!", "role": "trader"}

        # Unauthenticated -> 401
        self.assertEqual(self.client.post("/api/users", json=payload).status_code, 401)

        # Trader -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")):
            self.assertEqual(self.client.post("/api/users", json=payload, headers=self._headers(self.trader_token)).status_code, 403)

        # Viewer -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")):
            self.assertEqual(self.client.post("/api/users", json=payload, headers=self._headers(self.viewer_token)).status_code, 403)

        # Admin rejects arbitrary role string (e.g. 'superadmin')
        bad_role_payload = {"username": "hacker", "password": "SecurePassword123!", "role": "superadmin"}
        with patch("web.app.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(1, "admin", "admin")):
            res_bad_role = self.client.post("/api/users", json=bad_role_payload, headers=self._headers(self.admin_token))
        self.assertEqual(res_bad_role.status_code, 400)
        self.assertIn("Role must be one of: admin, trader, viewer", res_bad_role.json()["detail"])

    def test_update_user_lockout_and_role_validation(self):
        # Unauthenticated -> 401
        self.assertEqual(self.client.put("/api/users/2", json={"is_active": False}).status_code, 401)

        # Trader -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")):
            self.assertEqual(self.client.put("/api/users/2", json={"is_active": False}, headers=self._headers(self.trader_token)).status_code, 403)

        # Admin self-deactivation is strictly rejected with 400
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 1, "username": "admin", "role": "admin", "is_active": 1}

        with patch("web.app.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_self_deactivate = self.client.put(
                "/api/users/1",
                json={"is_active": False},
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_self_deactivate.status_code, 400)
        self.assertIn("Cannot deactivate your own admin account", res_self_deactivate.json()["detail"])

        # Admin rejects invalid role update
        mock_cur.fetchone.return_value = {"id": 2, "username": "user2", "role": "trader", "is_active": 1}
        with patch("web.app.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_bad_role = self.client.put(
                "/api/users/2",
                json={"role": "root"},
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_bad_role.status_code, 400)
        self.assertIn("Invalid role", res_bad_role.json()["detail"])

    def test_delete_user_lockout_protection(self):
        # Unauthenticated -> 401
        self.assertEqual(self.client.delete("/api/users/2").status_code, 401)

        # Trader -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")):
            self.assertEqual(self.client.delete("/api/users/2", headers=self._headers(self.trader_token)).status_code, 403)

        # Admin self-deletion is strictly rejected with 400 before DB read
        with patch("web.app.authenticate_request", return_value=self._auth_user(1, "admin", "admin")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(1, "admin", "admin")):
            res_self_delete = self.client.delete(
                "/api/users/1",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        self.assertEqual(res_self_delete.status_code, 400)
        self.assertIn("Cannot delete your own admin account", res_self_delete.json()["detail"])

    def test_admin_self_demotion_rejected_and_non_mutating(self):
        # Admin attempting to change their own role to trader or viewer is rejected with 400
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 1, "username": "admin", "role": "admin", "is_active": 1}

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_demote_trader = self.client.put(
                "/api/users/1",
                json={"role": "trader"},
                headers=self._headers(self.admin_token),
            )
            res_demote_viewer = self.client.put(
                "/api/users/1",
                json={"role": "viewer"},
                headers=self._headers(self.admin_token),
            )

        self.assertEqual(res_demote_trader.status_code, 400)
        self.assertIn("Cannot change your own role from admin", res_demote_trader.json()["detail"])
        self.assertEqual(res_demote_viewer.status_code, 400)
        self.assertIn("Cannot change your own role from admin", res_demote_viewer.json()["detail"])

        # Verify no UPDATE executed and no commit performed
        for call_args in mock_cur.execute.call_args_list:
            sql = call_args[0][0]
            self.assertFalse(sql.strip().upper().startswith("UPDATE"), f"Unexpected UPDATE executed: {sql}")
        mock_conn.commit.assert_not_called()

    def test_last_admin_demotion_and_deactivation_rejected(self):
        # Attempting to demote or deactivate another admin when no other active admin exists is rejected
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        # Target user 2 is an active admin; active admin list returned by ORDER BY id FOR UPDATE has ONLY user 2
        mock_cur.fetchone.return_value = {"id": 2, "username": "admin2", "role": "admin", "is_active": 1}
        mock_cur.fetchall.return_value = [{"id": 2, "username": "admin2"}]

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            # 1. Deactivation rejected
            res_deact = self.client.put(
                "/api/users/2",
                json={"is_active": False},
                headers=self._headers(self.admin_token),
            )
            # 2. Demotion rejected
            res_demote = self.client.put(
                "/api/users/2",
                json={"role": "trader"},
                headers=self._headers(self.admin_token),
            )

        self.assertEqual(res_deact.status_code, 400)
        self.assertIn("Cannot demote or deactivate the last active administrator", res_deact.json()["detail"])
        self.assertEqual(res_demote.status_code, 400)
        self.assertIn("Cannot demote or deactivate the last active administrator", res_demote.json()["detail"])

        for call_args in mock_cur.execute.call_args_list:
            sql = call_args[0][0]
            self.assertFalse(sql.strip().upper().startswith("UPDATE"), f"Unexpected UPDATE executed: {sql}")
        mock_conn.commit.assert_not_called()

    def test_last_admin_deletion_rejected(self):
        # Attempting to delete another admin when no other active admin exists is rejected with 400
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        # Target user 2 is active admin; active admin list returned by ORDER BY id FOR UPDATE has only user 2
        mock_cur.fetchone.return_value = {"username": "admin2", "role": "admin", "is_active": 1}
        mock_cur.fetchall.return_value = [{"id": 2, "username": "admin2"}]

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_del = self.client.delete(
                "/api/users/2",
                headers=self._headers(self.admin_token),
            )

        self.assertEqual(res_del.status_code, 400)
        self.assertIn("Cannot delete the last active administrator", res_del.json()["detail"])

        for call_args in mock_cur.execute.call_args_list:
            sql = call_args[0][0]
            self.assertFalse(sql.strip().upper().startswith("DELETE"), f"Unexpected DELETE executed: {sql}")
        mock_conn.commit.assert_not_called()

    def test_multiple_admin_operations_succeed(self):
        # When other active admins exist (len(other_active_admins) > 0), demoting, deactivating, and deleting an admin succeeds
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # Active admins list has user 1 and user 2
        mock_cur.fetchall.return_value = [
            {"id": 1, "username": "admin"},
            {"id": 2, "username": "admin2"},
        ]

        # 1. Update (demote) when another active admin exists
        mock_cur.fetchone.return_value = {"id": 2, "username": "admin2", "role": "admin", "is_active": 1}
        with patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes._commit_with_audit") as mock_audit:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_demote = self.client.put(
                "/api/users/2",
                json={"role": "trader"},
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_demote.status_code, 200)
        self.assertIn("User 'admin2' updated", res_demote.json()["message"])
        mock_audit.assert_called_once()

        # 2. Deactivate when another active admin exists
        mock_audit.reset_mock()
        mock_cur.fetchone.return_value = {"id": 2, "username": "admin2", "role": "admin", "is_active": 1}
        with patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes._commit_with_audit") as mock_audit:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_deact = self.client.put(
                "/api/users/2",
                json={"is_active": False},
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_deact.status_code, 200)
        self.assertIn("User 'admin2' updated", res_deact.json()["message"])
        mock_audit.assert_called_once()

        # 3. Delete when another active admin exists
        mock_audit.reset_mock()
        mock_cur.fetchone.return_value = {"username": "admin2", "role": "admin", "is_active": 1}
        with patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes._commit_with_audit") as mock_audit:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_delete = self.client.delete(
                "/api/users/2",
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_delete.status_code, 200)
        self.assertIn("User 'admin2' deleted", res_delete.json()["message"])
        mock_audit.assert_called_once()

    def test_reset_password_updates_hash_must_change_and_session_version(self):
        from web.auth import hash_password, verify_password

        old_password = "OldSecurePassword999!"
        old_hash = hash_password(old_password)
        new_password = "NewSecurePassword123!"
        payload = {"new_password": new_password}

        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 2, "username": "trader_joe", "password_hash": old_hash}

        with patch("web.routes.auth_routes.get_pool") as mock_pool, \
             patch("web.routes.auth_routes._commit_with_audit") as mock_audit:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res = self.client.post(
                "/api/users/2/reset-password",
                json=payload,
                headers=self._headers(self.admin_token),
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {
            "status": "success",
            "message": "Password reset successfully for user 'trader_joe'",
        })

        # Verify UPDATE executed with must_change_password=1 and session_version increment
        update_calls = [
            c for c in mock_cur.execute.call_args_list
            if "UPDATE users SET password_hash" in c[0][0]
        ]
        self.assertEqual(len(update_calls), 1)
        update_sql, update_params = update_calls[0][0]
        self.assertIn("must_change_password = 1", update_sql)
        self.assertIn("session_version = session_version + 1", update_sql)
        self.assertEqual(update_params[1], 2)  # target user_id

        # Verify actual password hash changed and verifies against the new password
        actual_new_hash = update_params[0]
        self.assertNotEqual(actual_new_hash, old_hash)
        self.assertTrue(verify_password(new_password, actual_new_hash))
        self.assertFalse(verify_password(old_password, actual_new_hash))

        # Verify audit log recorded
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args[0][2], "USER_PASSWORD_RESET")
        self.assertEqual(mock_audit.call_args[0][3], "admin")
        self.assertEqual(mock_audit.call_args[0][5], {"target_user": "trader_joe", "target_id": 2})

    def test_existing_jwt_becomes_invalid_after_session_version_increment(self):
        # 1. Issue an access token for user 2 with session_version = 0
        old_token = create_access_token(user_id=2, username="trader_joe", role="trader", session_version=0)
        from starlette.datastructures import Headers
        from fastapi import Request, HTTPException
        from web.auth import authenticate_request

        req = MagicMock(spec=Request)
        req.headers = Headers({"Authorization": f"Bearer {old_token}"})

        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # 2. When database reports session_version = 0, authenticate_request succeeds
        mock_cur.fetchone.return_value = {
            "id": 2, "username": "trader_joe", "email": "t@test.com", "role": "trader",
            "is_active": 1, "must_change_password": 0, "session_version": 0,
        }
        with patch("web.auth.get_pool") as mock_pool, \
             patch("web.auth.is_token_revoked", return_value=False):
            mock_pool.return_value.get_connection.return_value = mock_conn
            user_before = authenticate_request(req)
            self.assertEqual(user_before["username"], "trader_joe")

        # 3. When database increments session_version to 1 (e.g. after password reset),
        # the exact same old token (session_version = 0) is rejected with 401
        mock_cur.fetchone.return_value = {
            "id": 2, "username": "trader_joe", "email": "t@test.com", "role": "trader",
            "is_active": 1, "must_change_password": 1, "session_version": 1,
        }
        with patch("web.auth.get_pool") as mock_pool, \
             patch("web.auth.is_token_revoked", return_value=False):
            mock_pool.return_value.get_connection.return_value = mock_conn
            with self.assertRaises(HTTPException) as ctx:
                authenticate_request(req)
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("Session expired or invalid token", ctx.exception.detail)

    def test_reset_password_rejects_policy_violations_without_mutation(self):
        # 1. Pydantic schema rejection for short password (< 10 chars) returns 422
        payload_short = {"new_password": "short"}
        res_short = self.client.post(
            "/api/users/2/reset-password",
            json=payload_short,
            headers=self._headers(self.admin_token),
        )
        self.assertEqual(res_short.status_code, 422)

        # 2. Business rule complexity rejection for trivial / common base password returns 400
        payload_weak = {"new_password": "password1234!"}
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 2, "username": "trader_joe"}

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_weak = self.client.post(
                "/api/users/2/reset-password",
                json=payload_weak,
                headers=self._headers(self.admin_token),
            )

        self.assertEqual(res_weak.status_code, 400)
        self.assertIn("Password is too common or easy to guess", res_weak.json()["detail"])
        mock_conn.commit.assert_not_called()
        for call_args in mock_cur.execute.call_args_list:
            sql = call_args[0][0]
            self.assertFalse(sql.strip().upper().startswith("UPDATE"), f"Unexpected UPDATE executed: {sql}")

    def test_reset_password_role_gating(self):
        payload = {"new_password": "NewSecurePassword123!"}

        # Unauthenticated -> 401
        self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload).status_code, 401)

        # Trader -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(2, "trader_joe", "trader")):
            self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload, headers=self._headers(self.trader_token)).status_code, 403)

        # Viewer -> 403
        with patch("web.app.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")), \
             patch("web.auth.authenticate_request", return_value=self._auth_user(3, "viewer_bob", "viewer")):
            self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload, headers=self._headers(self.viewer_token)).status_code, 403)


if __name__ == "__main__":
    unittest.main()
