"""Wallet settings must never return provider credentials to API clients."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app


class TestWalletSettingsSecretRedaction(unittest.TestCase):
    def test_settings_response_exposes_only_masked_birdeye_key(self):
        secret = "birdeye-live-secret-123456"
        settings = {
            "enabled": True,
            "networks": ["ethereum"],
            "pools_per_network": 4,
            "min_trade_usd": 10000,
            "min_net_usd": 150000,
            "auto_track_net_usd": 500000,
            "poll_interval_seconds": 300,
            "retention_days": 8,
            "api_delay_seconds": 4,
            "token_mode": "all",
            "token_addresses": [],
            "birdeye_api_key": secret,
        }
        user = {"id": 1, "username": "viewer1", "email": None, "role": "viewer",
                "is_active": 1, "must_change_password": 0}
        client = TestClient(app, raise_server_exceptions=False)
        with (patch("web.app.authenticate_request", return_value=user),
              patch("web.auth.authenticate_request", return_value=user),
              patch("web.routes.wallet_routes.store.get_settings", return_value=settings.copy())):
            response = client.get("/api/wallets/settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("birdeye_api_key", payload)
        self.assertNotIn(secret, response.text)
        self.assertEqual(payload["birdeye_api_key_masked"], "••••••••3456")
        self.assertTrue(payload["has_birdeye_key"])


if __name__ == "__main__":
    unittest.main()
