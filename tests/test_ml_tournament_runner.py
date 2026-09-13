import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from ml.tournament import Candidate
from ml.tournament_runner import run_tournament


class TestMLTournamentRunner(unittest.TestCase):
    @staticmethod
    def _dataset(n=120):
        target = (np.arange(n) % 2).astype(int)
        feature = np.where(target == 1, 1.0, -1.0) + np.linspace(-0.1, 0.1, n)
        outcome = np.where(target == 1, 1.0, -0.5)
        return pd.DataFrame({
            "open_time": np.arange(n),
            "feature": feature,
            "target": target,
            "outcome_r": outcome,
        })

    @staticmethod
    def _candidate(model_type="logistic_regression", params=None):
        return Candidate(
            model_type=model_type,
            model_params=params or {},
            feature_columns=("feature",),
            threshold_candidates=(0.50, 0.60, 0.70),
        )

    def test_empty_grid_rejected(self):
        with self.assertRaises(ValueError):
            run_tournament(
                self._dataset(), [], experiment_id="x", symbol="BTCUSDT",
                timeframe="1h", base_strategy="trend_ema_v1",
            )

    def test_candidate_limit_enforced(self):
        candidates = [
            self._candidate("logistic_regression", {"C": 0.5}),
            self._candidate("logistic_regression", {"C": 1.0}),
        ]
        with self.assertRaises(ValueError):
            run_tournament(
                self._dataset(), candidates, experiment_id="x", symbol="BTCUSDT",
                timeframe="1h", base_strategy="trend_ema_v1", max_candidates=1,
            )

    def test_tournament_locks_one_winner_and_tests_once(self):
        candidates = [
            self._candidate("logistic_regression", {"C": 0.5}),
            self._candidate("logistic_regression", {"C": 1.0}),
        ]
        with patch(
            "ml.tournament_runner.evaluate_locked_model",
            return_value=({"accuracy": 1.0}, {"selected_trades": 20}),
        ) as evaluate:
            result = run_tournament(
                self._dataset(), candidates, experiment_id="exp", symbol="BTCUSDT",
                timeframe="1h", base_strategy="trend_ema_v1", min_validation_trades=5,
            )
        self.assertEqual(len(result.ranked_validation), 2)
        self.assertEqual(evaluate.call_count, 1)
        self.assertIs(result.winner, evaluate.call_args.args[1]["candidate"])
        self.assertEqual(result.experiment.status, "research")

    def test_deterministic_winner(self):
        candidates = [
            self._candidate("logistic_regression", {"C": 0.5}),
            self._candidate("logistic_regression", {"C": 1.0}),
        ]
        first = run_tournament(
            self._dataset(), candidates, experiment_id="a", symbol="BTCUSDT",
            timeframe="1h", base_strategy="trend_ema_v1", min_validation_trades=5,
        )
        second = run_tournament(
            self._dataset(), candidates, experiment_id="b", symbol="BTCUSDT",
            timeframe="1h", base_strategy="trend_ema_v1", min_validation_trades=5,
        )
        self.assertEqual(first.winner, second.winner)
        self.assertEqual(first.experiment.threshold, second.experiment.threshold)


if __name__ == "__main__":
    unittest.main()
