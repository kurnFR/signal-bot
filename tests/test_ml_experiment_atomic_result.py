"""Experiment result persistence must never truncate the previous result."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ml.result import ExperimentResult
from ml.tournament import Candidate
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

    def test_experiment_id_rejects_path_separators(self):
        for field, value in (
            ("symbol", "../outside"),
            ("market", "spot/futures"),
            ("timeframe", "1h\\\\..\\\\escape"),
            ("strategy_name", "../../outside"),
            ("model_type", "model/type"),
            ("target_type", "binary/positive"),
        ):
            values = {
                "symbol": "BTCUSDT",
                "market": "spot",
                "timeframe": "1h",
                "strategy_name": "trend_ema_v1",
                "model_type": "logistic_regression",
                "target_type": "binary_positive_r",
            }
            values[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Invalid experiment identifier component"):
                run_real_experiment._build_experiment_id(**values)

    def test_experiment_id_rejects_non_string_components(self):
        with self.assertRaisesRegex(ValueError, "Invalid experiment identifier component: symbol"):
            run_real_experiment._build_experiment_id(
                symbol=None, market="spot", timeframe="1h",
                strategy_name="trend_ema_v1", model_type="logistic_regression",
                target_type="binary_positive_r",
            )

    def test_result_payload_exposes_experiment_id_at_top_level(self):
        experiment = ExperimentResult(
            experiment_id="real-btcusdt-spot-1h-test",
            symbol="BTCUSDT",
            timeframe="1h",
            base_strategy="trend_ema_v1",
            model_type="logistic_regression",
            baseline_metrics={},
            ml_validation_metrics={},
            ml_test_metrics={},
            threshold=0.5,
        )
        candidate = Candidate(
            model_type="logistic_regression",
            model_params={},
            feature_columns=("rsi",),
            threshold_candidates=(0.5,),
        )
        score = SimpleNamespace(
            candidate=candidate,
            validation_net_pnl_r=1.0,
            validation_profit_factor=1.2,
            validation_max_drawdown_r=0.5,
            validation_trade_count=20,
        )
        result = SimpleNamespace(
            experiment=experiment,
            winner=candidate,
            ranked_validation=(score,),
            validation_by_candidate=(),
        )

        payload = run_real_experiment._result_payload(
            result, dataset_rows=30, market_rows=1000
        )

        self.assertEqual(payload["experiment_id"], experiment.experiment_id)
        self.assertEqual(
            payload["experiment"]["experiment_id"], experiment.experiment_id
        )

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
