import unittest

from ml.artifacts import ModelArtifact
from ml.registry import ModelRegistry, RegistryEntry


def artifact(model_id="m1", eligible=False, test_metrics=None):
    if test_metrics is None:
        test_metrics = (
            {"trade_count": 40, "profit_factor": 1.3, "max_drawdown_pct": 12.0, "net_pnl": 250.0}
            if eligible else {}
        )
    return ModelArtifact(
        model_id=model_id, experiment_id="e1", model_type="random_forest",
        base_strategy="trend_ema", feature_columns=("rsi",),
        strategy_params={}, model_params={}, threshold=.6,
        train_metrics={}, validation_metrics={}, test_metrics=test_metrics,
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

    def test_eligible_flag_cannot_bypass_bad_or_nonfinite_metrics(self):
        registry = ModelRegistry()
        registry.add(artifact(
            eligible=True,
            test_metrics={"trade_count": 40, "profit_factor": float("inf"),
                          "max_drawdown_pct": 12.0, "net_pnl": 250.0},
        ))
        with self.assertRaises(ValueError):
            registry.promote_to_paper("m1")

    def test_paper_registry_entry_rechecks_metrics(self):
        with self.assertRaises(ValueError):
            RegistryEntry(
                artifact(eligible=True, test_metrics={"trade_count": 40, "profit_factor": 1.3,
                                                       "max_drawdown_pct": -1, "net_pnl": 250.0}),
                "paper",
            )

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
