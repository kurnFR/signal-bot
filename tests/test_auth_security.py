import time
import unittest
from unittest.mock import patch, MagicMock

from web.auth import (
    create_access_token, verify_access_token,
    check_login_rate_limit, record_failed_login, reset_login_attempts,
    revoke_token, is_token_revoked
)


class TestAuthSecurity(unittest.TestCase):
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
