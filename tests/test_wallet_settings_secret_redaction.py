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


    def test_save_settings_response_never_returns_raw_birdeye_key(self):
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings

        secret = "birdeye-live-secret-123456"
        saved = {
            "enabled": True,
            "networks": ["eth"],
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
        request = WalletSettingsRequest(**{k: v for k, v in saved.items() if k != "birdeye_api_key"},
                                        birdeye_api_key=secret)
        with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), saved.copy()]),
              patch("web.routes.wallet_routes.store.save_settings") as save):
            payload = wallets_save_settings(request)

        save.assert_called_once()
        self.assertNotIn("birdeye_api_key", payload)
        self.assertNotIn(secret, str(payload))
        self.assertEqual(payload["birdeye_api_key_masked"], "••••••••3456")
        self.assertTrue(payload["has_birdeye_key"])


    def test_save_settings_omitted_key_preserves_existing_secret(self):
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings

        secret = "birdeye-live-secret-123456"
        saved = {
            "enabled": True,
            "networks": ["eth"],
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
        # Request omits birdeye_api_key completely (default is None)
        request = WalletSettingsRequest(**{k: v for k, v in saved.items() if k != "birdeye_api_key"})
        with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), saved.copy()]),
              patch("web.routes.wallet_routes.store.save_settings") as save):
            payload = wallets_save_settings(request)

        save.assert_called_once()
        passed_settings = save.call_args[0][0]
        self.assertEqual(passed_settings["birdeye_api_key"], secret)
        self.assertEqual(payload["birdeye_api_key_masked"], "••••••••3456")
        self.assertTrue(payload["has_birdeye_key"])


    def test_save_settings_masked_or_empty_key_preserves_existing_secret(self):
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings

        secret = "birdeye-live-secret-123456"
        saved = {
            "enabled": True,
            "networks": ["eth"],
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
        for placeholder in ("", "   ", "••••••••3456", "******"):
            with self.subTest(placeholder=placeholder):
                request = WalletSettingsRequest(
                    **{k: v for k, v in saved.items() if k != "birdeye_api_key"},
                    birdeye_api_key=placeholder,
                )
                with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), saved.copy()]),
                      patch("web.routes.wallet_routes.store.save_settings") as save):
                    payload = wallets_save_settings(request)

                save.assert_called_once()
                passed_settings = save.call_args[0][0]
                self.assertEqual(passed_settings["birdeye_api_key"], secret)
                self.assertEqual(payload["birdeye_api_key_masked"], "••••••••3456")
                self.assertTrue(payload["has_birdeye_key"])


    def test_save_settings_new_key_replaces_existing_secret(self):
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings

        old_secret = "birdeye-live-secret-123456"
        new_secret = "birdeye-brand-new-secret-7890"
        saved = {
            "enabled": True,
            "networks": ["eth"],
            "pools_per_network": 4,
            "min_trade_usd": 10000,
            "min_net_usd": 150000,
            "auto_track_net_usd": 500000,
            "poll_interval_seconds": 300,
            "retention_days": 8,
            "api_delay_seconds": 4,
            "token_mode": "all",
            "token_addresses": [],
            "birdeye_api_key": old_secret,
        }
        updated = dict(saved, birdeye_api_key=new_secret)
        request = WalletSettingsRequest(
            **{k: v for k, v in saved.items() if k != "birdeye_api_key"},
            birdeye_api_key=new_secret,
        )
        with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), updated.copy()]),
              patch("web.routes.wallet_routes.store.save_settings") as save):
            payload = wallets_save_settings(request)

        save.assert_called_once()
        passed_settings = save.call_args[0][0]
        self.assertEqual(passed_settings["birdeye_api_key"], new_secret)
        self.assertEqual(payload["birdeye_api_key_masked"], "••••••••7890")
        self.assertTrue(payload["has_birdeye_key"])


    def test_save_settings_clear_key_flag_removes_existing_secret(self):
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings

        secret = "birdeye-live-secret-123456"
        saved = {
            "enabled": True,
            "networks": ["eth"],
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
        cleared = dict(saved, birdeye_api_key="")
        request = WalletSettingsRequest(
            **{k: v for k, v in saved.items() if k != "birdeye_api_key"},
            clear_birdeye_api_key=True,
        )
        with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), cleared.copy()]),
              patch("web.routes.wallet_routes.store.save_settings") as save):
            payload = wallets_save_settings(request)

        save.assert_called_once()
        passed_settings = save.call_args[0][0]
        self.assertEqual(passed_settings["birdeye_api_key"], "")
        self.assertEqual(payload["birdeye_api_key_masked"], "")
        self.assertFalse(payload["has_birdeye_key"])


    def test_untrack_wallet_route_contract_returns_tracked_id(self):
        user = {"id": 2, "username": "trader1", "email": None, "role": "trader",
                "is_active": 1, "must_change_password": 0}
        client = TestClient(app, raise_server_exceptions=False)
        with (patch("web.app.authenticate_request", return_value=user),
              patch("web.auth.authenticate_request", return_value=user),
              patch("web.routes.wallet_routes.store.untrack_wallet", return_value=True)):
            response = client.delete("/api/wallets/tracked/42")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tracked_id"], 42)


    def test_network_limit_boundaries(self):
        from pydantic import ValidationError
        from web.routes.wallet_routes import WalletSettingsRequest, wallets_save_settings
        from fastapi import HTTPException

        all_eight = ["eth", "base", "bsc", "arbitrum", "polygon_pos", "optimism", "avalanche", "solana"]
        base_kwargs = {
            "enabled": True,
            "pools_per_network": 4,
            "min_trade_usd": 10000,
            "min_net_usd": 150000,
            "auto_track_net_usd": 500000,
            "poll_interval_seconds": 300,
            "retention_days": 8,
            "api_delay_seconds": 4,
            "token_mode": "all",
            "token_addresses": [],
        }

        # 1. Exactly 8 networks: accepted by WalletSettingsRequest and wallets_save_settings
        req_eight = WalletSettingsRequest(networks=all_eight, **base_kwargs)
        self.assertEqual(len(req_eight.networks), 8)
        saved = dict(base_kwargs, networks=all_eight, birdeye_api_key="")
        with (patch("web.routes.wallet_routes.store.get_settings", side_effect=[saved.copy(), saved.copy()]),
              patch("web.routes.wallet_routes.store.save_settings") as save):
            payload = wallets_save_settings(req_eight)
        self.assertEqual(len(payload["networks"]), 8)

        # 2. 9 networks: rejected by Pydantic max_length=8 with ValidationError
        nine_networks = all_eight + ["solana"]  # 9 items
        with self.assertRaises(ValidationError):
            WalletSettingsRequest(networks=nine_networks, **base_kwargs)

        # 3. 0 networks: rejected by Pydantic min_length=1 with ValidationError
        with self.assertRaises(ValidationError):
            WalletSettingsRequest(networks=[], **base_kwargs)

        # 4. Unsupported network: rejected with 400 by wallets_save_settings
        req_invalid = WalletSettingsRequest(networks=["unsupported_chain"], **base_kwargs)
        with self.assertRaises(HTTPException) as ctx:
            wallets_save_settings(req_invalid)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Unsupported network(s)", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
