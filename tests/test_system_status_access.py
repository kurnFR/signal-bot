import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app

FULL = {"database": {"status": "ok", "host": "10.1.2.3", "port": 3306, "database": "secretdb", "user": "u",
                     "tables": {"ohlcv": 5}, "error": "Access denied for user u@10.1.2.3"},
        "heartbeats": [{"collector": "c", "status": "ok", "detail": "", "lastBeat": "now"}],
        "binance": {"spotApi": "online", "futuresApi": "online", "enableFuturesConfig": True}}


def user(role):
    return {"id": 1, "username": "u", "email": None, "role": role, "is_active": 1, "must_change_password": 0}


class TestSystemStatusAccess(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _get(self, path, role):
        with patch("web.app.authenticate_request", return_value=user(role)), \
             patch("web.auth.authenticate_request", return_value=user(role)), \
             patch("web.routes.system_status.collect_system_status", return_value=FULL), \
             patch("web.routes.settings.collect_system_status", return_value=FULL):
            return self.client.get(path)

    def test_every_role_can_read_public_status_without_connection_details(self):
        for role in ("viewer", "trader", "admin"):
            res = self._get("/api/system/status", role)
            self.assertEqual(res.status_code, 200, role)
            body = res.json()
            self.assertEqual(body["database"]["status"], "ok")
            text = res.text
            for secret in ("10.1.2.3", "secretdb", "3306", "Access denied"):
                self.assertNotIn(secret, text)
            self.assertEqual(len(body["heartbeats"]), 1)

    def test_error_text_is_generic_when_database_is_down(self):
        down = dict(FULL, database=dict(FULL["database"], status="error"))
        with patch("web.app.authenticate_request", return_value=user("viewer")), \
             patch("web.auth.authenticate_request", return_value=user("viewer")), \
             patch("web.routes.system_status.collect_system_status", return_value=down):
            body = self.client.get("/api/system/status").json()
        self.assertEqual(body["database"]["error"], "Database unavailable")

    def test_detailed_status_stays_admin_only(self):
        self.assertEqual(self._get("/api/settings/status", "viewer").status_code, 403)
        self.assertEqual(self._get("/api/settings/status", "trader").status_code, 403)
        self.assertEqual(self._get("/api/settings/status", "admin").status_code, 200)

    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get("/api/system/status").status_code, 401)


if __name__ == "__main__":
    unittest.main()
