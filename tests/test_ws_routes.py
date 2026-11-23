import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestWsRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = create_access_token(user_id=1, username="admin", role="admin")

    @patch("web.routes.ws_routes._get_candles_from_db")
    def test_get_market_candles_authenticated(self, mock_get_candles):
        mock_get_candles.return_value = {
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "candles": [{"time": 1700000000, "open": 50000.0, "high": 50500.0, "low": 49800.0, "close": 50200.0, "volume": 10.5}],
            "mark_price": 50200.0,
            "funding_rate": 0.0001,
        }
        res = self.client.get(
            "/api/market/candles?symbol=BTCUSDT&market=spot&timeframe=1h&limit=50",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["symbol"], "BTCUSDT")
        self.assertEqual(len(data["candles"]), 1)
        self.assertEqual(data["candles"][0]["close"], 50200.0)

    def test_ws_market_unauthenticated_rejected(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/ws/market"):
                pass

    @patch("web.routes.ws_routes._get_candles_from_db")
    def test_ws_market_authenticated_connect_and_ping(self, mock_get_candles):
        mock_get_candles.return_value = {
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "candles": [],
            "mark_price": 50000.0,
            "funding_rate": 0.0001,
        }
        with self.client.websocket_connect(f"/ws/market?token={self.token}") as ws:
            init_msg = ws.receive_json()
            self.assertEqual(init_msg["type"], "market_snapshot")
            self.assertEqual(init_msg["symbol"], "BTCUSDT")

            ws.send_json({"action": "ping"})
            pong_msg = ws.receive_json()
            self.assertEqual(pong_msg["type"], "pong")

    @patch("web.routes.ws_routes.get_active_positions")
    @patch("web.routes.ws_routes.get_circuit_breaker_status")
    def test_ws_paper_authenticated_connect(self, mock_breaker, mock_positions):
        mock_positions.return_value = []
        mock_breaker.return_value = {"status": "ok", "emergency_stop_active": False}
        with self.client.websocket_connect(f"/ws/paper?token={self.token}") as ws:
            init_msg = ws.receive_json()
            self.assertEqual(init_msg["type"], "paper_snapshot")
            self.assertEqual(init_msg["positions"], [])

            ws.send_json({"action": "ping"})
            pong_msg = ws.receive_json()
            self.assertEqual(pong_msg["type"], "pong")

    @patch("web.routes.ws_routes.get_public_system_status")
    def test_ws_system_authenticated_connect(self, mock_sys_status):
        mock_sys_status.return_value = {
            "database": {"status": "ok"},
            "heartbeats": [],
            "binance": {},
        }
        with self.client.websocket_connect(f"/ws/system?token={self.token}") as ws:
            init_msg = ws.receive_json()
            self.assertEqual(init_msg["type"], "system_snapshot")
            self.assertEqual(init_msg["status"]["database"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
