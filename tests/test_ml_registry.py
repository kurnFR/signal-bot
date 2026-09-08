import unittest

from ml.artifacts import ModelArtifact
from ml.registry import ModelRegistry


def artifact(model_id="m1", eligible=False):
    return ModelArtifact(
        model_id=model_id, experiment_id="e1", model_type="random_forest",
        base_strategy="trend_ema", feature_columns=("rsi",),
        strategy_params={}, model_params={}, threshold=.6,
        train_metrics={}, validation_metrics={}, test_metrics={},
        eligible_for_paper=eligible,
    )


class TestMLRegistry(unittest.TestCase):
    def test_research_then_paper(self):
        registry = ModelRegistry()
        registry.add(artifact(eligible=True))
        self.assertEqual(registry.promote_to_paper("m1").status, "paper")

    def test_ineligible_cannot_be_promoted(self):
        registry = ModelRegistry()
        registry.add(artifact())
        with self.assertRaises(ValueError):
            registry.promote_to_paper("m1")

    def test_duplicate_model_is_rejected(self):
        registry = ModelRegistry()
        registry.add(artifact())
        with self.assertRaises(ValueError):
            registry.add(artifact())

    def test_retire(self):
        registry = ModelRegistry([__import__('ml.registry', fromlist=['RegistryEntry']).RegistryEntry(artifact(eligible=True), "paper")])
        self.assertEqual(registry.retire("m1").status, "retired")


if __name__ == "__main__":
    unittest.main()
