"""Regression tests for Master Data request validation and job error redaction."""
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from web.routes import master_data


class TestMasterDataRequestValidation(unittest.TestCase):
    def test_backfill_symbol_is_trimmed_and_uppercased(self):
        request = master_data.BackfillRequest(symbol=" btcusdt ")
        self.assertEqual(request.symbol, "BTCUSDT")

    def test_feature_build_symbol_is_trimmed_and_uppercased(self):
        request = master_data.FeatureBuildRequest(symbol=" ethusdt ")
        self.assertEqual(request.symbol, "ETHUSDT")

    def test_both_requests_reject_symbols_with_url_or_path_characters(self):
        for model in (master_data.BackfillRequest, master_data.FeatureBuildRequest):
            for symbol in ("BTC/USDT", "../BTC", "BTC USDT", "BTC?x=1", "B"):
                with self.subTest(model=model.__name__, symbol=symbol):
                    with self.assertRaises(ValidationError):
                        model(symbol=symbol)

    def test_backfill_feature_error_is_not_exposed_in_job_message(self):
        job = {
            "id": "job-1", "status": "queued", "progress": 0,
            "message": "", "created_at": 1.0, "updated_at": 1.0,
        }
        with (
            patch.object(master_data, "JOBS", {"job-1": job}),
            patch.object(master_data, "fetch_klines", return_value=[[1]]),
            patch.object(master_data, "klines_to_rows", return_value=[{"symbol": "BTCUSDT"}]),
            patch.object(master_data, "upsert_ohlcv"),
            patch.object(master_data, "build_one", side_effect=RuntimeError("db-password=do-not-disclose")),
            patch.object(master_data, "_persist"),
        ):
            master_data._run_backfill_job(
                "job-1", "BTCUSDT", "spot", "1h", 0.01, True
            )

        self.assertEqual(job["status"], "completed")
        self.assertIn("feature calculation failed", job["message"])
        self.assertNotIn("db-password", job["message"])
        self.assertNotIn("do-not-disclose", job["message"])


if __name__ == "__main__":
    unittest.main()
