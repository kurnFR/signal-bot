import unittest

from ml.metrics import probability_diagnostics
from ml.execution_tournament import _score_validation
from ml.tournament import Candidate


class TestMLProbabilityDiagnostics(unittest.TestCase):
    def test_probability_distribution_and_threshold_counts(self):
        result = probability_diagnostics(
            [0, 0, 1, 1],
            [0.20, 0.40, 0.60, 0.80],
            thresholds=(0.20, 0.50, 0.90),
        )
        self.assertEqual(result["samples"], 4)
        self.assertEqual(result["positive_rate"], 0.5)
        self.assertEqual(result["probability_min"], 0.2)
        self.assertEqual(result["probability_max"], 0.8)
        self.assertEqual(result["thresholds"][0]["selected_count"], 4)
        self.assertEqual(result["thresholds"][1]["selected_count"], 2)
        self.assertEqual(result["thresholds"][2]["selected_count"], 0)
        self.assertEqual(result["roc_auc"], 1.0)

    def test_single_class_auc_is_none(self):
        result = probability_diagnostics([0, 0, 0], [0.2, 0.3, 0.4])
        self.assertIsNone(result["roc_auc"])


class TestExecutionThresholdHandling(unittest.TestCase):
    def test_insufficient_validation_trades_is_unusable(self):
        candidate = Candidate(
            model_type="logistic_regression",
            model_params={},
            feature_columns=("feature",),
            threshold_candidates=(0.50,),
        )
        self.assertIsNone(
            _score_validation(
                [{"r_multiple": 1.0}],
                candidate,
                0.50,
                min_validation_trades=2,
            )
        )

    def test_sufficient_validation_trades_is_scored(self):
        candidate = Candidate(
            model_type="logistic_regression",
            model_params={},
            feature_columns=("feature",),
            threshold_candidates=(0.50,),
        )
        score = _score_validation(
            [{"r_multiple": 1.0}, {"r_multiple": -0.5}],
            candidate,
            0.50,
            min_validation_trades=2,
        )
        self.assertIsNotNone(score)
        self.assertEqual(score.validation_trade_count, 2)
        self.assertAlmostEqual(score.validation_net_pnl_r, 0.5)


if __name__ == "__main__":
    unittest.main()
