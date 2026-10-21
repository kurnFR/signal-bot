"""The ML paper-eligibility gate must be enforced by the deploy API, not just defined."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ml.artifacts import paper_eligibility, paper_eligibility_report
from web.app import app
from web.auth import create_access_token

GOOD = {"trade_count": 40, "profit_factor": 1.3, "max_drawdown_pct": 12.0, "net_pnl": 250.0}


class TestEligibilityReport(unittest.TestCase):
    def test_accepts_compute_metrics_key_names(self):
        metrics = {"total_trades": 35, "profit_factor": 1.1, "max_drawdown_pct": 9, "net_pnl": 5}
        self.assertTrue(paper_eligibility(test_metrics=metrics))

    def test_missing_or_none_metrics_fail_closed(self):
        self.assertFalse(paper_eligibility(test_metrics={}))
        self.assertFalse(paper_eligibility(test_metrics=dict(GOOD, profit_factor=None)))
        self.assertFalse(paper_eligibility(test_metrics=dict(GOOD, net_pnl=float("nan"))))
        no_dd = {k: v for k, v in GOOD.items() if k != "max_drawdown_pct"}
        self.assertFalse(paper_eligibility(test_metrics=no_dd))

    def test_each_gate_reports_a_reason(self):
        report = paper_eligibility_report(test_metrics={
            "trade_count": 3, "profit_factor": 0.8, "max_drawdown_pct": 40, "net_pnl": -1})
        self.assertFalse(report["eligible"])
        self.assertEqual(len(report["reasons"]), 4)

    def test_few_lucky_trades_are_not_eligible(self):
        # Old behaviour marked this eligible because total R > 0.
        lucky = dict(GOOD, trade_count=4, total_outcome_r=6.0)
        self.assertFalse(paper_eligibility(test_metrics=lucky))


class TestDeployEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer t"}
        self.tmp = tempfile.TemporaryDirectory()
        self.models = Path(self.tmp.name)
        patchers = [
            patch("web.app.authenticate_request", return_value={"id": 1, "username": "admin", "role": "admin", "is_active": 1}),
            patch("web.routes.ml_routes.ML_MODELS_DIR", self.models),
            patch("web.routes.ml_routes.set_paper_config", return_value={"ok": True}),
            patch("web.routes.ml_routes.sync_and_evaluate_paper_trading", return_value={}),
            patch("web.routes.ml_routes.send_telegram_message", return_value=None),
        ]
        self.mocks = [p.start() for p in patchers]
        self.patchers = patchers

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def _write(self, model_id, *, eligible=True, metrics=GOOD, weights=True):
        manifest = {
            "model_id": model_id, "base_strategy": "trend_ema_v1", "model_type": "random_forest",
            "threshold": 0.6, "eligible_for_paper": eligible, "test_metrics": metrics,
            "strategy_params": {"symbol": "BTCUSDT", "market": "spot", "timeframe": "1h"},
        }
        (self.models / f"{model_id}.json").write_text(json.dumps(manifest))
        if weights:
            (self.models / f"{model_id}.joblib").write_bytes(b"x")

    def _deploy(self, model_id):
        return self.client.post("/api/ml/deploy-to-paper", headers=self.headers,
                                json={"model_id": model_id, "send_telegram": False})

    def test_eligible_model_deploys(self):
        self._write("good-model")
        res = self._deploy("good-model")
        self.assertEqual(res.status_code, 200, res.text)
        self.mocks[2].assert_called_once()  # set_paper_config

    def test_flagged_ineligible_model_is_blocked(self):
        self._write("flagged-off", eligible=False)
        res = self._deploy("flagged-off")
        self.assertEqual(res.status_code, 409)
        self.mocks[2].assert_not_called()

    def test_edited_manifest_flag_cannot_bypass_metrics(self):
        self._write("edited", eligible=True, metrics=dict(GOOD, trade_count=5))
        res = self._deploy("edited")
        self.assertEqual(res.status_code, 409)
        self.assertIn("30", res.json()["detail"])
        self.mocks[2].assert_not_called()

    def test_negative_pnl_blocked(self):
        self._write("loser", metrics=dict(GOOD, net_pnl=-10))
        self.assertEqual(self._deploy("loser").status_code, 409)

    def test_missing_weights_blocked(self):
        self._write("noweights", weights=False)
        self.assertEqual(self._deploy("noweights").status_code, 409)

    def test_path_traversal_model_id_rejected(self):
        for bad in ("../secret", "a/b", "..", "x\\y", ""):
            res = self._deploy(bad)
            self.assertIn(res.status_code, (404, 422), bad)
        self.mocks[2].assert_not_called()

    def test_unknown_model_404(self):
        self.assertEqual(self._deploy("nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
