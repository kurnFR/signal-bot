import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestMLWebRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = create_access_token(user_id=1, username="admin", role="admin")
        self.headers = {"Authorization": f"Bearer {self.token}"}

        # Avoid DB-backed token revocation/user lookup in route unit tests.
        def authenticate_test_request(request):
            if request.headers.get("Authorization") != self.headers["Authorization"]:
                raise HTTPException(status_code=401, detail="Authentication credentials missing")
            return {"id": 1, "username": "admin", "role": "admin", "is_active": 1}

        self.auth_patcher = patch("web.app.authenticate_request", side_effect=authenticate_test_request)
        self.auth_patcher.start()

    def tearDown(self):
        self.auth_patcher.stop()

    def test_list_experiments_endpoint(self):
        res = self.client.get("/api/ml/experiments", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("experiments", data)
        self.assertIsInstance(data["experiments"], list)

    def test_experiment_endpoints_normalize_current_result_schema(self):
        experiment_id = "real-btcusdt-spot-1h-test"
        payload = {
            "experiment_id": experiment_id,
            "market": "spot",
            "dataset_rows": 120,
            "winner": {"model_type": "hist_gradient_boosting"},
            "experiment": {
                "experiment_id": experiment_id,
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "base_strategy": "trend_ema_v1",
                "threshold": 0.4,
                "baseline_metrics": {
                    "validation": {"total_outcome_r": 0.8},
                    "test": {"total_outcome_r": 1.25, "win_rate_pct": 60.0},
                },
                "ml_validation_metrics": {
                    "probability_diagnostics": {"roc_auc": 0.7, "brier_score": 0.18},
                },
                "ml_test_metrics": {
                    "total_outcome_r": 1.5,
                    "win_rate_pct": 65.0,
                    "trade_count": 20,
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / f"{experiment_id}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with patch("web.routes.ml_routes.ML_RESULTS_DIR", Path(tmp)):
                listed = self.client.get("/api/ml/experiments", headers=self.headers)
                detail = self.client.get(
                    f"/api/ml/experiments/{experiment_id}", headers=self.headers
                )

        self.assertEqual(listed.status_code, 200)
        summary = listed.json()["experiments"][0]
        self.assertEqual(summary["market"], "spot")
        self.assertEqual(summary["baseline_test_r"], 1.25)
        self.assertEqual(summary["roc_auc"], 0.7)
        self.assertEqual(summary["brier_score"], 0.18)

        self.assertEqual(detail.status_code, 200)
        normalized = detail.json()["experiment"]
        self.assertEqual(normalized["market"], "spot")
        self.assertEqual(normalized["baseline_test_metrics"]["total_outcome_r"], 1.25)
        self.assertEqual(normalized["probability_diagnostics"]["roc_auc"], 0.7)

    def test_list_models_endpoint(self):
        res = self.client.get("/api/ml/models", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("models", data)
        self.assertIsInstance(data["models"], list)

    @patch("paper.circuit_breaker.get_circuit_breaker_status", return_value={
        "status": "normal", "can_open_new_position": True,
        "max_concurrent_positions": 5,
    })
    def test_circuit_breakers_endpoint(self, mock_status):
        res = self.client.get("/api/paper/circuit-breakers", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertIn("can_open_new_position", data)
        self.assertIn("max_concurrent_positions", data)


if __name__ == "__main__":
    unittest.main()
