"""Operator controls for continuous retraining: global switch, per-model pause, unregister."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import online_retrainer as ort


class TestRetrainingControls(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._patches = [
            patch.object(ort, "REGISTRY_PATH", tmp / "registry.json"),
            patch.object(ort, "REGISTRY_LOCK_PATH", tmp / "registry.lock"),
        ]
        for p in self._patches:
            p.start()
        self.model = ort.register_model_for_retraining("BTCUSDT", "spot", "1h", "trend_ema_v1")
        self.key = self.model["key"]

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_global_switch_roundtrip_and_status(self):
        self.assertFalse(ort.set_retraining_enabled(False))
        self.assertFalse(ort.get_retraining_status()["enabled"])
        self.assertTrue(ort.set_retraining_enabled(True))
        self.assertTrue(ort.get_retraining_status()["enabled"])

    def test_pause_and_resume_one_model(self):
        paused = ort.set_model_retraining_enabled(self.key, False)
        self.assertIs(paused["enabled"], False)
        self.assertEqual(paused["status"], "paused")
        resumed = ort.set_model_retraining_enabled(self.key, True)
        self.assertEqual(resumed["status"], "active")
        self.assertIsNone(ort.set_model_retraining_enabled("missing", False))

    def test_paused_model_is_skipped_by_cycle_even_when_forced(self):
        ort.set_model_retraining_enabled(self.key, False)
        with patch.object(ort, "fetch_ohlcv_with_features_df") as fetch, \
             patch.object(ort, "run_real_experiment") as run:
            self.assertEqual(ort._execute_retraining_cycle_unlocked(force=True), [])
        fetch.assert_not_called()
        run.assert_not_called()

    def test_unregister_removes_only_that_model(self):
        other = ort.register_model_for_retraining("ETHUSDT", "spot", "1h", "trend_ema_v1")
        self.assertTrue(ort.unregister_model_for_retraining(self.key))
        self.assertFalse(ort.unregister_model_for_retraining(self.key))
        keys = [m["key"] for m in ort.get_retraining_status()["models"]]
        self.assertEqual(keys, [other["key"]])

    def test_invalid_enabled_flag_fails_closed(self):
        data = json.loads(ort.REGISTRY_PATH.read_text())
        data["registered_models"][self.key]["enabled"] = "yes"
        ort.REGISTRY_PATH.write_text(json.dumps(data))
        self.assertFalse(ort.get_retraining_status()["enabled"])


class TestRetrainingControlRoutes(unittest.TestCase):
    def _client(self, role):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from web.app import app
        from web.auth import create_access_token
        hdr = {"Authorization": "Bearer " + create_access_token(user_id=1, username="u", role=role)}

        def auth(request):
            if request.headers.get("Authorization") != hdr["Authorization"]:
                raise HTTPException(status_code=401, detail="no")
            return {"id": 1, "username": "u", "role": role, "is_active": 1}

        p = patch("web.app.authenticate_request", side_effect=auth)
        p.start()
        self.addCleanup(p.stop)
        return TestClient(app), hdr

    def test_routes_map_results_and_enforce_roles(self):
        c, h = self._client("trader")
        with patch("ml.online_retrainer.set_retraining_enabled", return_value=False):
            self.assertEqual(c.put("/api/ml/retraining/enabled", json={"enabled": False}, headers=h).json(),
                             {"status": "ok", "enabled": False})
        with patch("ml.online_retrainer.set_model_retraining_enabled", return_value=None):
            self.assertEqual(c.patch("/api/ml/retraining/models/k", json={"enabled": False}, headers=h).status_code, 404)
        with patch("ml.online_retrainer.set_model_retraining_enabled", return_value={"key": "k", "enabled": False}):
            self.assertEqual(c.patch("/api/ml/retraining/models/k", json={"enabled": False}, headers=h).status_code, 200)
        with patch("ml.online_retrainer.unregister_model_for_retraining", return_value=True):
            self.assertEqual(c.delete("/api/ml/retraining/models/k", headers=h).status_code, 200)
        with patch("ml.online_retrainer.unregister_model_for_retraining", return_value=False):
            self.assertEqual(c.delete("/api/ml/retraining/models/k", headers=h).status_code, 404)
        v, vh = self._client("viewer")
        self.assertEqual(v.delete("/api/ml/retraining/models/k", headers=vh).status_code, 403)
        self.assertEqual(v.put("/api/ml/retraining/enabled", json={"enabled": True}, headers=vh).status_code, 403)
        self.assertEqual(v.post("/api/ml/retraining/models/k/trigger", headers=vh).status_code, 403)

    def test_register_api_removes_auto_promote_and_per_model_trigger_enforces_pause(self):
        from web.routes.ml_routes import RegisterRetrainRequest
        self.assertNotIn("auto_promote", RegisterRetrainRequest.model_fields)

        c, h = self._client("trader")
        with patch("ml.online_retrainer.register_model_for_retraining", return_value={"key": "k"}) as reg_mock:
            res = c.post(
                "/api/ml/retraining/register",
                json={"symbol": "BTCUSDT", "market": "spot", "timeframe": "1h", "strategy_name": "trend_ema_v1"},
                headers=h,
            )
            self.assertEqual(res.status_code, 200)
            self.assertIs(reg_mock.call_args.kwargs["auto_promote"], False)

        with patch("ml.online_retrainer.get_registered_model", return_value=(True, None)):
            self.assertEqual(c.post("/api/ml/retraining/models/missing/trigger", headers=h).status_code, 404)

        with patch("ml.online_retrainer.get_registered_model", return_value=(False, {"key": "k", "enabled": True})):
            self.assertEqual(c.post("/api/ml/retraining/models/k/trigger", headers=h).status_code, 409)

        with patch("ml.online_retrainer.get_registered_model", return_value=(True, {"key": "k", "enabled": False})):
            res_paused = c.post("/api/ml/retraining/models/k/trigger", headers=h)
            self.assertEqual(res_paused.status_code, 409)
            self.assertIn("paused", res_paused.json()["detail"].lower())

        with patch("ml.online_retrainer.get_registered_model", return_value=(True, {"key": "k", "enabled": True})), \
             patch("ml.online_retrainer.execute_retraining_cycle") as exec_mock:
            res_ok = c.post("/api/ml/retraining/models/k/trigger", headers=h)
            self.assertEqual(res_ok.status_code, 200)
            self.assertEqual(res_ok.json()["status"], "queued")
            exec_mock.assert_called_once_with(force=True, model_key="k")


if __name__ == "__main__":
    unittest.main()
