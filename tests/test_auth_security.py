import time
import unittest
from unittest.mock import patch, MagicMock

from web.auth import (
    create_access_token, verify_access_token,
    check_login_rate_limit, record_failed_login, reset_login_attempts,
    revoke_token, is_token_revoked
)


class TestAuthSecurity(unittest.TestCase):
    def test_secret_file_is_created_private_and_reused(self):
        import os
        import stat
        import tempfile
        from web.auth import _load_or_create_secret

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, ".app_secret")
            first = _load_or_create_secret(path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            second = _load_or_create_secret(path)

            self.assertEqual(mode, 0o600)
            self.assertEqual(len(first), 64)
            self.assertEqual(second, first)

    def test_existing_secret_permissions_are_restricted(self):
        import os
        import stat
        import tempfile
        from web.auth import _load_or_create_secret

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, ".app_secret")
            with open(path, "w", encoding="utf-8") as secret_file:
                secret_file.write("existing-secret")
            os.chmod(path, 0o644)

            loaded = _load_or_create_secret(path)

            self.assertEqual(loaded, "existing-secret")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_login_rate_limiting(self):
        key = "test_ip:test_user"
        reset_login_attempts(key)

        # 4 failed attempts should still be permitted
        for _ in range(4):
            record_failed_login(key)
            self.assertTrue(check_login_rate_limit(key))

        # 5th attempt hits limit
        record_failed_login(key)
        self.assertFalse(check_login_rate_limit(key))

        # Reset clears lockout
        reset_login_attempts(key)
        self.assertTrue(check_login_rate_limit(key))

    @patch("web.auth.get_pool")
    def test_token_revocation_lifecycle(self, mock_get_pool):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_get_pool.return_value.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur

        token = create_access_token(user_id=42, username="trader1", role="trader")
        self.assertIsNotNone(verify_access_token(token))

        # Revoke token
        revoke_token(token)
        self.assertTrue(is_token_revoked(token))

        # verify_access_token must reject the revoked token
        payload = verify_access_token(token)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
