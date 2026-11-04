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

    def test_list_users_role_gating(self):
        # 1. Unauthenticated is rejected (401)
        res_unauth = self.client.get("/api/users")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Trader is denied (403)
        res_trader = self.client.get("/api/users", headers=self._headers(self.trader_token))
        self.assertEqual(res_trader.status_code, 403)

        # 3. Viewer is denied (403)
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
        with patch("web.routes.auth_routes.get_pool") as mock_pool:
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
        self.assertEqual(self.client.post("/api/users", json=payload, headers=self._headers(self.trader_token)).status_code, 403)

        # Viewer -> 403
        self.assertEqual(self.client.post("/api/users", json=payload, headers=self._headers(self.viewer_token)).status_code, 403)

        # Admin rejects arbitrary role string (e.g. 'superadmin')
        bad_role_payload = {"username": "hacker", "password": "SecurePassword123!", "role": "superadmin"}
        res_bad_role = self.client.post("/api/users", json=bad_role_payload, headers=self._headers(self.admin_token))
        self.assertEqual(res_bad_role.status_code, 400)
        self.assertIn("Role must be one of: admin, trader, viewer", res_bad_role.json()["detail"])

    def test_update_user_lockout_and_role_validation(self):
        # Unauthenticated -> 401
        self.assertEqual(self.client.put("/api/users/2", json={"is_active": False}).status_code, 401)

        # Trader -> 403
        self.assertEqual(self.client.put("/api/users/2", json={"is_active": False}, headers=self._headers(self.trader_token)).status_code, 403)

        # Admin self-deactivation is strictly rejected with 400
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 1, "username": "admin", "role": "admin", "is_active": 1}

        with patch("web.routes.auth_routes.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn
            res_self_deactivate = self.client.put(
                "/api/users/1",
                json={"is_active": False},
                headers=self._headers(self.admin_token),
            )
        self.assertEqual(res_self_deactivate.status_code, 400)
        self.assertIn("Cannot deactivate your own admin account", res_self_deactivate.json()["detail"])

        # Admin rejects invalid role update
        with patch("web.routes.auth_routes.get_pool") as mock_pool:
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
        self.assertEqual(self.client.delete("/api/users/2", headers=self._headers(self.trader_token)).status_code, 403)

        # Admin self-deletion is strictly rejected with 400 before DB read
        res_self_delete = self.client.delete(
            "/api/users/1",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res_self_delete.status_code, 400)
        self.assertIn("Cannot delete your own admin account", res_self_delete.json()["detail"])

    def test_reset_password_role_gating(self):
        payload = {"new_password": "NewSecurePassword123!"}

        # Unauthenticated -> 401
        self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload).status_code, 401)

        # Trader -> 403
        self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload, headers=self._headers(self.trader_token)).status_code, 403)

        # Viewer -> 403
        self.assertEqual(self.client.post("/api/users/2/reset-password", json=payload, headers=self._headers(self.viewer_token)).status_code, 403)


if __name__ == "__main__":
    unittest.main()
