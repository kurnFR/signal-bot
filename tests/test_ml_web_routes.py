import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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


    def test_generator_experiment_endpoint_uses_separate_research_pipeline(self):
        result = SimpleNamespace(
            artifact=SimpleNamespace(
                model_id="generator-test-0123456789abcdef",
                task_type="signal_generator_v1",
                eligible_for_paper=False,
                decision_policy={"min_confidence": 0.6, "min_directional_margin": 0.1},
            ),
            dataset_rows=500,
            train_rows=280,
            validation_rows=90,
            test_rows=80,
            validation_metrics={"evaluation_mode": "canonical_simulator_validation"},
            test_metrics={"evaluation_mode": "canonical_simulator_oos"},
        )
        with (
            patch("db.db.fetch_ohlcv_with_features_df", return_value=[object()] * 120),
            patch("ml.generator_experiment.run_generator_experiment", return_value=result) as runner,
            patch("web.routes.ml_routes.set_paper_config") as set_config,
            patch("web.routes.ml_routes.send_telegram_message") as telegram,
        ):
            response = self.client.post(
                "/api/ml/generator/run-experiment",
                json={"symbol": "BTCUSDT", "market": "spot", "timeframe": "1h"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["model_id"], result.artifact.model_id)
        self.assertEqual(payload["task_type"], "signal_generator_v1")
        self.assertFalse(payload["eligible_for_paper"])
        self.assertEqual(payload["test_metrics"]["evaluation_mode"], "canonical_simulator_oos")
        runner.assert_called_once()
        self.assertEqual(runner.call_args.kwargs["model_type"], "hist_gradient_boosting")
        set_config.assert_not_called()
        telegram.assert_not_called()

    def test_generator_experiment_endpoint_rejects_insufficient_closed_candles(self):
        with (
            patch("db.db.fetch_ohlcv_with_features_df", return_value=[object()] * 99),
            patch("ml.generator_experiment.run_generator_experiment") as runner,
        ):
            response = self.client.post(
                "/api/ml/generator/run-experiment",
                json={"symbol": "BTCUSDT", "market": "spot", "timeframe": "1h"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 400)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
