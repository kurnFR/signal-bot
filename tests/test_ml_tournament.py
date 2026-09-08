import unittest

from ml.tournament import CandidateScore, build_candidates, rank_candidates


class TestMLTournament(unittest.TestCase):
    def test_build_candidates_cartesian_product(self):
        candidates = build_candidates(
            [("logistic_regression", {"C": 1.0}), ("random_forest", {"n_estimators": 100})],
            [("rsi",), ("rsi", "atr")],
            [.5, .6],
        )
        self.assertEqual(len(candidates), 4)
        self.assertEqual(candidates[1].feature_columns, ("rsi", "atr"))

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            build_candidates([("logistic_regression", {})], [("rsi",)], [0.0, .5])

    def test_rank_uses_validation_only(self):
        candidates = build_candidates([("logistic_regression", {})], [("rsi",)], [.6])
        scores = [
            CandidateScore(candidates[0], 10, 1.5, 4, 30),
            CandidateScore(candidates[0], 8, 3.0, 1, 20),
        ]
        ranked = rank_candidates(scores)
        self.assertEqual(ranked[0].validation_net_pnl_r, 10)
        # CandidateScore intentionally has no TEST/OOS fields.
        self.assertFalse(hasattr(ranked[0], "test_net_pnl_r"))


if __name__ == "__main__":
    unittest.main()
