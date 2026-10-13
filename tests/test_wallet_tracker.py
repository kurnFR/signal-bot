import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from wallet_tracker import analyzer
from wallet_tracker.chains import explorer_url, normalize_wallet
from wallet_tracker.providers import parse_trade

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

    def test_period_window_excludes_old_swaps(self):
        swaps = [swap(W1, "buy", 200_000, 10, 90)]    # 90 min ago
        self.assertEqual(analyzer.aggregate_swaps(swaps, 1, NOW, 100_000), [])
        self.assertEqual(len(analyzer.aggregate_swaps(swaps, 6, NOW, 100_000)), 1)

    def test_balanced_wallet_is_not_a_candidate(self):
        swaps = [swap(W1, "buy", 500_000, 10, 5), swap(W1, "sell", 480_000, 10, 4)]
        self.assertEqual(analyzer.aggregate_swaps(swaps, 24, NOW, 100_000), [])

    def test_token_flow_signal(self):
        swaps = [swap(W1, "buy", 300_000, 10, 5), swap(W2, "buy", 200_000, 10, 5),
                 swap("0x" + "d" * 40, "sell", 120_000, 10, 5)]
        positions = analyzer.aggregate_swaps(swaps, 24, NOW, 100_000)
        (flow,) = analyzer.token_flow(positions)
        self.assertEqual(flow["signal"], "ACCUMULATION")
        self.assertEqual((flow["accumulating_wallets"], flow["distributing_wallets"]), (2, 1))
        self.assertEqual(flow["net_usd"], 380_000)


class TestParsingAndValidation(unittest.TestCase):
    ATTRS = {"kind": "buy", "tx_from_address": "0x" + "A" * 40, "tx_hash": "0xabc",
             "block_timestamp": "2026-10-13T11:00:00Z", "volume_in_usd": "12345.5",
             "to_token_amount": "1000", "from_token_amount": "5", "price_to_in_usd": "12.3455",
             "price_from_in_usd": "2000"}

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

    @patch("web.routes.wallet_routes.store.get_positions", return_value=[{"wallet": W1}])
    def test_positions_ok(self, m):
        r = self.client.get("/api/wallets/positions?period_hours=24&side=BUY&min_net_usd=50000", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with(24, "BUY", None, 50000.0, 100)

    @patch("web.routes.wallet_routes.store.get_all_positions_for_period")
    def test_token_flow(self, m):
        m.return_value = analyzer.aggregate_swaps([swap(W1, "buy", 300_000, 10, 5)], 24, NOW, 100_000)
        r = self.client.get("/api/wallets/token-flow?period_hours=24", headers=self.headers)
        self.assertEqual(r.json()["flows"][0]["signal"], "ACCUMULATION")

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
