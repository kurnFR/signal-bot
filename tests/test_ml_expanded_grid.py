import unittest

from ml.models import create_model, supported_models
from ml.tournament import get_expanded_model_grid, build_candidates


class TestMLExpandedGrid(unittest.TestCase):
    def test_supported_models_includes_ensembles(self):
        models = supported_models()
        self.assertIn("logistic_regression", models)
        self.assertIn("random_forest", models)
        self.assertIn("hist_gradient_boosting", models)

    def test_create_model_all_supported_types(self):
        lr = create_model("logistic_regression", {"C": 1.0})
        self.assertEqual(lr.__class__.__name__, "LogisticRegression")

        rf = create_model("random_forest", {"n_estimators": 50, "max_depth": 3})
        self.assertEqual(rf.__class__.__name__, "RandomForestClassifier")

        hgb = create_model("hist_gradient_boosting", {"learning_rate": 0.05, "max_iter": 50})
        self.assertEqual(hgb.__class__.__name__, "HistGradientBoostingClassifier")

    def test_expanded_model_grid_contains_multi_family_candidates(self):
        grid = get_expanded_model_grid()
        types = {item[0] for item in grid}
        self.assertIn("logistic_regression", types)
        self.assertIn("random_forest", types)
        self.assertIn("hist_gradient_boosting", types)
        self.assertGreaterEqual(len(grid), 7)

        candidates = build_candidates(
            model_grid=grid,
            feature_sets=[("rsi", "macd")],
            thresholds=[0.5, 0.6],
        )
        self.assertEqual(len(candidates), len(grid))


if __name__ == "__main__":
    unittest.main()
