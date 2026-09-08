"""ML signal-filter strategy adapter.

The ML model never invents an execution price or risk calculation. It only
filters an already-generated base strategy signal. The normal backtest/paper
execution and canonical accounting remain authoritative.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

import pandas as pd

from ml.models import create_model


class MLSignalFilter:
    """Fit an ML classifier and use it to filter base strategy signals."""

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

    def predict_probability(self, X: pd.DataFrame):
        if self.feature_columns is None:
            raise RuntimeError("ML filter has not been fitted")
        missing = [c for c in self.feature_columns if c not in X.columns]
        if missing:
            raise ValueError(f"missing ML features: {', '.join(missing)}")
        return self.model.predict_proba(X[list(self.feature_columns)])[:, 1]

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
