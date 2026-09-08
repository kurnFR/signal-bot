import tempfile
import unittest

import numpy as np
import pandas as pd

from ml.artifacts import ModelArtifact
from ml.model_store import ModelStore
from ml.registry import ModelRegistry, RegistryEntry
from ml.strategy import MLSignalFilter
from ml.strategy_config import validate_ml_config


class TestMLStrategy(unittest.TestCase):
    def test_config_validation(self):
        validate_ml_config("ml_signal_filter", {
            "base_strategy": "trend_ema",
            "model_type": "logistic_regression",
            "probability_threshold": 0.65,
        })
        with self.assertRaises(ValueError):
            validate_ml_config("ml_signal_filter", {
                "base_strategy": "trend_ema",
                "model_type": "unknown",
            })

    def test_filter_trades(self):
        filt = MLSignalFilter(lambda df, p: [], "logistic_regression", threshold=0.6)
        trades = [{"direction": "LONG"}, {"direction": "SHORT"}]
        result = filt.filter_trades(trades, [0.61, 0.59])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ml_probability"], 0.61)

    def test_fit_rejects_single_class(self):
        filt = MLSignalFilter(lambda df, p: [], "logistic_regression")
        with self.assertRaises(ValueError):
            filt.fit(pd.DataFrame({"x": [1, 2]}), [1, 1])

    def test_inference_applies_training_preprocessor(self):
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler

        filt = MLSignalFilter(lambda df, p: [], "logistic_regression")
        train = pd.DataFrame({"x": [0.0, 1.0, 10.0, 11.0]})
        y = [0, 0, 1, 1]
        imputer = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        x_train = scaler.fit_transform(imputer.fit_transform(train))
        filt.model.fit(x_train, y)
        filt.model._signal_bot_preprocessor = (imputer, scaler)
        filt.feature_columns = ("x",)

        inference = pd.DataFrame({"x": [0.5, 10.5]})
        actual = filt.predict_probability(inference)
        expected_x = scaler.transform(imputer.transform(inference[["x"]]))
        expected = filt.model.predict_proba(expected_x)[:, 1]
        np.testing.assert_allclose(actual, expected)

    def _paper_artifact(self):
        return ModelArtifact(
            model_id="deployment-test-v1",
            experiment_id="exp-deployment-1",
            model_type="logistic_regression",
            base_strategy="trend_ema",
            feature_columns=("x",),
            strategy_params={},
            model_params={},
            threshold=.6,
            train_metrics={},
            validation_metrics={},
            test_metrics={
                "trade_count": 50,
                "profit_factor": 1.2,
                "max_drawdown_pct": 10.0,
                "net_pnl": 100.0,
            },
            eligible_for_paper=True,
        )

    def _trained_store(self, root, artifact):
        source = MLSignalFilter(lambda df, p: [], artifact.model_type, threshold=artifact.threshold)
        source.fit(pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0]}), [0, 0, 1, 1])
        store = ModelStore(root)
        store.save(artifact, source.model)
        return store

    def test_registry_loader_blocks_research_model_for_paper(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = self._paper_artifact()
            store = self._trained_store(d, artifact)
            registry = ModelRegistry([RegistryEntry(artifact)])
            with self.assertRaises(ValueError):
                MLSignalFilter.from_registry(
                    model_id=artifact.model_id,
                    registry=registry,
                    store=store,
                    base_strategy=lambda df, p: [],
                    expected_features=["x"],
                    paper=True,
                )

    def test_registry_loader_loads_promoted_model_without_refit(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = self._paper_artifact()
            store = self._trained_store(d, artifact)
            registry = ModelRegistry()
            registry.add(artifact)
            registry.promote_to_paper(artifact.model_id)
            loaded = MLSignalFilter.from_registry(
                model_id=artifact.model_id,
                registry=registry,
                store=store,
                base_strategy=lambda df, p: [],
                expected_features=["x"],
                paper=True,
            )
            self.assertEqual(loaded.model_id, artifact.model_id)
            self.assertEqual(loaded.feature_columns, ("x",))
            self.assertEqual(loaded.threshold, artifact.threshold)
            probabilities = loaded.predict_probability(pd.DataFrame({"x": [0.5, 2.5]}))
            self.assertEqual(len(probabilities), 2)

    def test_registry_loader_rejects_retired_model(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = self._paper_artifact()
            store = self._trained_store(d, artifact)
            registry = ModelRegistry()
            registry.add(artifact)
            registry.retire(artifact.model_id)
            with self.assertRaises(ValueError):
                MLSignalFilter.from_registry(
                    model_id=artifact.model_id,
                    registry=registry,
                    store=store,
                    base_strategy=lambda df, p: [],
                    expected_features=["x"],
                )

    def test_registry_loader_rejects_feature_schema_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = self._paper_artifact()
            store = self._trained_store(d, artifact)
            registry = ModelRegistry()
            registry.add(artifact)
            with self.assertRaises(ValueError):
                MLSignalFilter.from_registry(
                    model_id=artifact.model_id,
                    registry=registry,
                    store=store,
                    base_strategy=lambda df, p: [],
                    expected_features=["x", "missing"],
                )


if __name__ == "__main__":
    unittest.main()
