"""Experiment result persistence must never truncate the previous result."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import run_real_experiment


class TestMLExperimentAtomicResult(unittest.TestCase):
    def test_experiment_ids_are_unique_and_fit_paper_config_limit(self):
        first = run_real_experiment._build_experiment_id(
            symbol="BTCUSDT", market="spot", timeframe="1h",
            strategy_name="trend_ema_v1", model_type="logistic_regression",
            target_type="binary_positive_r",
        )
        second = run_real_experiment._build_experiment_id(
            symbol="BTCUSDT", market="spot", timeframe="1h",
            strategy_name="trend_ema_v1", model_type="logistic_regression",
            target_type="binary_positive_r",
        )
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("real-btcusdt-spot-1h-trend_ema_v1-logistic_regression-"))
        self.assertLessEqual(len(first), 100)
        self.assertRegex(first.rsplit("-", 1)[-1], r"^[a-f0-9]{12}$")

    def test_atomic_result_write_replaces_complete_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "experiment.json"
            target.write_text('{"old": true}', encoding="utf-8")

            run_real_experiment._atomic_write_text(target, '{"new": true}\n')

            self.assertEqual(target.read_text(encoding="utf-8"), '{"new": true}\n')
            self.assertEqual(list(Path(tmp).glob(".experiment.json.*.tmp")), [])

    def test_failed_replace_preserves_previous_experiment_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "experiment.json"
            target.write_text('{"last_valid": true}', encoding="utf-8")

            with patch.object(run_real_experiment.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    run_real_experiment._atomic_write_text(target, '{"incomplete":')

            self.assertEqual(target.read_text(encoding="utf-8"), '{"last_valid": true}')
            self.assertEqual(list(Path(tmp).glob(".experiment.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
