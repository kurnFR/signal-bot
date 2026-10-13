import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from wallet_tracker import analyzer
from wallet_tracker.chains import explorer_url, normalize_wallet
from wallet_tracker.providers import GeckoTerminalProvider, parse_trade
from wallet_tracker.stablecoins import is_stablecoin_base_pool

NOW = datetime(2026, 10, 13, 12, 0, 0)
W1 = "0x" + "a" * 40
W2 = "0x" + "b" * 40
POOL = "0x" + "c" * 40


def swap(wallet, side, usd, tokens, minutes_ago, pool=POOL):
    return {"chain": "eth", "wallet": wallet, "pool_address": pool, "pair": "PEPE / WETH",
            "side": side, "usd_value": usd, "token_amount": tokens,
            "block_time": NOW - timedelta(minutes=minutes_ago)}


class TestAggregation(unittest.TestCase):
    def test_accumulation_math_and_vwap(self):
        swaps = [swap(W1, "buy", 100_000, 1_000_000, 30),
                 swap(W1, "buy", 200_000, 1_000_000, 20),
                 swap(W1, "sell", 50_000, 250_000, 10)]
        (p,) = analyzer.aggregate_swaps(swaps, 24, NOW, 100_000)
        self.assertEqual(p["bias"], analyzer.ACCUMULATING)
        self.assertEqual(p["side"], "BUY")
        self.assertEqual(p["buy_usd"], 300_000)
        self.assertEqual(p["sell_usd"], 50_000)
        self.assertEqual(p["net_usd"], 250_000)
        self.assertEqual(p["total_usd"], 350_000)
        self.assertAlmostEqual(p["avg_buy_price"], 0.15)      # 300k / 2M tokens
        self.assertAlmostEqual(p["avg_sell_price"], 0.2)      # 50k / 250k tokens
        self.assertEqual((p["buy_count"], p["sell_count"], p["trade_count"]), (2, 1, 3))
        self.assertEqual(p["net_tokens"], 1_750_000)

    def test_distribution_and_threshold(self):
        swaps = [swap(W1, "sell", 150_000, 10, 5), swap(W2, "buy", 50_000, 10, 5)]
        out = analyzer.aggregate_swaps(swaps, 24, NOW, 100_000)
        self.assertEqual(len(out), 1)                 # W2 below threshold
        self.assertEqual(out[0]["wallet"], W1)
        self.assertEqual(out[0]["side"], "SELL")
        self.assertEqual(out[0]["bias"], analyzer.DISTRIBUTING)
        self.assertIsNone(out[0]["avg_buy_price"])

    def test_side_uses_token_delta_not_usd_flow(self):
        # Price/value changes can make net USD positive while token holdings fall.
        swaps = [swap(W1, "buy", 200_000, 10, 20),
                 swap(W1, "sell", 80_000, 20, 10)]
        (p,) = analyzer.aggregate_swaps(swaps, 24, NOW, 100_000)
        self.assertGreater(p["net_usd"], 0)
        self.assertLess(p["net_tokens"], 0)
        self.assertEqual(p["side"], "SELL")
        self.assertEqual(p["bias"], analyzer.DISTRIBUTING)
    def test_period_window_excludes_old_swaps(self):
        swaps = [swap(W1, "buy", 200_000, 10, 90)]    # 90 min ago
        self.assertEqual(analyzer.aggregate_swaps(swaps, 1, NOW, 100_000), [])
        self.assertEqual(len(analyzer.aggregate_swaps(swaps, 6, NOW, 100_000)), 1)

    def test_balanced_wallet_is_not_a_candidate(self):
        swaps = [swap(W1, "buy", 500_000, 10, 5), swap(W1, "sell", 480_000, 10, 4)]
        self.assertEqual(analyzer.aggregate_swaps(swaps, 24, NOW, 100_000), [])

    def test_quality_follow_aggregates_across_pools_and_rejects_noise(self):
        pool2 = "0x" + "d" * 40
        swaps = [
            swap(W1, "buy", 200_000, 100, 10, pool=POOL),
            swap(W1, "buy", 100_000, 50, 8, pool=pool2),
            swap(W1, "sell", 50_000, 20, 5, pool=POOL),
            # A single whale-sized trade must not qualify on its own.
            swap(W2, "buy", 900_000, 100, 3, pool=POOL),
            # Large turnover but nearly balanced net flow is not directional.
            swap("0x" + "e" * 40, "buy", 300_000, 100, 4, pool=POOL),
            swap("0x" + "e" * 40, "sell", 290_000, 95, 2, pool=POOL),
        ]
        candidates = analyzer.quality_follow_candidates(
            swaps, [24], NOW, min_net_usd=200_000, min_total_usd=300_000,
            min_trade_count=3, min_net_flow_ratio=0.25,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["wallet"], W1)
        self.assertEqual(candidates[0]["net_usd"], 250_000)
        self.assertEqual(candidates[0]["total_usd"], 350_000)
        self.assertEqual(candidates[0]["trade_count"], 3)

    def test_token_flow_signal(self):
        swaps = [swap(W1, "buy", 300_000, 10, 5), swap(W2, "buy", 200_000, 10, 5),
                 swap("0x" + "d" * 40, "sell", 120_000, 10, 5)]
        positions = analyzer.aggregate_swaps(swaps, 24, NOW, 100_000)
        (flow,) = analyzer.token_flow(positions)
        self.assertEqual(flow["signal"], "ACCUMULATION")
        self.assertEqual((flow["accumulating_wallets"], flow["distributing_wallets"]), (2, 1))
        self.assertEqual(flow["net_usd"], 380_000)

    def test_token_flow_is_mixed_when_net_usd_conflicts_with_wallet_majority(self):
        positions = [
            {"chain": "eth", "pool_address": POOL, "pair": "X/WETH", "bias": "ACCUMULATING",
             "buy_usd": 100_000, "sell_usd": 0, "net_usd": 100_000},
            {"chain": "eth", "pool_address": POOL, "pair": "X/WETH", "bias": "ACCUMULATING",
             "buy_usd": 100_000, "sell_usd": 0, "net_usd": 100_000},
            {"chain": "eth", "pool_address": POOL, "pair": "X/WETH", "bias": "DISTRIBUTING",
             "buy_usd": 0, "sell_usd": 500_000, "net_usd": -500_000},
        ]
        (flow,) = analyzer.token_flow(positions)
        self.assertEqual(flow["signal"], "MIXED")
        self.assertEqual(flow["net_usd"], -300_000)

    def test_token_flow_needs_at_least_two_wallets(self):
        positions = [
            {"chain": "eth", "pool_address": POOL, "pair": "X/WETH", "bias": "ACCUMULATING",
             "buy_usd": 200_000, "sell_usd": 0, "net_usd": 200_000},
        ]
        (flow,) = analyzer.token_flow(positions)
        self.assertEqual(flow["signal"], "MIXED")

    def test_stablecoin_base_pools_are_excluded_but_usdt_quote_pairs_remain(self):
        self.assertTrue(is_stablecoin_base_pool("eth", {
            "base_token_symbol": "USDC", "base_token_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "quote_token_symbol": "WETH", "quote_token_address": W2,
        }))
        self.assertTrue(is_stablecoin_base_pool("bsc", {
            "base_token_symbol": "TOKEN", "base_token_address": "0x" + "1" * 40,
            "quote_token_symbol": "USDT", "quote_token_address": "0x55d398326f99059ff775485246999027b3197955",
        }) is False)
        self.assertFalse(is_stablecoin_base_pool("bsc", {
            "base_token_symbol": "ARK", "base_token_address": "0x" + "2" * 40,
            "quote_token_symbol": "USDT", "quote_token_address": "0x55d398326f99059ff775485246999027b3197955",
        }))


class TestParsingAndValidation(unittest.TestCase):
    ATTRS = {"kind": "buy", "tx_from_address": "0x" + "A" * 40, "tx_hash": "0xabc",
             "block_timestamp": "2026-10-13T11:00:00Z", "volume_in_usd": "12345.5",
             "to_token_amount": "1000", "from_token_amount": "5", "price_to_in_usd": "12.3455",
             "price_from_in_usd": "2000"}

    def test_pool_parser_exposes_base_token_address(self):
        payload = {
            "data": [{"attributes": {"address": POOL, "name": "PEPE / WETH", "volume_usd": {"h24": "1000000"}},
                      "relationships": {"base_token": {"data": {"id": "eth_" + W1}},
                                       "quote_token": {"data": {"id": "eth_" + W2}}}}],
            "included": [{"id": "eth_" + W1, "attributes": {"address": W1, "symbol": "PEPE"}},
                         {"id": "eth_" + W2, "attributes": {"address": W2, "symbol": "WETH"}}]
        }
        (pool,) = GeckoTerminalProvider._pools_from(payload)
        self.assertEqual(pool["base_token_address"], W1)
        self.assertEqual(pool["base_token_symbol"], "PEPE")
    def test_parse_buy_uses_to_token(self):
        s = parse_trade("eth", POOL, "PEPE / WETH", dict(self.ATTRS))
        self.assertEqual(s["wallet"], "0x" + "a" * 40)    # lower-cased
        self.assertEqual((s["side"], s["token_amount"], s["price_usd"]), ("buy", 1000.0, 12.3455))
        self.assertEqual(s["block_time"], datetime(2026, 10, 13, 11, 0, 0))

    def test_parse_sell_uses_from_token(self):
        a = dict(self.ATTRS, kind="sell")
        s = parse_trade("eth", POOL, "x", a)
        self.assertEqual((s["token_amount"], s["price_usd"]), (5.0, 2000.0))

    def test_parse_rejects_bad_rows(self):
        for patch_ in ({"kind": "swap"}, {"volume_in_usd": "0"}, {"volume_in_usd": "nan"},
                       {"tx_from_address": "not-a-wallet"}, {"block_timestamp": "garbage"},
                       {"to_token_amount": None}):
            self.assertIsNone(parse_trade("eth", POOL, "x", dict(self.ATTRS, **patch_)), patch_)
        self.assertIsNone(parse_trade("eth", POOL, "x", None))

    def test_wallet_validation(self):
        self.assertEqual(normalize_wallet("eth", " 0x" + "A" * 40 + " "), "0x" + "a" * 40)
        self.assertIsNone(normalize_wallet("eth", "0x123"))
        sol = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        self.assertEqual(normalize_wallet("solana", sol), sol)       # case preserved
        self.assertIsNone(normalize_wallet("solana", "0x" + "a" * 40))
        self.assertIsNone(normalize_wallet("doge", sol))
        self.assertTrue(explorer_url("eth", W1).startswith("https://etherscan.io/address/0x"))
        self.assertIsNone(explorer_url("eth", "javascript:alert(1)"))


class TestProviderRateLimit(unittest.TestCase):
    def test_rate_limit_increases_delay_and_retries(self):
        from unittest.mock import MagicMock

        provider = GeckoTerminalProvider(delay_seconds=1, session=MagicMock())
        limited = MagicMock()
        limited.status_code = 429
        limited.headers = {"Retry-After": "1"}
        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {}
        ok.json.return_value = {"data": []}
        provider.session.get.side_effect = [limited, ok]
        with patch("wallet_tracker.providers.time.sleep"):
            with patch("wallet_tracker.providers.time.monotonic", return_value=100.0):
                result = provider._get("/networks/eth/pools")
        self.assertEqual(result, {"data": []})
        self.assertEqual(provider.session.get.call_count, 2)
        self.assertGreater(provider.delay, provider.base_delay)


class TestWalletRoutes(unittest.TestCase):
    def setUp(self):
        from web.app import app
        from web.auth import create_access_token
        self.client = TestClient(app)
        self.headers = {}
        role_holder = self

        def fake_auth(request):
            if not request.headers.get("Authorization"):
                raise HTTPException(status_code=401, detail="Authentication credentials missing")
            return {"id": 1, "username": "u", "role": role_holder.role, "is_active": 1}

        self.role = "admin"
        self.token = create_access_token(user_id=1, username="u", role="admin")
        self.headers = {"Authorization": f"Bearer {self.token}"}
        p = patch("web.app.authenticate_request", side_effect=fake_auth)
        p.start()
        self.addCleanup(p.stop)

    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/wallets/positions").status_code, 401)

    def test_positions_validation(self):
        self.assertEqual(self.client.get("/api/wallets/positions?period_hours=5", headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get("/api/wallets/positions?chain=doge", headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get("/api/wallets/positions?side=HODL", headers=self.headers).status_code, 422)

    @patch("web.routes.wallet_routes.store.get_settings", return_value={"enabled": True, "networks": ["eth"], "pools_per_network": 8, "min_trade_usd": 2000, "min_net_usd": 100000, "auto_track_net_usd": 250000, "poll_interval_seconds": 120, "retention_days": 8, "api_delay_seconds": 2.2, "token_mode": "all", "token_addresses": []})
    def test_wallet_settings_read(self, _m):
        r = self.client.get("/api/wallets/settings", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["networks"], ["eth"])

    @patch("web.routes.wallet_routes.store.get_settings", return_value={"enabled": True, "networks": ["eth"], "pools_per_network": 8, "min_trade_usd": 2000, "min_net_usd": 100000, "auto_track_net_usd": 250000, "poll_interval_seconds": 120, "retention_days": 8, "api_delay_seconds": 2.2, "token_mode": "all", "token_addresses": []})
    @patch("web.routes.wallet_routes.store.save_settings")
    def test_wallet_settings_validation_and_save(self, save, _get):
        payload = {"enabled": True, "networks": ["eth"], "pools_per_network": 12, "min_trade_usd": 500, "min_net_usd": 5000, "auto_track_net_usd": 20000, "poll_interval_seconds": 30, "retention_days": 7, "api_delay_seconds": 1, "token_mode": "selected", "token_addresses": ["eth:" + W1]}
        r = self.client.put("/api/wallets/settings", json=payload, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        saved = save.call_args.args[0]
        self.assertEqual(saved["token_addresses"], ["eth:" + W1])
        self.assertEqual(saved["pools_per_network"], 12)
        payload["token_addresses"] = []
        r = self.client.put("/api/wallets/settings", json=payload, headers=self.headers)
        self.assertEqual(r.status_code, 400)
    @patch("web.routes.wallet_routes.store.get_positions", return_value=[{"wallet": W1}])
    def test_positions_ok(self, m):
        r = self.client.get("/api/wallets/positions?period_hours=24&side=BUY&min_net_usd=50000", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with(24, "BUY", None, 50000.0, 100, only_tracked=False)

    @patch("web.routes.wallet_routes.store.get_positions", return_value=[{"wallet": W1}])
    def test_positions_only_tracked(self, m):
        r = self.client.get("/api/wallets/positions?period_hours=24&only_tracked=true", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with(24, None, None, 0.0, 100, only_tracked=True)

    @patch("web.routes.wallet_routes.store.is_wallet_tracked", return_value=False)
    @patch("web.routes.wallet_routes.store.track_wallet")
    def test_toggle_track_on(self, track_mock, is_tracked_mock):
        r = self.client.post("/api/wallets/tracked/toggle", json={"chain": "eth", "wallet": W1}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["tracked"])
        track_mock.assert_called_once_with("eth", W1, None)

    @patch("web.routes.wallet_routes.store.is_wallet_tracked", return_value=True)
    @patch("web.routes.wallet_routes.store.untrack_wallet_by_address", return_value=True)
    def test_toggle_track_off(self, untrack_mock, is_tracked_mock):
        r = self.client.post("/api/wallets/tracked/toggle", json={"chain": "eth", "wallet": W1}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["tracked"])
        untrack_mock.assert_called_once_with("eth", W1)

    @patch("web.routes.wallet_routes.store.get_all_positions_for_period")
    def test_token_flow(self, m):
        m.return_value = analyzer.aggregate_swaps([swap(W1, "buy", 300_000, 10, 5)], 24, NOW, 100_000)
        r = self.client.get("/api/wallets/token-flow?period_hours=24", headers=self.headers)
        self.assertEqual(r.json()["flows"][0]["signal"], "ACCUMULATION")

    @patch("web.routes.wallet_routes.store.get_events", return_value=[])
    def test_events_filter_by_chain_wallet_and_side(self, events_mock):
        r = self.client.get(
            f"/api/wallets/events?chain=eth&wallet={W1.upper().replace('0X', '0x')}&side=buy",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["events"], [])
        events_mock.assert_called_once_with(
            limit=100, since_id=0, chain="eth", wallet=W1, side="buy"
        )

    @patch("web.routes.wallet_routes.store.get_events", return_value=[])
    def test_events_chain_filter_without_wallet(self, events_mock):
        r = self.client.get("/api/wallets/events?chain=base&limit=25", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        events_mock.assert_called_once_with(
            limit=25, since_id=0, chain="base", wallet=None, side=None
        )

    @patch("web.routes.wallet_routes.store.track_wallet")
    def test_track_validates_and_normalises(self, m):
        bad = self.client.post("/api/wallets/tracked", json={"chain": "eth", "wallet": "0x1"}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        bad_chain = self.client.post("/api/wallets/tracked", json={"chain": "doge", "wallet": W1}, headers=self.headers)
        self.assertEqual(bad_chain.status_code, 400)
        m.assert_not_called()
        ok = self.client.post("/api/wallets/tracked", json={"chain": "eth", "wallet": W1.upper().replace("0X", "0x"), "label": " whale "}, headers=self.headers)
        self.assertEqual(ok.status_code, 200)
        m.assert_called_once_with("eth", W1, "whale")

    @patch("web.routes.wallet_routes.store.track_wallet")
    @patch("web.routes.wallet_routes.store.get_tracked", return_value=[])
    def test_viewer_can_read_but_not_mutate(self, _get, track):
        self.role = "viewer"
        self.assertEqual(self.client.get("/api/wallets/tracked", headers=self.headers).status_code, 200)
        r = self.client.post("/api/wallets/tracked", json={"chain": "eth", "wallet": W1}, headers=self.headers)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.delete("/api/wallets/tracked/1", headers=self.headers).status_code, 403)
        track.assert_not_called()

    @patch("web.routes.wallet_routes.store.untrack_wallet", return_value=False)
    def test_untrack_missing_is_404(self, _m):
        self.assertEqual(self.client.delete("/api/wallets/tracked/99", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
