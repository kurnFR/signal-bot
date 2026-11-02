"""Bound authentication input sizes before database and password-hash work."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from web.app import app
from web.routes.auth_routes import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    ResetPasswordRequest,
)


MAX_USERNAME_LENGTH = 30
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 1024


class TestAuthRequestSizeLimits(unittest.TestCase):
    def test_login_username_is_bounded(self):
        LoginRequest(username="u" * MAX_USERNAME_LENGTH, password="password")
        with self.assertRaises(ValidationError):
            LoginRequest(username="u" * (MAX_USERNAME_LENGTH + 1), password="password")

    def test_login_password_is_bounded(self):
        LoginRequest(username="trader", password="p" * MAX_PASSWORD_LENGTH)
        with self.assertRaises(ValidationError):
            LoginRequest(username="trader", password="p" * (MAX_PASSWORD_LENGTH + 1))

    def test_password_change_bounds_both_password_fields(self):
        ChangePasswordRequest(old_password="o" * MAX_PASSWORD_LENGTH, new_password="N" * MAX_PASSWORD_LENGTH)
        with self.assertRaises(ValidationError):
            ChangePasswordRequest(old_password="o" * (MAX_PASSWORD_LENGTH + 1), new_password="Strong-New-Pass!")
        with self.assertRaises(ValidationError):
            ChangePasswordRequest(old_password="old-password", new_password="N" * (MAX_PASSWORD_LENGTH + 1))

    def test_user_creation_bounds_username_email_and_password(self):
        CreateUserRequest(username="u" * MAX_USERNAME_LENGTH, email="e" * MAX_EMAIL_LENGTH, password="P" * 10)
        with self.assertRaises(ValidationError):
            CreateUserRequest(username="u" * (MAX_USERNAME_LENGTH + 1), password="Strong-New-Pass!")
        with self.assertRaises(ValidationError):
            CreateUserRequest(username="trader", email="e" * (MAX_EMAIL_LENGTH + 1), password="Strong-New-Pass!")
        with self.assertRaises(ValidationError):
            CreateUserRequest(username="trader", password="P" * (MAX_PASSWORD_LENGTH + 1))

    def test_admin_password_reset_bounds_new_password(self):
        ResetPasswordRequest(new_password="P" * MAX_PASSWORD_LENGTH)
        with self.assertRaises(ValidationError):
            ResetPasswordRequest(new_password="P" * (MAX_PASSWORD_LENGTH + 1))

    def test_oversized_login_is_rejected_before_database_access(self):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("web.routes.auth_routes._get_required_connection") as get_connection:
            response = client.post(
                "/api/auth/login",
                json={"username": "trader", "password": "p" * (MAX_PASSWORD_LENGTH + 1)},
            )
        self.assertEqual(response.status_code, 422)
        get_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
