"""Contract tests for read-only Master Data endpoints.

All database/job state is mocked; these tests must never start backfills,
feature builders, or call Binance.
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app


def user(role):
    return {"id": 1, "username": "test-user", "email": None, "role": role,
            "is_active": 1, "must_change_password": 0}


class FakeCursor:
    def __init__(self):
        self.query = ""
        self.closed = False

    def execute(self, query, params=None):
        self.query = query

    def fetchall(self):
        if "FROM ohlcv" in self.query:
            return [{
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1h",
                "candle_count": 120,
                "min_open_time": datetime(2026, 10, 1, 0, 0),
                "max_close_time": datetime(2026, 10, 5, 1, 0),
                "min_wib": datetime(2026, 10, 1, 7, 0),
                "max_wib": datetime(2026, 10, 5, 8, 0),
            }]
        if "FROM features" in self.query:
            return [{"symbol": "BTCUSDT", "market": "spot", "timeframe": "1h", "feature_count": 118}]
        raise AssertionError(f"Unexpected query in read-only contract test: {self.query}")

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("Coverage query requires dictionary cursor")
        return self.fake_cursor

    def close(self):
        self.closed = True


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def get_connection(self):
        return self.connection


class TestMasterDataReadContracts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def _get(self, path, role="viewer"):
        with patch("web.app.authenticate_request", return_value=user(role)), \\
             patch("web.auth.authenticate_request", return_value=user(role)):
            return self.client.get(path)

    def test_coverage_response_matches_frontend_contract_and_closes_connection(self):
        connection = FakeConnection()
        with patch("web.routes.master_data.get_pool", return_value=FakePool(connection)):
            response = self._get("/api/master-data/coverage")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"], {
            "totalCandles": 120, "distinctSymbols": 1, "coverageEntries": 1,
        })
        self.assertEqual(body["coverage"], [{
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "candleCount": 120,
            "featureCount": 118,
            "hasFeatures": True,
            "minOpenTime": "2026-10-01T00:00:00",
            "maxCloseTime": "2026-10-05T01:00:00",
            "startDate": "2026-10-01 07:00:00",
            "endDate": "2026-10-05 08:00:00",
        }])
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_jobs_response_merges_live_and_persisted_jobs_without_starting_work(self):
        live = {
            "id": "live-1", "type": "build_features", "symbol": "BTCUSDT",
            "market": "spot", "timeframe": "1h", "status": "running",
            "progress": 45.0, "message": "Computing features",
            "created_at": 200.0, "updated_at": 210.0,
        }
        persisted = {
            "id": "stored-1", "type": "backfill", "symbol": "ETHUSDT",
            "market": "spot", "timeframe": "4h", "status": "interrupted",
            "progress": 30.0, "message": "Interrupted by a server restart",
            "created_at": 100.0, "updated_at": 150.0,
        }
        with patch("web.routes.master_data.JOBS", {"live-1": live}), \\
             patch("web.routes.master_data.job_store.list_jobs", return_value=[persisted]):
            response = self._get("/api/master-data/jobs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([job["id"] for job in response.json()["jobs"]], ["live-1", "stored-1"])
        self.assertEqual(response.json()["jobs"][0]["progress"], 45.0)
        self.assertEqual(response.json()["jobs"][1]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
