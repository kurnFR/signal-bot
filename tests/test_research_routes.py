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
            patch("web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings", return_value=[]),
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
            "/api/research/retailbot2/archive/summary",
            "/api/research/retailbot2/archive/trades.csv",
            "/api/research/retailbot2/archive/stats.csv",
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

    def test_lifecycle_readback_parses_legacy_flags_and_never_exposes_auto_promotion(self):
        legacy_rows = [{
            "strategy_id": "BB_REV_INV",
            "mode": "inverse",
            "evaluation_enabled": "0",
            "paper_enabled": "0",
            "telegram_enabled": "0",
            "report_enabled": "false",
            "auto_promote_enabled": "1",
        }]
        with patch(
            "web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings",
            return_value=legacy_rows,
        ):
            res = self.client.get(
                "/api/research/retailbot2/strategies",
                headers=self.headers,
            )

        self.assertEqual(res.status_code, 200)
        row = next(
            item for item in res.json()["strategies"]
            if item["strategy_id"] == "BB_REV_INV" and item["mode"] == "inverse"
        )
        self.assertFalse(row["evaluation_enabled"])
        self.assertFalse(row["paper_enabled"])
        self.assertFalse(row["telegram_enabled"])
        self.assertFalse(row["report_enabled"])
        # Legacy DB values must not make an unavailable feature appear enabled.
        self.assertFalse(row["auto_promote_enabled"])

    def test_historical_composite_rows_fail_closed_even_if_legacy_settings_enable_them(self):
        legacy_rows = [{
            "strategy_id": "COMPOSITE",
            "mode": "shadow",
            "evaluation_enabled": 1,
            "paper_enabled": 1,
            "telegram_enabled": 1,
            "report_enabled": 1,
            "auto_promote_enabled": 1,
        }]
        with patch(
            "web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings",
            return_value=legacy_rows,
        ):
            res = self.client.get(
                "/api/research/retailbot2/strategies",
                headers=self.headers,
            )

        self.assertEqual(res.status_code, 200)
        row = next(
            item for item in res.json()["strategies"]
            if item["strategy_id"] == "COMPOSITE" and item["mode"] == "shadow"
        )
        self.assertFalse(row["evaluation_enabled"])
        self.assertFalse(row["paper_enabled"])
        self.assertFalse(row["telegram_enabled"])
        self.assertTrue(row["report_enabled"])
        self.assertFalse(row["auto_promote_enabled"])

    def test_retailbot2_strategy_lifecycle_writes_are_retired(self):
        """Step 3: POST /retailbot2/strategies is retired with the worker.
        There is no process left to apply lifecycle writes. The route is
        gone, and the api_security_boundary fails closed with 403 for a
        POST to a path that matches no write prefix (not a 400 for a bad
        payload, and not a silent no-op)."""
        for payload in (
            {
                "strategy_id": "BB_REV_INV", "mode": "inverse",
                "evaluation_enabled": True, "paper_enabled": True,
                "telegram_enabled": True, "report_enabled": True,
            },
            {
                "strategy_id": "DBL_PAT_INV", "mode": "inverse",
                "evaluation_enabled": True, "paper_enabled": True,
                "telegram_enabled": False, "report_enabled": True,
                "auto_promote_enabled": True,
            },
        ):
            res = self.client.post(
                "/api/research/retailbot2/strategies",
                headers=self.headers, json=payload,
            )
            self.assertEqual(res.status_code, 403, payload)

    def test_retailbot2_control_write_is_retired(self):
        """Step 3: POST /retailbot2/control is retired with the worker
        (403 from the security boundary, as above)."""
        res = self.client.post(
            "/api/research/retailbot2/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"rr_ratio": 2.5}},
        )
        self.assertEqual(res.status_code, 403)

    def test_strategy_lifecycle_get_surfaces_persistence_read_failure(self):
        with patch(
            "web.routes.research_routes.get_retailbot2_strategy_lifecycle_settings",
            side_effect=RuntimeError("database read unavailable"),
        ):
            res = self.client.get("/api/research/retailbot2/strategies", headers=self.headers)

        self.assertEqual(res.status_code, 500)
        self.assertIn("failed to load", res.json()["detail"].lower())

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

    def test_retailbot2_control_get_is_read_only(self):
        # GET still serves the saved control row for archive display.
        res = self.client.get("/api/research/retailbot2/control", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("enabled", data)
        self.assertIn("tunable_fields", data)

        # POST is retired with the worker (see
        # test_retailbot2_control_write_is_retired); even an unknown override
        # key is a 403 now, not a 400 -- there is no write path left to
        # validate against.
        bad_res = self.client.post(
            "/api/research/retailbot2/control",
            headers=self.headers,
            json={"enabled": True, "overrides": {"db_password": 123.0}}
        )
        self.assertEqual(bad_res.status_code, 403)

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

    @patch("web.routes.research_routes.get_screener_control")
    @patch("web.routes.research_routes.get_screener_status")
    def test_screener_status_returns_500_when_control_schema_is_unavailable(self, mock_status, mock_control):
        mock_control.side_effect = RuntimeError("screener_control table missing")
        mock_status.return_value = {"running": False, "database_ok": True}

        response = self.client.get("/api/research/screener/status", headers=self.headers)

        self.assertEqual(response.status_code, 500)
        mock_status.assert_not_called()

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

    @patch("web.routes.research_routes.get_retailbot2_archive_summary", return_value={"trades_count": 9042, "stats_count": 14})
    def test_retailbot2_archive_summary_returns_exact_counts(self, mock_summary):
        res = self.client.get("/api/research/retailbot2/archive/summary", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"tradesCount": 9042, "statsCount": 14})
        mock_summary.assert_called_once_with()

    @patch("web.routes.research_routes.iter_retailbot2_table_batches")
    def test_retailbot2_archive_trades_csv_streams_paginated_rows(self, mock_iter):
        import csv
        import io
        from db.db import RETAILBOT2_ARCHIVE_COLUMNS

        cols = RETAILBOT2_ARCHIVE_COLUMNS["rdt_trades"]
        row1 = tuple(f"v1_{i}" for i in range(len(cols)))
        row2 = (2, "BTCUSDT", "SHORT", "inverse", "50000.0", "50500.0", "49000.0", "250.0", "2.0", "0.1",
                "BB_REV", '["BB_REV", "RSI_CTR"]', 0, 2, "fade, bounce\nline2", "CLOSED", "49000.0",
                "TAKE_PROFIT", "100.0", "2.0", "2026-10-01 00:00:00", "2026-10-01 04:00:00", "2026-10-01 00:00:00")
        row3 = (3, "ETHUSDT", "LONG", "shadow", "3000.0", "2950.0", "3100.0", "25.0", "2.0", "1.0",
                "MA_WHIP", None, 1, 1, None, "OPEN", None, None, None, None, "2026-10-02 01:00:00", None, "2026-10-02 01:00:00")
        mock_iter.return_value = iter([[row1, row2], [row3]])

        res = self.client.get("/api/research/retailbot2/archive/trades.csv", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("content-type", ""))
        self.assertIn('filename="rdt_trades_archive.csv"', res.headers.get("content-disposition", ""))

        parsed = list(csv.reader(io.StringIO(res.text)))
        self.assertEqual(tuple(parsed[0]), cols)
        self.assertEqual(len(parsed) - 1, 3)
        self.assertEqual(parsed[2][14], "fade, bounce\nline2")
        self.assertEqual(parsed[3][11], "")
        mock_iter.assert_called_once_with("rdt_trades", batch_size=500)

    @patch("web.routes.research_routes.iter_retailbot2_table_batches")
    def test_retailbot2_archive_stats_csv_streams_rows(self, mock_iter):
        import csv
        import io
        from db.db import RETAILBOT2_ARCHIVE_COLUMNS

        cols = RETAILBOT2_ARCHIVE_COLUMNS["rdt_strategy_stats"]
        rows = [
            (1, "BB_REV", "shadow", 120, 40, 80, "-250.5", "2026-11-01 00:00:00"),
            (2, "BB_REV", "inverse", 120, 80, 40, "310.25", "2026-11-01 00:00:00"),
        ]
        mock_iter.return_value = iter([rows])

        res = self.client.get("/api/research/retailbot2/archive/stats.csv", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn('filename="rdt_strategy_stats_archive.csv"', res.headers.get("content-disposition", ""))

        parsed = list(csv.reader(io.StringIO(res.text)))
        self.assertEqual(tuple(parsed[0]), cols)
        self.assertEqual(len(parsed) - 1, len(rows))
        mock_iter.assert_called_once_with("rdt_strategy_stats", batch_size=500)

    def test_iter_retailbot2_table_batches_uses_keyset_pagination(self):
        from unittest.mock import MagicMock
        import db.db as db_module

        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [(1, "a"), (2, "b")],
            [(5, "c")],
        ]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        pool = MagicMock()
        pool.get_connection.return_value = conn

        with patch.object(db_module, "get_retailbot2_pool", return_value=pool):
            batches = list(db_module.iter_retailbot2_table_batches("rdt_strategy_stats", batch_size=2))

        self.assertEqual(batches, [[(1, "a"), (2, "b")], [(5, "c")]])
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("WHERE id > %s ORDER BY id ASC LIMIT %s", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(cursor.execute.call_args_list[0].args[1], (0, 2))
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (2, 2))
        cursor.close.assert_called_once()
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_ui_show_retailbot2_history_flag_exposed_in_status(self):
        import os
        with patch.dict(os.environ, {}, clear=False), \
             patch("web.routes.settings.collect_system_status", return_value={
                 "database": {"status": "ok", "tables": {}},
                 "heartbeats": [],
                 "binance": {"spotApi": "online", "futuresApi": "online", "enableFuturesConfig": True},
                 "loginRateLimiter": {"mode": "memory", "degraded": False},
             }):
            os.environ.pop("UI_SHOW_RETAILBOT2_HISTORY", None)
            from web.routes.settings import is_retailbot2_history_visible
            self.assertFalse(is_retailbot2_history_visible())

        with patch.dict(os.environ, {"UI_SHOW_RETAILBOT2_HISTORY": "1"}, clear=False):
            from web.routes.settings import is_retailbot2_history_visible
            self.assertTrue(is_retailbot2_history_visible())

        res_default = self.client.get("/api/system/status", headers=self.headers)
        self.assertEqual(res_default.status_code, 200)
        self.assertFalse(res_default.json()["uiFlags"]["showRetailbot2History"])

        with patch.dict(os.environ, {"UI_SHOW_RETAILBOT2_HISTORY": "true"}, clear=False):
            res_enabled = self.client.get("/api/system/status", headers=self.headers)
            self.assertEqual(res_enabled.status_code, 200)
            self.assertTrue(res_enabled.json()["uiFlags"]["showRetailbot2History"])


if __name__ == "__main__":
    unittest.main()
