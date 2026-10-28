"""Contract tests for Binance market search and popular-pair endpoints.

All upstream HTTP calls and market data are mocked; no test reaches Binance.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app
from web.routes import search as crypto_routes


def _user(role="viewer"):
    return {
        "id": 1, "username": "test-user", "email": None, "role": role,
        "is_active": 1, "must_change_password": 0,
    }


def _ticker(symbol, market, quote_volume, price=100.0, change=1.25):
    return {
        "symbol": symbol,
        "baseAsset": symbol[:-4],
        "quoteAsset": "USDT",
        "market": market,
        "lastPrice": price,
        "priceChangePercent": change,
        "highPrice": price * 1.1,
        "lowPrice": price * 0.9,
        "volume": quote_volume / price if price else 0.0,
        "quoteVolume": quote_volume,
    }


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class TestCryptoSearchContracts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _get(self, path):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.auth.authenticate_request", return_value=_user()):
            return self.client.get(path)

    def test_search_default_contract_merges_and_sorts_spot_and_futures(self):
        spot = [_ticker("BTCUSDT", "spot", 100), _ticker("ETHUSDT", "spot", 300)]
        futures = [_ticker("SOLUSDT", "futures", 200)]
        with patch.object(crypto_routes, "get_spot_tickers", return_value=spot), \
             patch.object(crypto_routes, "get_futures_tickers", return_value=futures):
            response = self._get("/api/crypto/search")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"query", "market", "count", "results"})
        self.assertEqual((body["query"], body["market"], body["count"]), ("", "all", 3))
        self.assertEqual([item["symbol"] for item in body["results"]], ["ETHUSDT", "SOLUSDT", "BTCUSDT"])
        expected_ticker_fields = {
            "symbol", "baseAsset", "quoteAsset", "market", "lastPrice",
            "priceChangePercent", "highPrice", "lowPrice", "volume", "quoteVolume",
        }
        self.assertEqual(set(body["results"][0]), expected_ticker_fields)

    def test_search_respects_market_query_and_limit(self):
        spot = [_ticker("ETHUSDT", "spot", 400), _ticker("ETHBTC", "spot", 900)]
        futures = [_ticker("ETHUSDT", "futures", 300), _ticker("BTCUSDT", "futures", 800)]
        with patch.object(crypto_routes, "get_spot_tickers", return_value=spot) as spot_mock, \
             patch.object(crypto_routes, "get_futures_tickers", return_value=futures) as futures_mock:
            response = self._get("/api/crypto/search?q=eth&market=spot&limit=1")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["query"], "eth")
        self.assertEqual(body["market"], "spot")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["symbol"], "ETHUSDT")
        spot_mock.assert_called_once_with()
        futures_mock.assert_not_called()

    def test_search_rejects_unknown_market(self):
        with patch.object(crypto_routes, "get_spot_tickers") as spot_mock, \
             patch.object(crypto_routes, "get_futures_tickers") as futures_mock:
            response = self._get("/api/crypto/search?market=margin")

        self.assertEqual(response.status_code, 422)
        spot_mock.assert_not_called()
        futures_mock.assert_not_called()

    def test_popular_endpoint_returns_top_fifteen_for_requested_market(self):
        spot = [_ticker(f"COIN{i}USDT", "spot", 1000 - i) for i in range(20)]
        futures = [_ticker(f"FUT{i}USDT", "futures", 2000 - i) for i in range(18)]
        with patch.object(crypto_routes, "get_spot_tickers", return_value=spot) as spot_mock, \
             patch.object(crypto_routes, "get_futures_tickers", return_value=futures) as futures_mock:
            spot_response = self._get("/api/crypto/popular")
            futures_response = self._get("/api/crypto/popular?market=futures")

        self.assertEqual(spot_response.status_code, 200)
        self.assertEqual(spot_response.json()["market"], "spot")
        self.assertEqual(len(spot_response.json()["items"]), 15)
        self.assertEqual(futures_response.status_code, 200)
        self.assertEqual(futures_response.json()["market"], "futures")
        self.assertEqual(len(futures_response.json()["items"]), 15)
        self.assertEqual(futures_response.json()["items"][0]["symbol"], "FUT0USDT")
        self.assertEqual(spot_mock.call_count, 1)
        self.assertEqual(futures_mock.call_count, 1)

    def test_spot_ticker_parser_filters_usdt_and_normalizes_numeric_fields(self):
        raw = [
            {"symbol": "ETHBTC", "lastPrice": "1", "priceChangePercent": "0", "highPrice": "1", "lowPrice": "1", "volume": "1", "quoteVolume": "1"},
            {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "2.5", "highPrice": "110", "lowPrice": "90", "volume": "200", "quoteVolume": "20000"},
            {"symbol": "ETHUSDT", "lastPrice": "50", "priceChangePercent": "-1.5", "highPrice": "55", "lowPrice": "45", "volume": "1000", "quoteVolume": "50000"},
        ]
        cache = {"timestamp": 0, "data": []}
        with patch.object(crypto_routes, "_SPOT_CACHE", cache), \
             patch.object(crypto_routes.time, "time", return_value=1000), \
             patch.object(crypto_routes.requests, "get", return_value=_FakeResponse(raw)) as get:
            result = crypto_routes.get_spot_tickers()

        self.assertEqual([item["symbol"] for item in result], ["ETHUSDT", "BTCUSDT"])
        self.assertEqual(result[0]["baseAsset"], "ETH")
        self.assertEqual(result[0]["market"], "spot")
        self.assertEqual(result[0]["lastPrice"], 50.0)
        self.assertEqual(result[0]["priceChangePercent"], -1.5)
        get.assert_called_once_with("https://api.binance.com/api/v3/ticker/24hr", timeout=8)
        self.assertEqual(cache["timestamp"], 1000)

    def test_stale_cache_is_returned_on_upstream_failure_and_empty_cache_is_safe(self):
        stale = [_ticker("BTCUSDT", "spot", 100)]
        with patch.object(crypto_routes, "_SPOT_CACHE", {"timestamp": 0, "data": stale}), \
             patch.object(crypto_routes.time, "time", return_value=crypto_routes.CACHE_TTL + 1), \
             patch.object(crypto_routes.requests, "get", side_effect=TimeoutError("upstream unavailable")):
            self.assertEqual(crypto_routes.get_spot_tickers(), stale)

        with patch.object(crypto_routes, "_FUTURES_CACHE", {"timestamp": 0, "data": []}), \
             patch.object(crypto_routes.time, "time", return_value=crypto_routes.CACHE_TTL + 1), \
             patch.object(crypto_routes.requests, "get", side_effect=TimeoutError("upstream unavailable")):
            self.assertEqual(crypto_routes.get_futures_tickers(), [])


if __name__ == "__main__":
    unittest.main()
