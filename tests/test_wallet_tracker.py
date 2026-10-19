import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from wallet_tracker import analyzer, store, worker
from wallet_tracker.chains import explorer_url, normalize_wallet
from wallet_tracker.providers import (BirdeyeProvider, DexScreenerProvider,
                                      GeckoTerminalProvider,
                                      parse_birdeye_trade, parse_trade)
from wallet_tracker.stablecoins import is_stablecoin_base_pool

NOW = datetime(2026, 10, 13, 12, 0, 0)
W1 = "0x" + "a" * 40
W2 = "0x" + "b" * 40
POOL = "0x" + "c" * 40


def swap(wallet, side, usd, tokens, minutes_ago, pool=POOL):
    return {"chain": "eth", "wallet": wallet, "pool_address": pool, "pair": "PEPE / WETH",
            "side": side, "usd_value": usd, "token_amount": tokens,
            "block_time": NOW - timedelta(minutes=minutes_ago)}


class TestAtomicWalletToggle(unittest.TestCase):
    def _run_toggle(self, lock_result=1, current_status=None):
        from unittest.mock import MagicMock

        connection = MagicMock()
        cursor = MagicMock()
        release_cursor = MagicMock()
        release_cursor.fetchone.return_value = (1,)
        connection.cursor.side_effect = [cursor, release_cursor]
        cursor.fetchone.side_effect = [(lock_result,), (current_status,) if current_status is not None else None]
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            result = store.toggle_wallet_tracking("eth", W1, "test")
        return result, connection, cursor

    def test_new_wallet_is_inserted_active(self):
        result, connection, cursor = self._run_toggle(1, None)
        self.assertTrue(result)
        sql_calls = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("GET_LOCK" in sql for sql in sql_calls))
        self.assertTrue(any("SELECT status FROM tracked_wallets" in sql for sql in sql_calls))
        self.assertTrue(any("INSERT INTO tracked_wallets" in sql for sql in sql_calls))
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()

    def test_active_wallet_is_ignored(self):
        result, connection, cursor = self._run_toggle(1, "ACTIVE")
        self.assertFalse(result)
        self.assertTrue(any("SET status = 'IGNORED'" in call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_called_once()

    def test_inactive_wallet_is_reactivated_as_manual(self):
        result, connection, cursor = self._run_toggle(1, "INACTIVE")
        self.assertTrue(result)
        self.assertTrue(any("source = 'manual'" in call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_called_once()

    def test_advisory_lock_timeout_rolls_back(self):
        from unittest.mock import MagicMock

        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (0,)
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            with self.assertRaises(TimeoutError):
                store.toggle_wallet_tracking("eth", W1)
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_missing_row_after_select_inserts_without_duplicate_race(self):
        result, connection, cursor = self._run_toggle(1, None)
        self.assertTrue(result)
        self.assertTrue(any("INSERT INTO tracked_wallets" in call.args[0] for call in cursor.execute.call_args_list))
        connection.commit.assert_called_once()


class TestStoreSerialization(unittest.TestCase):
    def test_datetime_serializes_as_explicit_utc_iso8601(self):
        row = {"block_time": datetime(2026, 10, 13, 7, 53, 10)}
        self.assertEqual(store._clean(row, wallet_chain_keys=False)["block_time"], "2026-10-13T07:53:10Z")

    def test_aware_datetime_is_normalized_to_utc(self):
        from datetime import timezone
        row = {"created_at": datetime(2026, 10, 13, 15, 53, 10, tzinfo=timezone(timedelta(hours=8)))}
        self.assertEqual(store._clean(row, wallet_chain_keys=False)["created_at"], "2026-10-13T07:53:10Z")


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
            swap(W1, "buy", 300_000, 100, 10, pool=POOL),
            swap(W1, "buy", 50_000, 25, 8, pool=POOL),
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
        self.assertEqual(candidates[0]["net_usd"], 300_000)
        self.assertEqual(candidates[0]["total_usd"], 400_000)
        self.assertEqual(candidates[0]["trade_count"], 3)

    def test_follow_candidate_does_not_net_unrelated_pools(self):
        pool2 = "0x" + "d" * 40
        swaps = [
            swap(W1, "buy", 120_000, 100, 20, pool=POOL),
            swap(W1, "buy", 100_000, 80, 15, pool=POOL),
            swap(W1, "buy", 100_000, 80, 10, pool=POOL),
            swap(W1, "sell", 100_000, 90, 8, pool=pool2),
            swap(W1, "sell", 90_000, 80, 6, pool=pool2),
            swap(W1, "sell", 80_000, 70, 4, pool=pool2),
        ]
        candidates = analyzer.quality_follow_candidates(
            swaps, [24], NOW, min_net_usd=200_000, min_total_usd=300_000,
            min_trade_count=3, min_net_flow_ratio=0.25,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["pool_address"], POOL)
        self.assertEqual(candidates[0]["net_usd"], 320_000)

    def test_high_frequency_round_trip_activity_is_rejected_but_directional_activity_is_kept(self):
        noisy = []
        for minute in range(60):
            noisy.append(swap(W1, "buy", 20_000, 100, minute, pool=POOL))
            noisy.append(swap(W1, "sell", 9_000, 99, minute, pool=POOL))
        directional = [
            swap(W2, "buy", 300_000, 1000, 50, pool=POOL),
            swap(W2, "buy", 200_000, 700, 40, pool=POOL),
            swap(W2, "buy", 100_000, 500, 30, pool=POOL),
        ]
        candidates = analyzer.quality_follow_candidates(
            noisy + directional, [1], NOW, min_net_usd=200_000, min_total_usd=300_000,
            min_trade_count=3, min_net_flow_ratio=0.25,
        )
        self.assertEqual([c["wallet"] for c in candidates], [W2])

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
        self.assertFalse(is_stablecoin_base_pool("bsc", {
            "base_token_symbol": "TOKEN", "base_token_address": "0x" + "1" * 40,
            "quote_token_symbol": "USDT", "quote_token_address": "0x55d398326f99059ff775485246999027b3197955",
        }))
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
    def test_pool_parser_skips_malformed_provider_records(self):
        payload = {
            "data": [
                None,
                {"attributes": None},
                {"attributes": {"address": "0xpool", "volume_usd": "unexpected"}},
                {"attributes": {"address": "0xvalid", "volume_usd": {"h24": "1200"}}},
            ],
            "included": [None, {"id": "eth_token", "attributes": None}],
        }
        pools = GeckoTerminalProvider._pools_from(payload)
        self.assertEqual(len(pools), 2)
        self.assertEqual(pools[0]["volume_24h_usd"], 0.0)
        self.assertEqual(pools[1]["volume_24h_usd"], 1200.0)

    def test_pool_parser_rejects_non_object_payload(self):
        for payload in (None, [], "invalid", {"data": "not-a-list"}):
            self.assertEqual(GeckoTerminalProvider._pools_from(payload), [])

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
    def test_retry_after_parses_seconds_and_http_date(self):
        from datetime import timezone
        from wallet_tracker.providers import _retry_after_seconds

        now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(_retry_after_seconds("45", now=now), 45.0)
        self.assertEqual(
            _retry_after_seconds("Tue, 13 Oct 2026 12:01:00 GMT", now=now),
            60.0,
        )
        self.assertIsNone(_retry_after_seconds("not-a-date", now=now))

    def test_long_retry_after_defers_instead_of_retrying_early(self):
        from unittest.mock import MagicMock
        import requests

        provider = GeckoTerminalProvider(delay_seconds=1, session=MagicMock())
        limited = MagicMock()
        limited.status_code = 429
        limited.headers = {"Retry-After": "300"}
        limited.raise_for_status.side_effect = requests.HTTPError(
            "rate limited", response=limited
        )
        provider.session.get.return_value = limited
        with patch("wallet_tracker.providers.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                provider._get("/networks/eth/pools")
        self.assertEqual(provider.session.get.call_count, 1)
        sleep.assert_not_called()

    def test_pool_discovery_is_cached_for_ttl(self):
        from unittest.mock import MagicMock

        provider = GeckoTerminalProvider(delay_seconds=1, session=MagicMock())
        provider._pools_from = MagicMock(return_value=[
            {"address": "pool-1", "name": "TOKEN / WETH", "volume_24h_usd": 200000}
        ])
        provider._get = MagicMock(return_value={"data": []})
        with patch("wallet_tracker.providers.time.monotonic", side_effect=[100.0, 100.0, 101.0]):
            first = provider.hot_pools("eth", 4)
            second = provider.hot_pools("eth", 4)
        self.assertEqual(first, second)
        self.assertEqual(provider._get.call_count, 2)

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


class TestAutoFollowExpiry(unittest.TestCase):
    def test_expiry_only_targets_stale_active_auto_wallets(self):
        from unittest.mock import MagicMock

        connection = MagicMock()
        connection.cursor.return_value.rowcount = 2
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            expired = store.expire_inactive_auto_wallets(7)

        self.assertEqual(expired, 2)
        sql, params = connection.cursor.return_value.execute.call_args.args
        self.assertIn("source = 'auto'", sql)
        self.assertIn("status = 'ACTIVE'", sql)
        self.assertIn("status = 'INACTIVE'", sql)
        self.assertEqual(params, (7,))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()


class TestWorkerRateLimitIsolation(unittest.TestCase):
    def test_rate_limited_network_does_not_starve_other_networks(self):
        from unittest.mock import MagicMock
        import requests

        settings = {
            "enabled": True, "networks": ["eth", "solana", "base", "bsc"],
            "pools_per_network": 1, "min_trade_usd": 10000,
            "min_net_usd": 150000, "auto_track_net_usd": 250000,
            "retention_days": 8, "api_delay_seconds": 1,
        }
        provider = MagicMock()
        provider.hot_pools.side_effect = lambda network, limit: (
            (_ for _ in ()).throw(requests.HTTPError(
                "rate limited", response=MagicMock(status_code=429)
            )) if network == "eth" else []
        )
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.store, "get_settings", return_value=settings))
            stack.enter_context(patch.object(worker, "recompute", return_value=0))
            stack.enter_context(patch.object(worker.store, "prune_swaps"))
            stack.enter_context(patch.object(worker.store, "prune_wallet_alerts"))
            stack.enter_context(patch.object(worker.store, "get_active_pools", return_value=[]))
            stack.enter_context(patch.object(worker.store, "set_status"))
            stack.enter_context(patch.object(worker, "dispatch_telegram_alerts"))
            stack.enter_context(patch.object(worker, "_network_rotation_offset", 0))
            stack.enter_context(patch.dict(worker._NETWORK_COOLDOWN_UNTIL, {}, clear=True))
            worker.run_cycle(provider)
            cooldown_after_cycle = dict(worker._NETWORK_COOLDOWN_UNTIL)

        called_networks = [call.args[0] for call in provider.hot_pools.call_args_list]
        self.assertEqual(called_networks, ["eth", "solana", "base", "bsc"])
        self.assertIn("solana", called_networks)
        self.assertIn("base", called_networks)
        self.assertIn("bsc", called_networks)
        self.assertIn("eth", cooldown_after_cycle)



class TestWorkerProviderFailover(unittest.TestCase):
    def test_birdeye_takes_over_after_geckoterminal_trade_rate_limit(self):
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        import requests

        settings = {
            "enabled": True, "networks": ["eth", "solana", "base", "bsc"],
            "pools_per_network": 1, "min_trade_usd": 10000,
            "min_net_usd": 150000, "auto_track_net_usd": 250000,
            "retention_days": 8, "api_delay_seconds": 1, "token_mode": "all",
            "token_addresses": [],
        }
        pool = {
            "address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 200000,
            "base_token_address": W1, "base_token_symbol": "PEPE",
            "quote_token_address": W2, "quote_token_symbol": "WETH",
        }
        primary = MagicMock()
        primary.hot_pools.side_effect = lambda network, limit: [pool] if network == "eth" else []
        primary.pool_trades.side_effect = requests.HTTPError(
            "rate limited", response=MagicMock(status_code=429)
        )
        fallback = MagicMock()
        fallback.pool_trades.return_value = [swap(W1, "buy", 20000, 5, 1)]

        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.store, "get_settings", return_value=settings))
            stack.enter_context(patch.object(worker.store, "get_active_pools", return_value=[]))
            stack.enter_context(patch.object(worker, "recompute", return_value=0))
            stack.enter_context(patch.object(worker.store, "prune_swaps"))
            stack.enter_context(patch.object(worker.store, "prune_wallet_alerts"))
            stack.enter_context(patch.object(worker.store, "set_status"))
            insert_swaps = stack.enter_context(patch.object(worker.store, "insert_swaps", return_value=1))
            stack.enter_context(patch.object(worker, "dispatch_telegram_alerts"))
            stack.enter_context(patch.object(worker, "_network_rotation_offset", 0))
            stack.enter_context(patch.dict(worker._NETWORK_COOLDOWN_UNTIL, {}, clear=True))
            worker.run_cycle(primary, fallback_provider=fallback)

        primary.pool_trades.assert_called_once_with("eth", pool, 10000)
        fallback.pool_trades.assert_called_once_with("eth", pool, 10000)
        insert_swaps.assert_called_once()

    def test_cooldown_uses_fallback_instead_of_skipping_network(self):
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        settings = {
            "enabled": True, "networks": ["eth"], "pools_per_network": 1,
            "min_trade_usd": 10000, "min_net_usd": 150000,
            "auto_track_net_usd": 250000, "retention_days": 8,
            "api_delay_seconds": 1, "token_mode": "all", "token_addresses": [],
        }
        pool = {"address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 200000,
                "base_token_address": W1, "base_token_symbol": "PEPE",
                "quote_token_address": W2, "quote_token_symbol": "WETH"}
        primary = MagicMock()
        primary.hot_pools.return_value = [pool]
        fallback = MagicMock()
        fallback.pool_trades.return_value = [swap(W1, "buy", 20000, 5, 1)]

        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.store, "get_settings", return_value=settings))
            stack.enter_context(patch.object(worker, "recompute", return_value=0))
            stack.enter_context(patch.object(worker.store, "prune_swaps"))
            stack.enter_context(patch.object(worker.store, "prune_wallet_alerts"))
            stack.enter_context(patch.object(worker.store, "set_status"))
            stack.enter_context(patch.object(worker.store, "insert_swaps", return_value=1))
            stack.enter_context(patch.object(worker, "dispatch_telegram_alerts"))
            stack.enter_context(patch.object(worker, "_network_rotation_offset", 0))
            stack.enter_context(patch.dict(worker._NETWORK_COOLDOWN_UNTIL, {"eth": 9999999999}, clear=True))
            worker.run_cycle(primary, fallback_provider=fallback)

        primary.pool_trades.assert_not_called()
        fallback.pool_trades.assert_called_once_with("eth", pool, 10000)


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

    @patch("web.routes.wallet_routes.store.get_settings", side_effect=RuntimeError("private database connection detail"))
    def test_settings_errors_do_not_leak_internal_exception(self, _mock):
        response = self.client.get("/api/wallets/settings", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private database connection detail", response.text)
        self.assertEqual(response.json()["detail"], "Failed to load wallet tracker settings")

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
    def test_wallet_settings_rejects_inconsistent_thresholds(self):
        payload = {
            "enabled": True, "networks": ["eth"], "pools_per_network": 4,
            "min_trade_usd": 10000, "min_net_usd": 150000,
            "auto_track_net_usd": 100000, "poll_interval_seconds": 300,
            "retention_days": 8, "api_delay_seconds": 4.0,
            "token_mode": "all", "token_addresses": [],
        }
        response = self.client.put("/api/wallets/settings", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 422)

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

    @patch("web.routes.wallet_routes.store.toggle_wallet_tracking", return_value=True)
    def test_toggle_track_on(self, toggle_mock):
        r = self.client.post("/api/wallets/tracked/toggle", json={"chain": "eth", "wallet": W1}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["tracked"])
        toggle_mock.assert_called_once_with("eth", W1, None)

    @patch("web.routes.wallet_routes.store.toggle_wallet_tracking", return_value=False)
    def test_toggle_track_off(self, toggle_mock):
        r = self.client.post("/api/wallets/tracked/toggle", json={"chain": "eth", "wallet": W1}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["tracked"])
        toggle_mock.assert_called_once_with("eth", W1, None)

    @patch("web.routes.wallet_routes.store.get_all_positions_for_period")
    def test_token_flow(self, m):
        m.return_value = analyzer.aggregate_swaps([swap(W1, "buy", 300_000, 10, 5)], 24, NOW, 100_000)
        r = self.client.get("/api/wallets/token-flow?period_hours=24", headers=self.headers)
        self.assertEqual(r.json()["flows"][0]["signal"], "MIXED")

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



class TestWalletTrackerSingleInstanceLock(unittest.TestCase):
    def test_lock_acquisition_holds_connection_when_successful(self):
        from unittest.mock import MagicMock
        import run_wallet_tracker

        connection = MagicMock()
        connection.cursor.return_value.fetchone.return_value = (1,)
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("run_wallet_tracker.get_pool", return_value=pool):
            result = run_wallet_tracker.acquire_single_instance_lock()

        self.assertIs(result, connection)
        connection.close.assert_not_called()
        connection.cursor.return_value.execute.assert_called_once_with(
            "SELECT GET_LOCK(%s, 0)", ("signal_bot_wallet_tracker",)
        )

    def test_lock_heartbeat_checks_connection_still_owns_lock(self):
        from unittest.mock import MagicMock
        import run_wallet_tracker

        connection = MagicMock()
        connection.cursor.return_value.fetchone.return_value = (123, 123)
        self.assertTrue(run_wallet_tracker.verify_lock_connection(connection))
        connection.cursor.return_value.execute.assert_called_once_with(
            "SELECT IS_USED_LOCK(%s), CONNECTION_ID()", ("signal_bot_wallet_tracker",)
        )

        connection.cursor.return_value.fetchone.return_value = (456, 123)
        self.assertFalse(run_wallet_tracker.verify_lock_connection(connection))

    def test_lock_contention_closes_connection_and_refuses_start(self):
        from unittest.mock import MagicMock
        import run_wallet_tracker

        connection = MagicMock()
        connection.cursor.return_value.fetchone.return_value = (0,)
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("run_wallet_tracker.get_pool", return_value=pool):
            result = run_wallet_tracker.acquire_single_instance_lock()

        self.assertIsNone(result)
        connection.close.assert_called_once()



class TestBirdeyeFallbackProvider(unittest.TestCase):
    def _trade_row(self):
        return {
            "txHash": "0xabc",
            "blockUnixTime": 1791898800,
            "owner": W1,
            "volumeUSD": 25000.0,
            "from": {"address": W2, "uiAmount": 100.0, "price": 250.0},
            "to": {"address": W1, "uiAmount": 5.0, "price": 5000.0},
        }

    def test_pair_trade_parser_maps_wallet_direction_and_usd_value(self):
        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        row = self._trade_row()
        parsed = parse_birdeye_trade("eth", pool, row)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["wallet"], W1)
        self.assertEqual(parsed["side"], "buy")
        self.assertEqual(parsed["usd_value"], 25000.0)
        self.assertEqual(parsed["token_amount"], 5.0)
        self.assertEqual(parsed["price_usd"], 5000.0)
        self.assertEqual(parsed["tx_hash"], "0xabc")
        self.assertEqual(parsed["pool_address"], POOL.lower())

    def test_pair_trade_parser_uses_base_token_direction_from_v3_shape(self):
        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        row = {
            "tx_hash": "0xdef", "block_unix_time": 1791898800, "owner": W1,
            "volume_usd": 20000,
            "base": {"address": W1, "ui_amount": 4.0, "price": 5000.0, "type_swap": "from"},
            "quote": {"address": W2, "ui_amount": 2.0, "price": 10000.0, "type_swap": "to"},
        }
        parsed = parse_birdeye_trade("eth", pool, row)
        self.assertEqual(parsed["side"], "sell")
        self.assertEqual(parsed["token_amount"], 4.0)

    def test_pair_trade_parser_rejects_ambiguous_or_incomplete_rows(self):
        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        self.assertIsNone(parse_birdeye_trade("eth", pool, {"owner": W1}))
        row = self._trade_row()
        row["volumeUSD"] = None
        row["volume_usd"] = None
        row["volume_in_usd"] = None
        row["usd_value"] = None
        self.assertIsNone(parse_birdeye_trade("eth", pool, row))
        row = self._trade_row()
        row["from"]["address"] = W2
        row["to"]["address"] = W2
        self.assertIsNone(parse_birdeye_trade("eth", pool, row))

    def test_provider_calls_free_pair_trade_endpoint_with_chain_and_api_key(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"success": True, "data": {"items": [self._trade_row()]}}
        session.get.return_value = response
        provider = BirdeyeProvider(api_key="test-key", session=session)
        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        with patch("wallet_tracker.providers.time.sleep"):
            swaps = provider.pool_trades("eth", pool, 10000)
        self.assertEqual(len(swaps), 1)
        args, kwargs = session.get.call_args
        self.assertTrue(args[0].endswith("/defi/txs/pair"))
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "test-key")
        self.assertEqual(kwargs["headers"]["x-chain"], "ethereum")
        self.assertEqual(kwargs["params"]["address"], POOL)
        self.assertEqual(kwargs["params"]["tx_type"], "swap")

    def test_provider_ignores_malformed_items_container(self):
        from unittest.mock import MagicMock

        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        for malformed_items in ({"unexpected": self._trade_row()}, "not-a-list", 42):
            with self.subTest(items=malformed_items):
                session = MagicMock()
                response = MagicMock()
                response.json.return_value = {
                    "success": True, "data": {"items": malformed_items}
                }
                session.get.return_value = response
                provider = BirdeyeProvider(api_key="test-key", session=session)
                with patch("wallet_tracker.providers.time.sleep"):
                    self.assertEqual(provider.pool_trades("eth", pool, 10000), [])

    def test_provider_ignores_malformed_data_envelope(self):
        from unittest.mock import MagicMock

        pool = {"address": POOL, "name": "PEPE / WETH", "base_token_address": W1}
        for malformed_data in (None, [], "unexpected"):
            with self.subTest(data=malformed_data):
                session = MagicMock()
                response = MagicMock()
                response.json.return_value = {"success": True, "data": malformed_data}
                session.get.return_value = response
                provider = BirdeyeProvider(api_key="test-key", session=session)
                with patch("wallet_tracker.providers.time.sleep"):
                    self.assertEqual(provider.pool_trades("eth", pool, 10000), [])

    def test_provider_requires_explicit_api_key(self):
        with patch.dict("os.environ", {"BIRDEYE_API_KEY": ""}):
            with self.assertRaises(ValueError):
                BirdeyeProvider()

    def test_provider_supports_arbitrum_and_polygon_chains(self):
        from wallet_tracker.providers import BIRDEYE_CHAIN_IDS
        self.assertEqual(BIRDEYE_CHAIN_IDS.get("arbitrum"), "arbitrum")
        self.assertEqual(BIRDEYE_CHAIN_IDS.get("polygon_pos"), "polygon")


class TestDexScreenerProvider(unittest.TestCase):
    def test_hot_pools_discovers_and_normalizes_pairs(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "pairs": [
                {
                    "chainId": "ethereum",
                    "pairAddress": "0x1111111111111111111111111111111111111111",
                    "baseToken": {"address": "0xaaa", "symbol": "PEPE"},
                    "quoteToken": {"address": "0xbbb", "symbol": "WETH"},
                    "volume": {"h24": 500000.0},
                },
                {
                    "chainId": "ethereum",
                    "pairAddress": "0x2222222222222222222222222222222222222222",
                    "baseToken": {"address": "0xccc", "symbol": "UNI"},
                    "quoteToken": {"address": "0xddd", "symbol": "USDC"},
                    "volume": {"h24": 1500000.0},
                },
                {
                    "chainId": "bsc",  # Different chain, should be filtered out
                    "pairAddress": "0x3333333333333333333333333333333333333333",
                    "baseToken": {"address": "0xeee", "symbol": "CAKE"},
                    "quoteToken": {"address": "0xfff", "symbol": "USDT"},
                    "volume": {"h24": 9999999.0},
                },
            ]
        }
        session.get.return_value = response
        provider = DexScreenerProvider(session=session)
        pools = provider.hot_pools("eth", limit=5)
        self.assertEqual(len(pools), 2)
        # Sorted by volume descending
        self.assertEqual(pools[0]["name"], "UNI / USDC")
        self.assertEqual(pools[0]["volume_24h_usd"], 1500000.0)
        self.assertEqual(pools[1]["name"], "PEPE / WETH")
        self.assertEqual(pools[1]["volume_24h_usd"], 500000.0)

    def test_hot_pools_handles_network_failure(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        session.get.side_effect = Exception("Network timeout")
        provider = DexScreenerProvider(session=session)
        pools = provider.hot_pools("eth", limit=5)
        self.assertEqual(pools, [])


class TestGeckoTerminalFastFailAndCache(unittest.TestCase):
    def test_pool_trades_skips_malformed_provider_payloads(self):
        from unittest.mock import MagicMock

        provider = GeckoTerminalProvider(session=MagicMock())
        pool = {"address": POOL, "name": "PEPE / WETH"}
        for payload in (None, [], {"data": {}}, {"data": [None, "bad", {"attributes": []}]}):
            with self.subTest(payload=payload), patch.object(provider, "_get", return_value=payload):
                self.assertEqual(provider.pool_trades("eth", pool, 10000), [])

    def test_pool_trades_keeps_valid_rows_when_other_rows_are_malformed(self):
        from unittest.mock import MagicMock

        provider = GeckoTerminalProvider(session=MagicMock())
        pool = {"address": POOL, "name": "PEPE / WETH"}
        attrs = {
            "kind": "buy", "tx_from_address": W1, "volume_in_usd": "25000",
            "block_timestamp": "2026-10-13T11:55:00Z", "tx_hash": "0xabc",
            "to_token_amount": "5", "price_to_in_usd": "5000",
        }
        payload = {"data": [None, {"attributes": []}, {"attributes": attrs}]}
        with patch.object(provider, "_get", return_value=payload):
            swaps = provider.pool_trades("eth", pool, 10000)
        self.assertEqual(len(swaps), 1)
        self.assertEqual(swaps[0]["tx_hash"], "0xabc")

    def test_fast_fail_429_raises_immediately_without_sleeping(self):
        from unittest.mock import MagicMock
        import requests
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {}
        http_err = requests.exceptions.HTTPError(response=resp)
        resp.raise_for_status.side_effect = http_err
        session.get.return_value = resp

        provider = GeckoTerminalProvider(delay_seconds=0.5, session=session, fast_fail_429=True)
        with patch("wallet_tracker.providers.time.sleep") as mock_sleep:
            with self.assertRaises(requests.exceptions.HTTPError):
                provider._get("/test")
            # Should NOT sleep for the 20s or 40s retry backoff
            for call in mock_sleep.call_args_list:
                self.assertLess(call[0][0], 5.0)

    def test_hot_pools_uses_cached_pools_when_rate_limited(self):
        from unittest.mock import MagicMock
        import requests
        session = MagicMock()
        provider = GeckoTerminalProvider(delay_seconds=0.5, session=session)
        # Prepopulate cache
        cached_pool = {"address": "0x123", "name": "TEST / WETH", "volume_24h_usd": 1000.0}
        provider._pool_cache[("eth", 4)] = (0.0, [cached_pool])

        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {}
        http_err = requests.exceptions.HTTPError(response=resp)
        resp.raise_for_status.side_effect = http_err
        session.get.return_value = resp

        with patch("wallet_tracker.providers.time.sleep"):
            pools = provider.hot_pools("eth", limit=4)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["address"], "0x123")


class TestMultiSourceDiscoveryAndFailover(unittest.TestCase):
    def test_discover_pools_falls_back_to_dexscreener_on_gecko_failure(self):
        from unittest.mock import MagicMock
        gecko = MagicMock()
        gecko.hot_pools.side_effect = Exception("Gecko rate limit")
        ds = MagicMock()
        ds.hot_pools.return_value = [{"address": "0xabc", "name": "DS / WETH", "volume_24h_usd": 50000.0}]

        pools, source = worker._discover_pools("eth", 4, gecko, dexscreener_provider=ds)
        self.assertEqual(source, "DexScreener")
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["name"], "DS / WETH")

    def test_discover_pools_falls_back_to_db_when_both_apis_fail(self):
        from unittest.mock import MagicMock
        gecko = MagicMock()
        gecko.hot_pools.side_effect = Exception("Gecko 429")
        ds = MagicMock()
        ds.hot_pools.side_effect = Exception("DexScreener down")

        with patch("wallet_tracker.store.get_active_pools", return_value=[{"address": "0xdb", "name": "DB / WETH", "volume_24h_usd": 10000.0}]):
            pools, source = worker._discover_pools("eth", 4, gecko, dexscreener_provider=ds)
        self.assertEqual(source, "Database")
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["name"], "DB / WETH")

    def test_cooldown_without_birdeye_still_uses_dexscreener_pool_discovery(self):
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        settings = {
            "enabled": True, "networks": ["eth"], "pools_per_network": 1,
            "min_trade_usd": 10000, "min_net_usd": 150000,
            "auto_track_net_usd": 250000, "retention_days": 8,
            "api_delay_seconds": 1, "token_mode": "all", "token_addresses": [],
        }
        pool = {"address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 200000}
        gecko = MagicMock()
        gecko.hot_pools.return_value = []
        gecko.pool_trades.return_value = []
        dexscreener = MagicMock()
        dexscreener.hot_pools.return_value = [pool]

        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.store, "get_settings", return_value=settings))
            stack.enter_context(patch.object(worker, "recompute", return_value=0))
            stack.enter_context(patch.object(worker.store, "prune_swaps"))
            stack.enter_context(patch.object(worker.store, "prune_wallet_alerts"))
            stack.enter_context(patch.object(worker.store, "insert_swaps", return_value=0))
            stack.enter_context(patch.object(worker.store, "prune_swaps"))
            stack.enter_context(patch.object(worker.store, "set_status"))
            stack.enter_context(patch.object(worker, "dispatch_telegram_alerts"))
            stack.enter_context(patch.dict(worker._NETWORK_COOLDOWN_UNTIL, {"eth": 9999999999}, clear=True))
            stack.enter_context(patch.object(worker.time, "monotonic", return_value=100.0))
            worker.run_cycle(gecko, dexscreener_provider=dexscreener)

        dexscreener.hot_pools.assert_called_once_with("eth", settings["pools_per_network"] * 2)
        # Discovery can fall back to free sources, but trade requests must honor
        # the active GeckoTerminal cooldown when Birdeye is not configured.
        gecko.pool_trades.assert_not_called()

    def test_trade_fetch_fast_failover_to_birdeye(self):
        from unittest.mock import MagicMock
        import requests
        gecko = MagicMock()
        resp = MagicMock()
        resp.status_code = 429
        gecko.pool_trades.side_effect = requests.exceptions.HTTPError(response=resp)

        birdeye = MagicMock()
        trade_swap = {
            "chain": "eth", "wallet": W1, "pool_address": POOL, "pair": "PEPE / WETH",
            "side": "buy", "usd_value": 25000.0, "token_amount": 5.0, "price_usd": 5000.0,
            "tx_hash": "0xabc", "block_time": NOW,
        }
        birdeye.pool_trades.return_value = [trade_swap]

        pool = {"address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 500000.0}
        with patch("wallet_tracker.store.get_settings", return_value={
            "enabled": True, "networks": ["eth"], "pools_per_network": 1,
            "min_trade_usd": 10000, "min_net_usd": 150000, "retention_days": 8,
            "api_delay_seconds": 0.5, "token_mode": "all", "token_addresses": [],
        }), patch("wallet_tracker.store.insert_swaps", return_value=1) as mock_insert, \
           patch("wallet_tracker.worker.recompute", return_value=0), \
           patch("wallet_tracker.store.prune_swaps"), \
           patch("wallet_tracker.store.prune_wallet_alerts"), \
           patch("wallet_tracker.store.set_status"), \
           patch("wallet_tracker.worker.dispatch_telegram_alerts"):
            ds = MagicMock()
            gecko.hot_pools.return_value = [pool]
            worker.run_cycle(gecko, fallback_provider=birdeye, dexscreener_provider=ds)
            mock_insert.assert_called_once_with([trade_swap])


class TestWalletSettingsWithBirdeye(unittest.TestCase):
    def test_settings_preserves_masked_key(self):
        from web.routes.wallet_routes import _mask_api_key, wallets_save_settings, WalletSettingsRequest
        masked = _mask_api_key("1234567890abcdef")
        self.assertEqual(masked, "••••••••cdef")

        req = WalletSettingsRequest(
            enabled=True, networks=["eth"], pools_per_network=4,
            min_trade_usd=10000, min_net_usd=150000, auto_track_net_usd=500000,
            poll_interval_seconds=300, retention_days=8, api_delay_seconds=4.0,
            token_mode="all", token_addresses=[], birdeye_api_key="••••••••cdef",
        )
        with patch("wallet_tracker.store.get_settings", return_value={"birdeye_api_key": "1234567890abcdef", "networks": ["eth"]}), \
             patch("wallet_tracker.store.save_settings") as mock_save:
            wallets_save_settings(req)
            saved_settings = mock_save.call_args[0][0]
            self.assertEqual(saved_settings["birdeye_api_key"], "1234567890abcdef")


class TestWalletTrackerPoolDiscoveryFallback(unittest.TestCase):
    def test_empty_geckoterminal_result_uses_dexscreener_fallback(self):
        from unittest.mock import MagicMock

        primary = MagicMock()
        primary.hot_pools.return_value = []
        fallback = MagicMock()
        pool = {"address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 200000}
        fallback.hot_pools.return_value = [pool]

        pools, source = worker._discover_pools(
            "eth", 4, primary, dexscreener_provider=fallback,
        )

        self.assertEqual(pools, [pool])
        self.assertEqual(source, "DexScreener")
        primary.hot_pools.assert_called_once_with("eth", 4)
        fallback.hot_pools.assert_called_once_with("eth", 4)

    def test_empty_geckoterminal_result_uses_database_fallback_when_needed(self):
        from unittest.mock import MagicMock

        primary = MagicMock()
        primary.hot_pools.return_value = []
        fallback = MagicMock()
        fallback.hot_pools.return_value = []
        pool = {"address": POOL, "name": "PEPE / WETH", "volume_24h_usd": 200000}

        with patch.object(worker.store, "get_active_pools", return_value=[pool]) as get_active:
            pools, source = worker._discover_pools(
                "eth", 4, primary, dexscreener_provider=fallback,
            )

        self.assertEqual(pools, [pool])
        self.assertEqual(source, "Database")
        get_active.assert_called_once_with("eth", 4)
