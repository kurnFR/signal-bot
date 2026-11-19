import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.app import app
from web.routes import ml_routes
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

    def test_list_models_skips_retraining_registry_and_non_manifest_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            # Create a retraining registry file
            registry_file = tmp_path / "retraining_registry.json"
            registry_file.write_text(json.dumps({"last_run": "2026-11-06T00:00:00", "entries": []}), encoding="utf-8")

            # Create an arbitrary non-manifest JSON
            misc_file = tmp_path / "other_operational_data.json"
            misc_file.write_text(json.dumps({"some_key": "some_value"}), encoding="utf-8")

            # Create a valid model manifest
            valid_manifest = tmp_path / "valid-model-1.json"
            valid_manifest.write_text(json.dumps({
                "model_id": "valid-model-1",
                "experiment_id": "exp-1",
                "model_type": "random_forest",
                "base_strategy": "trend_ema_v1",
                "feature_columns": ["f1", "f2"],
                "strategy_params": {},
                "model_params": {},
                "threshold": 0.5,
                "train_metrics": {},
                "validation_metrics": {},
                "test_metrics": {"total_trades": 35, "profit_factor": 1.5, "max_drawdown": 0.15, "net_pnl": 500},
                "eligible_for_paper": True,
                "evaluation_protocol_version": "purged_exit_time_v1"
            }), encoding="utf-8")

            with patch("web.routes.ml_routes.ML_MODELS_DIR", tmp_path):
                res = self.client.get("/api/ml/models", headers=self.headers)
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("models", data)
                model_ids = [m["model_id"] for m in data["models"]]
                self.assertIn("valid-model-1", model_ids)
                self.assertNotIn("retraining_registry", model_ids)
                self.assertNotIn("other_operational_data", model_ids)
                for model in data["models"]:
                    self.assertIsInstance(model.get("model_id"), str)
                    self.assertTrue(len(model["model_id"]) > 0)

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
            patch("db.db.fetch_ohlcv_with_features_df", return_value=[object()] * 500),
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
        from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT
        self.assertEqual(runner.call_args.kwargs["label_config"]["fee_rate"], BACKTEST_FEE_PCT)
        self.assertEqual(runner.call_args.kwargs["label_config"]["slippage_rate"], BACKTEST_SLIPPAGE_PCT)
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


def test_leaderboard_filters_incomplete_oos_results_and_recovers_legacy_counts(tmp_path):
    def save(name, metrics, market="spot"):
        experiment = {"symbol": "BTCUSDT", "timeframe": "1h", "base_strategy": "trend_ema_v1",
                      "threshold": 0.35, "baseline_metrics": {"total_outcome_r": -50.27},
                      "ml_test_metrics": metrics}
        payload = {"experiment": experiment, "market": market, "winner": {"model_type": "test"}}
        (tmp_path / f"{name}.json").write_text(json.dumps(payload))

    metrics = {"win_rate_pct": 32, "total_outcome_r": -8.41}
    for key in ("trade_count", "selected_trades", "total_trades"):
        save(key, {key: 25, **metrics})
    save("nested", {"trading": {"selected_trades": 25, **metrics}})
    save("unknown-market", {"trade_count": 25, **metrics}, market=None)
    save("missing-count", metrics)
    save("zero-count", {"trade_count": 0, **metrics})
    save("conflicting-counts", {"trade_count": 0, "selected_trades": 25, **metrics})
    save("validation-only", {"candidate_signals": 25, **metrics})
    save("missing-rate", {"trade_count": 25, "total_outcome_r": 1})
    save("zero-rate", {"trade_count": 4, "win_rate_pct": 0, "total_outcome_r": -4})
    with patch.object(ml_routes, "ML_RESULTS_DIR", tmp_path):
        rows = {row["experiment_id"]: row for row in ml_routes.list_experiments()["experiments"]}
    assert set(rows) == {"trade_count", "selected_trades", "total_trades", "nested", "zero-rate"}
    assert rows["nested"]["ml_test_trades"] == 25
    assert rows["nested"]["ml_test_win_rate"] == 32
    assert rows["nested"]["baseline_test_win_rate"] is None
    assert rows["zero-rate"]["ml_test_win_rate"] == 0
