import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestResearchRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = create_access_token(user_id=1, username="admin", role="admin")
        self.headers = {"Authorization": f"Bearer {self.token}"}

        # Route tests must not depend on a live MySQL server. The real auth
        # module checks token revocation and the active user in MySQL on every
        # request, which made these endpoint tests block in CI. Keep the
        # middleware boundary in the request path, but stub its DB-backed
        # identity lookup; missing credentials must still be rejected.
        def authenticate_test_request(request):
            if request.headers.get("Authorization") != self.headers["Authorization"]:
                raise HTTPException(status_code=401, detail="Authentication credentials missing")
            return {"id": 1, "username": "admin", "role": "admin", "is_active": 1}

        self.auth_patcher = patch("web.app.authenticate_request", side_effect=authenticate_test_request)
        self.auth_patcher.start()

        # Stub route-level database helpers as well as authentication. These
        # tests assert HTTP contracts; database behavior is covered separately.
        self.route_patches = [
            patch("web.routes.research_routes.get_retailbot2_open_trades", return_value=[]),
            patch("web.routes.research_routes.get_retailbot2_recent_trades", return_value=[]),
            patch("web.routes.research_routes.get_retailbot2_stats", return_value={
                "by_strategy": [], "by_mode": {"inverse": [], "shadow": []}
            }),
            patch("web.routes.research_routes.get_recent_smart_money_signals", return_value=[]),
            patch("web.routes.research_routes.get_smart_money_stats", return_value={
                "total_signals": 0, "signals_last_24h": 0, "by_type_last_24h": []
            }),
            patch("web.routes.research_routes.get_retailbot2_control", return_value={
                "enabled": True, "overrides": {}, "updated_at": None
            }),
            patch("web.routes.research_routes.set_retailbot2_control"),
            patch("web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings", return_value=[]),
            patch("web.routes.research_routes.set_retailbot2_strategy_lifecycle_setting"),
            patch("web.routes.research_routes.get_screener_control", return_value={
                "enabled": True, "overrides": {}, "updated_at": None
            }),
            patch("web.routes.research_routes.set_screener_control"),
            patch("web.routes.research_routes.get_screener_status", return_value={
                "running": True, "database_ok": True, "pid": 1234,
                "timeframe": "5m", "symbols_monitored": 247,
                "last_beat_at": "2026-10-06 03:30:00",
                "last_signal_at": "2026-10-06 03:29:00",
                "last_universe_refresh": "2026-10-06 03:00:00",
            }),
        ]
        for route_patch in self.route_patches:
            route_patch.start()

    def tearDown(self):
        for route_patch in reversed(self.route_patches):
            route_patch.stop()
        self.auth_patcher.stop()

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
            "/api/research/retailbot2/strategies",
            "/api/research/screener/control",
        ]
        for ep in endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 401, f"Endpoint {ep} should require auth")

    def test_strategy_lifecycle_endpoint_defaults_fail_closed_for_telegram(self):
        res = self.client.get("/api/research/retailbot2/strategies", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        strategies = res.json()["strategies"]
        bb_inverse = next(
            row for row in strategies
            if row["strategy_id"] == "BB_REV_INV" and row["mode"] == "inverse"
        )
        self.assertTrue(bb_inverse["paper_enabled"])
        self.assertTrue(bb_inverse["report_enabled"])
        self.assertFalse(bb_inverse["telegram_enabled"])
        self.assertFalse(bb_inverse["auto_promote_enabled"])

    def test_requested_inverse_strategies_are_registered_and_default_telegram_off(self):
        res = self.client.get("/api/research/retailbot2/strategies", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        rows = res.json()["strategies"]
        for strategy_id in ("BB_REV_INV", "DBL_PAT_INV"):
            row = next(
                item for item in rows
                if item["strategy_id"] == strategy_id and item["mode"] == "inverse"
            )
            self.assertTrue(row["evaluation_enabled"])
            self.assertTrue(row["paper_enabled"])
            self.assertTrue(row["report_enabled"])
            self.assertFalse(row["telegram_enabled"])
            self.assertFalse(row["auto_promote_enabled"])

    def test_strategy_lifecycle_rejects_auto_promotion_until_validation_workflow_exists(self):
        with patch(
            "web.routes.research_routes.set_retailbot2_strategy_lifecycle_setting"
        ) as save:
            res = self.client.post(
                "/api/research/retailbot2/strategies",
                headers=self.headers,
                json={
                    "strategy_id": "DBL_PAT_INV",
                    "mode": "inverse",
                    "evaluation_enabled": True,
                    "paper_enabled": True,
                    "telegram_enabled": False,
                    "report_enabled": True,
                    "auto_promote_enabled": True,
                },
            )
        self.assertEqual(res.status_code, 400)
        self.assertIn("not available", res.json()["detail"].lower())
        save.assert_not_called()

    def test_strategy_lifecycle_rejects_unknown_strategy(self):
        res = self.client.post("/api/research/retailbot2/strategies", headers=self.headers, json={
            "strategy_id": "NOT_A_STRATEGY", "mode": "shadow",
            "evaluation_enabled": True, "paper_enabled": True,
            "telegram_enabled": True, "report_enabled": True,
        })
        self.assertEqual(res.status_code, 400)

    def test_strategy_lifecycle_saves_user_selected_telegram_promotion(self):
        with patch("web.routes.research_routes.set_retailbot2_strategy_lifecycle_setting") as save:
            res = self.client.post("/api/research/retailbot2/strategies", headers=self.headers, json={
                "strategy_id": "BB_REV_INV", "mode": "inverse",
                "evaluation_enabled": True, "paper_enabled": True,
                "telegram_enabled": True, "report_enabled": True,
            })
        self.assertEqual(res.status_code, 200)
        args = save.call_args.args
        self.assertEqual(args[0:2], ("BB_REV_INV", "inverse"))
        self.assertTrue(args[2]["telegram_enabled"])
        self.assertFalse(args[2]["auto_promote_enabled"])

    def test_strategy_lifecycle_stop_and_hide_persist_across_dashboard_reload(self):
        saved_rows = []

        def save_setting(strategy_id, mode, settings):
            saved_rows[:] = [{
                "strategy_id": strategy_id,
                "mode": mode,
                **settings,
                "updated_at": "2026-10-25 00:00:00",
            }]

        with patch(
            "web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings",
            side_effect=lambda: [dict(row) for row in saved_rows],
        ), patch(
            "web.routes.research_routes.set_retailbot2_strategy_lifecycle_setting",
            side_effect=save_setting,
        ):
            stopped = self.client.post(
                "/api/research/retailbot2/strategies",
                headers=self.headers,
                json={
                    "strategy_id": "DBL_PAT_INV",
                    "mode": "inverse",
                    "evaluation_enabled": False,
                    "paper_enabled": False,
                    "telegram_enabled": False,
                    "report_enabled": False,
                    "auto_promote_enabled": False,
                },
            )
            self.assertEqual(stopped.status_code, 200)

            # A separate GET represents a dashboard refresh / new client session.
            refreshed = self.client.get(
                "/api/research/retailbot2/strategies",
                headers=self.headers,
            )
            self.assertEqual(refreshed.status_code, 200)
            row = next(
                item for item in refreshed.json()["strategies"]
                if item["strategy_id"] == "DBL_PAT_INV" and item["mode"] == "inverse"
            )
            self.assertFalse(row["evaluation_enabled"])
            self.assertFalse(row["paper_enabled"])
            self.assertFalse(row["telegram_enabled"])
            self.assertFalse(row["report_enabled"])

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
