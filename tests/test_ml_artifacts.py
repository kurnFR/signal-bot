import tempfile
import unittest
from pathlib import Path

from ml.artifacts import ModelArtifact, paper_eligibility


class TestMLArtifacts(unittest.TestCase):
    def test_manifest_round_trip(self):
        artifact = ModelArtifact(
            model_id="ml-test-v1", experiment_id="exp-1",
            model_type="random_forest", base_strategy="trend_ema",
            feature_columns=("rsi", "atr"), strategy_params={"x": 1},
            model_params={"n_estimators": 10}, threshold=0.65,
            train_metrics={"accuracy": .6}, validation_metrics={"accuracy": .61},
            test_metrics={"trade_count": 40, "profit_factor": 1.2,
                          "max_drawdown_pct": 10, "net_pnl": 100},
        )
        with tempfile.TemporaryDirectory() as d:
            path = artifact.save_manifest(d)
            self.assertTrue(Path(path).exists())

    def test_eligibility_is_conservative(self):
        good = {"trade_count": 30, "profit_factor": 1.0,
                "max_drawdown_pct": 25, "net_pnl": 0}
        self.assertTrue(paper_eligibility(test_metrics=good))
        bad = dict(good, trade_count=29)
        self.assertFalse(paper_eligibility(test_metrics=bad))


if __name__ == "__main__":
    unittest.main()
