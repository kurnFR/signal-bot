import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestResearchRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = create_access_token(user_id=1, username="admin", role="admin")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_unauthorized_access(self):
        """Endpoints under /api/research must require authentication."""
        endpoints = [
            "/api/research/retailbot2/positions",
            "/api/research/retailbot2/trades",
            "/api/research/retailbot2/stats",
            "/api/research/screener/status",
            "/api/research/screener/signals",
            "/api/research/screener/stats",
            "/api/research/retailbot2/control",
            "/api/research/screener/control",
        ]
        for ep in endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 401, f"Endpoint {ep} should require auth")

    def test_retailbot2_positions_endpoint(self):
        res = self.client.get("/api/research/retailbot2/positions", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("positions", data)
        self.assertIsInstance(data["positions"], list)

    def test_retailbot2_trades_endpoint(self):
        res = self.client.get("/api/research/retailbot2/trades", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("trades", data)
        self.assertIsInstance(data["trades"], list)

    def test_retailbot2_stats_endpoint(self):
        res = self.client.get("/api/research/retailbot2/stats", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("by_strategy", data)
        self.assertIn("by_mode", data)
        self.assertIsInstance(data["by_strategy"], list)
        self.assertIn("inverse", data["by_mode"])
        self.assertIn("shadow", data["by_mode"])

    def test_screener_signals_endpoint(self):
        res = self.client.get("/api/research/screener/signals", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("signals", data)
        self.assertIsInstance(data["signals"], list)

    def test_screener_stats_endpoint(self):
        res = self.client.get("/api/research/screener/stats", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total_signals", data)
        self.assertIn("signals_last_24h", data)
        self.assertIn("by_type_last_24h", data)

    def test_retailbot2_control_get_and_set(self):
        # GET
        res = self.client.get("/api/research/retailbot2/control", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("enabled", data)
        self.assertIn("tunable_fields", data)

        # POST valid override
        post_res = self.client.post(
            "/api/research/retailbot2/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"rr_ratio": 2.5}}
        )
        self.assertEqual(post_res.status_code, 200)

        # POST invalid override (credentials/unknown key should be rejected with 400)
        bad_res = self.client.post(
            "/api/research/retailbot2/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"db_password": 123.0}}
        )
        self.assertEqual(bad_res.status_code, 400)

    def test_screener_control_get_and_set(self):
        # GET
        res = self.client.get("/api/research/screener/control", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("enabled", data)
        self.assertIn("tunable_fields", data)

        # POST valid override
        post_res = self.client.post(
            "/api/research/screener/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"rvol_multiplier": 8.0}}
        )
        self.assertEqual(post_res.status_code, 200)

        # POST invalid override (unknown key should be rejected with 400)
        bad_res = self.client.post(
            "/api/research/screener/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"non_existent_setting": 123}}
        )
        self.assertEqual(bad_res.status_code, 400)

    @patch("web.routes.research_routes.get_screener_control")
    @patch("web.routes.research_routes.get_screener_status")
    def test_screener_status_endpoint(self, mock_status, mock_control):
        mock_control.return_value = {"enabled": True, "overrides": {}, "updated_at": None}
        mock_status.return_value = {
            "running": True,
            "database_ok": True,
            "pid": 1234,
            "timeframe": "5m",
            "symbols_monitored": 247,
            "last_beat_at": "2026-10-06 03:30:00",
            "last_signal_at": "2026-10-06 03:29:00",
            "last_universe_refresh": "2026-10-06 03:00:00",
        }
        res = self.client.get("/api/research/screener/status", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["running"])
        self.assertTrue(data["enabled"])
        self.assertEqual(data["symbols_monitored"], 247)
        self.assertEqual(data["timeframe"], "5m")

    def test_screener_control_accepts_boolean_overrides(self):
        res = self.client.post(
            "/api/research/screener/control",
            headers=self.headers,
            json={
                "enabled": True,
                "overrides": {
                    "enable_velocity_detection": True,
                    "require_confluence": False,
                    "min_quality_score": 70,
                },
            },
        )
        self.assertEqual(res.status_code, 200)

    @patch("web.routes.research_routes.get_retailbot2_stats")
    def test_retailbot2_stats_error_handling(self, mock_stats):
        mock_stats.side_effect = Exception("DB query timeout")
        res = self.client.get("/api/research/retailbot2/stats", headers=self.headers)
        self.assertEqual(res.status_code, 500)
        self.assertIn("Failed to load retailbot2 stats", res.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
