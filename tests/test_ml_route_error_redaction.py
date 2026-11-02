"""ML API errors must not expose raw exception details."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from web.routes import ml_routes


class TestMLRouteErrorRedaction(unittest.TestCase):
    def test_experiment_execution_error_is_generic(self):
        secret = "https://api.telegram.org/bot123456:secret-token/sendMessage"
        request = ml_routes.RunExperimentRequest()
        with patch.object(ml_routes, "run_real_experiment", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.ml_routes", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                ml_routes.trigger_experiment(request)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("secret-token", raised.exception.detail)
        self.assertNotIn(secret, raised.exception.detail)
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_malformed_experiment_json_returns_generic_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "safe-exp.json").write_text("{ malformed secret-token", encoding="utf-8")
            with patch.object(ml_routes, "ML_RESULTS_DIR", Path(tmp)), \
                 self.assertLogs("web.ml_routes", level="WARNING") as captured:
                with self.assertRaises(HTTPException) as raised:
                    ml_routes.get_experiment_details("safe-exp")

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Failed to parse experiment data")
        self.assertNotIn("secret-token", str(raised.exception.detail))
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_malformed_model_manifest_returns_generic_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "safe-model.json").write_text("{ malformed secret-token", encoding="utf-8")
            request = ml_routes.DeployMLToPaperRequest(model_id="safe-model")
            with patch.object(ml_routes, "ML_MODELS_DIR", Path(tmp)), \
                 self.assertLogs("web.ml_routes", level="WARNING") as captured:
                with self.assertRaises(HTTPException) as raised:
                    ml_routes.deploy_ml_model_to_paper(request)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Failed to parse model manifest")
        self.assertNotIn("secret-token", "\n".join(captured.output))


    def test_retraining_status_redacts_persisted_exception_details(self):
        from ml import online_retrainer

        secret = "database-password=secret-token /srv/private/config.env"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            audit = root / "audit.json"
            registry.write_text(
                '{"enabled": true, "registered_models": {"BTCUSDT_spot_1h_test": {'
                '"symbol": "BTCUSDT", "status": "active"}}}',
                encoding="utf-8",
            )
            audit.write_text(
                '[{"key": "BTCUSDT_spot_1h_test", "timestamp": "2026-11-02T00:00:00Z", '
                '"status": "failed", "error": "' + secret.replace("\\", "\\\\").replace('"', '\\"') + '"}]',
                encoding="utf-8",
            )
            with patch.object(online_retrainer, "REGISTRY_PATH", registry), patch.object(online_retrainer, "AUDIT_LOG_PATH", audit):
                result = online_retrainer.get_retraining_status()

        self.assertEqual(len(result["recent_audit"]), 1)
        event = result["recent_audit"][0]
        self.assertEqual(event["status"], "failed")
        self.assertNotIn("error", event)
        self.assertEqual(event["message"], "Retraining failed; check protected server logs.")
        self.assertNotIn("secret-token", str(result))
        self.assertNotIn("/srv/private", str(result))


    def test_walk_forward_error_response_is_generic(self):
        from ml import dataset, walk_forward_engine
        from db import db

        secret = "database-password=secret-token /srv/private/model-cache"
        request = ml_routes.MLWalkForwardRequest(strategy_name="test_strategy")
        with patch.object(db, "fetch_ohlcv_with_features_df", return_value=[None] * 100), \\
             patch.object(dataset, "build_signal_dataset", return_value=[{}] * 40), \\
             patch.object(walk_forward_engine, "run_ml_walk_forward", side_effect=RuntimeError(secret)), \\
             patch.dict(ml_routes.STRATEGIES, {"test_strategy": lambda *_: []}):
            with self.assertLogs("web.ml_routes", level="ERROR"):
                with self.assertRaises(HTTPException) as raised:
                    ml_routes.execute_ml_walk_forward(request)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Walk-forward analysis failed; check server logs for diagnostics")
        self.assertNotIn("secret-token", raised.exception.detail)
        self.assertNotIn("/srv/private", raised.exception.detail)

    def test_rl_training_error_response_is_generic(self):
        secret = "database-password=secret-token /srv/private/rl-cache"
        request = ml_routes.RLTrainRequest()
        with patch("ml.rl.runner.train_rl_model", side_effect=RuntimeError(secret)), \\
             self.assertLogs("web.ml_routes", level="ERROR"):
            with self.assertRaises(HTTPException) as raised:
                ml_routes.train_rl_trading_agent(request)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "RL training failed; check server logs for diagnostics")
        self.assertNotIn("secret-token", raised.exception.detail)
        self.assertNotIn("/srv/private", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
