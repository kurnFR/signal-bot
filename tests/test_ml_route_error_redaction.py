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


if __name__ == "__main__":
    unittest.main()
