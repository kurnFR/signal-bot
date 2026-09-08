import unittest

from ml.artifacts import ModelArtifact
from ml.registry import ModelRegistry
from ml.strategy_registry import available_ml_strategies, validate_base_strategy


def make_artifact(model_id="m1", status_eligible=True):
    return ModelArtifact(
        model_id=model_id, experiment_id="e1", model_type="random_forest",
        base_strategy="trend_ema", feature_columns=("rsi",),
        strategy_params={}, model_params={}, threshold=.6,
        train_metrics={}, validation_metrics={}, test_metrics={},
        eligible_for_paper=status_eligible,
    )


class TestMLStrategyRegistry(unittest.TestCase):
    def test_menu_exposes_research_and_paper_models(self):
        registry = ModelRegistry()
        registry.add(make_artifact())
        menu = available_ml_strategies(registry)
        self.assertIn("ml:m1", menu)
        self.assertEqual(menu["ml:m1"]["base_strategy"], "trend_ema")

    def test_paper_menu_filters_research(self):
        registry = ModelRegistry()
        registry.add(make_artifact())
        self.assertEqual(available_ml_strategies(registry, paper_only=True), {})

    def test_base_strategy_must_exist(self):
        validate_base_strategy("trend_ema")
        with self.assertRaises(ValueError):
            validate_base_strategy("does_not_exist")


if __name__ == "__main__":
    unittest.main()
