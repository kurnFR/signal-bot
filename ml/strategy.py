"""ML signal-filter strategy adapter.

The ML model never invents an execution price or risk calculation. It only
filters an already-generated base strategy signal. The normal backtest/paper
execution and canonical accounting remain authoritative.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

import pandas as pd

from ml.artifacts import ModelArtifact
from ml.models import create_model
from ml.model_store import ModelStore
from ml.registry import ModelRegistry


class MLSignalFilter:
    """Use a trained ML classifier to filter base strategy signals."""

    def __init__(self, base_strategy: Callable, model_type: str, model_params: Mapping[str, Any] | None = None,
                 threshold: float = 0.5):
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self.base_strategy = base_strategy
        self.model_type = model_type
        self.model_params = dict(model_params or {})
        self.threshold = threshold
        self.model = create_model(model_type, self.model_params)
        self.feature_columns: tuple[str, ...] | None = None
        self.model_id: str | None = None
        self.artifact: ModelArtifact | None = None

    @classmethod
    def from_registry(
        cls,
        *,
        model_id: str,
        registry: ModelRegistry,
        store: ModelStore,
        base_strategy: Callable,
        expected_features: list[str] | tuple[str, ...],
        paper: bool = False,
    ) -> "MLSignalFilter":
        """Load a persisted model without refitting it."""
        entry = registry.get(model_id)
        if entry.status == "retired":
            raise ValueError(f"retired ML model cannot be loaded: {model_id}")
        if paper and entry.status != "paper":
            raise ValueError(f"ML model is not promoted for paper trading: {model_id}")
        if paper and not entry.artifact.eligible_for_paper:
            raise ValueError(f"ML model is not paper eligible: {model_id}")

        artifact = entry.artifact
        expected = tuple(expected_features)
        if not expected:
            raise ValueError("expected_features must not be empty")
        if tuple(artifact.feature_columns) != expected:
            raise ValueError("ML artifact feature schema does not match requested features")

        loaded_model = store.load(artifact, expected)
        filt = cls(
            base_strategy=base_strategy,
            model_type=artifact.model_type,
            model_params=artifact.model_params,
            threshold=artifact.threshold,
        )
        filt.model = loaded_model
        filt.feature_columns = tuple(artifact.feature_columns)
        filt.model_id = artifact.model_id
        filt.artifact = artifact
        return filt

    def fit(self, X: pd.DataFrame, y) -> "MLSignalFilter":
        if X.empty:
            raise ValueError("cannot fit ML filter on an empty dataset")
        if len(X) != len(y):
            raise ValueError("X and y must have equal length")
        if len(set(int(v) for v in y)) < 2:
            raise ValueError("ML classifier requires both target classes")
        self.feature_columns = tuple(X.columns)
        self.model.fit(X, y)
        return self

    def _transform_features(self, X: pd.DataFrame):
        """Apply the exact preprocessing fitted by the training pipeline."""
        if self.feature_columns is None:
            raise RuntimeError("ML filter has not been fitted or loaded")
        values = X.loc[:, self.feature_columns]
        preprocessor = getattr(self.model, "_signal_bot_preprocessor", None)
        if preprocessor is None:
            return values
        imputer, scaler = preprocessor
        return scaler.transform(imputer.transform(values))

    def predict_probability(self, X: pd.DataFrame):
        if self.feature_columns is None:
            raise RuntimeError("ML filter has not been fitted or loaded")
        missing = [c for c in self.feature_columns if c not in X.columns]
        if missing:
            raise ValueError(f"missing ML features: {', '.join(missing)}")
        return self.model.predict_proba(self._transform_features(X))[:, 1]

    def allow(self, X: pd.DataFrame):
        return self.predict_probability(X) >= self.threshold

    def filter_trades(self, trades: list[dict], probabilities) -> list[dict]:
        if len(trades) != len(probabilities):
            raise ValueError("trades and probabilities must have equal length")
        result = []
        for trade, probability in zip(trades, probabilities):
            if float(probability) >= self.threshold:
                enriched = dict(trade)
                enriched["ml_probability"] = float(probability)
                result.append(enriched)
        return result
