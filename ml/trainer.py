"""Leakage-safe training and validation selection for ML signal filters.

The trainer deliberately has no database/backtest side effects. It accepts a
prepared supervised dataset, trains only on TRAIN, selects the probability
threshold only on VALIDATION, then evaluates the locked model exactly once on
TEST. The test partition is never consulted during model or threshold
selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

from ml.experiment import ExperimentConfig
from ml.metrics import classification_metrics
from ml.models import create_model


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float
    samples: int
    selected_trades: int
    total_outcome_r: float
    expectancy_r: float | None
    win_rate_pct: float | None


@dataclass(frozen=True)
class TrainingResult:
    experiment: ExperimentConfig
    model: Any
    feature_columns: tuple[str, ...]
    threshold: ThresholdResult
    train_metrics: Mapping[str, Any]
    validation_metrics: Mapping[str, Any]
    test_metrics: Mapping[str, Any]
    test_trading: Mapping[str, Any]


def _validate_frame(df: pd.DataFrame, feature_columns: Iterable[str], *, name: str) -> None:
    required = set(feature_columns) | {"target", "outcome_r"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")
    if df.empty:
        raise ValueError(f"{name} must not be empty")
    if df["target"].isna().any():
        raise ValueError(f"{name}.target contains missing values")
    if df["outcome_r"].isna().any():
        raise ValueError(f"{name}.outcome_r contains missing values")
    if df["target"].nunique() < 2:
        raise ValueError(f"{name}.target must contain both classes")


def _prepare_features(train: pd.DataFrame, validation: pd.DataFrame, test: pd.DataFrame,
                      feature_columns: tuple[str, ...]):
    """Fit numeric imputation/scaling on TRAIN only and transform all splits."""
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for ML training") from exc

    # Median values and scaling statistics are learned exclusively from TRAIN.
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train.loc[:, feature_columns])
    x_validation = imputer.transform(validation.loc[:, feature_columns])
    x_test = imputer.transform(test.loc[:, feature_columns])

    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_validation = scaler.transform(x_validation)
    x_test = scaler.transform(x_test)
    return x_train, x_validation, x_test, imputer, scaler


def _threshold_candidates(start: float = 0.50, stop: float = 0.90, step: float = 0.05) -> tuple[float, ...]:
    values = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 4))
        current += step
    return tuple(values)


def select_validation_threshold(
    validation: pd.DataFrame,
    probabilities,
    *,
    min_trades: int = 10,
    candidates: Iterable[float] | None = None,
) -> ThresholdResult:
    """Select threshold using VALIDATION outcomes only.

    Objective: maximize total outcome R among accepted signals, subject to a
    minimum trade count. Ties prefer the lower threshold so the filter does
    not become unnecessarily selective. This is a research objective, not a
    claim of deployability; full simulator metrics remain a promotion gate.
    """
    if min_trades < 1:
        raise ValueError("min_trades must be >= 1")
    probabilities = pd.Series(probabilities, index=validation.index, dtype=float)
    if len(probabilities) != len(validation):
        raise ValueError("probabilities must have the same length as validation")
    best: ThresholdResult | None = None

    for threshold in candidates or _threshold_candidates():
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold candidates must be between 0 and 1")
        mask = probabilities >= threshold
        selected = validation.loc[mask]
        count = len(selected)
        if count < min_trades:
            continue
        total_r = float(selected["outcome_r"].sum())
        expectancy = total_r / count if count else None
        win_rate = float((selected["target"] > 0).mean() * 100.0) if count else None
        result = ThresholdResult(threshold, len(validation), count, total_r, expectancy, win_rate)
        if best is None or (result.total_outcome_r, -result.threshold) > (best.total_outcome_r, -best.threshold):
            best = result

    if best is None:
        raise ValueError("no validation threshold satisfies min_trades")
    return best


def _trading_metrics(df: pd.DataFrame, probabilities, threshold: float) -> dict[str, Any]:
    probabilities = pd.Series(probabilities, index=df.index, dtype=float)
    selected = df.loc[probabilities >= threshold]
    count = len(selected)
    total_r = float(selected["outcome_r"].sum()) if count else 0.0
    return {
        "candidate_signals": len(df),
        "selected_trades": count,
        "selection_rate_pct": round(count / len(df) * 100.0, 4) if len(df) else None,
        "total_outcome_r": round(total_r, 8),
        "expectancy_r": round(total_r / count, 8) if count else None,
        "win_rate_pct": round(float((selected["target"] > 0).mean() * 100.0), 4) if count else None,
    }


def train_experiment(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    config: ExperimentConfig,
    *,
    min_validation_trades: int = 10,
    threshold_candidates: Iterable[float] | None = None,
) -> TrainingResult:
    """Train, lock on validation, and perform one final OOS test evaluation."""
    features = tuple(config.feature_columns)
    for frame, name in ((train, "train"), (validation, "validation"), (test, "test")):
        _validate_frame(frame, features, name=name)

    # The splitter resets DataFrame indexes, so row-index overlap is not a
    # valid leakage test. When available, open_time is the canonical identity.
    if all("open_time" in frame.columns for frame in (train, validation, test)):
        train_times = set(train["open_time"])
        validation_times = set(validation["open_time"])
        test_times = set(test["open_time"])
        if train_times & validation_times or train_times & test_times or validation_times & test_times:
            raise ValueError("train/validation/test open_time rows must be disjoint")

    x_train, x_validation, x_test, imputer, scaler = _prepare_features(train, validation, test, features)
    model = create_model(config.model_type, {**config.model_params, "random_state": config.seed})
    model.fit(x_train, train["target"].astype(int))

    train_prob = model.predict_proba(x_train)[:, 1]
    validation_prob = model.predict_proba(x_validation)[:, 1]
    test_prob = model.predict_proba(x_test)[:, 1]

    # This is the only selection step. TEST probabilities are calculated only
    # after threshold/model selection and are never fed back into a decision.
    threshold = select_validation_threshold(
        validation,
        validation_prob,
        min_trades=min_validation_trades,
        candidates=threshold_candidates,
    )

    train_metrics = classification_metrics(train["target"], train_prob, threshold.threshold)
    validation_metrics = classification_metrics(validation["target"], validation_prob, threshold.threshold)
    test_metrics = classification_metrics(test["target"], test_prob, threshold.threshold)
    test_trading = _trading_metrics(test, test_prob, threshold.threshold)

    # Persist fitted preprocessing with the model object for later artifact
    # serialization. It is fitted from TRAIN only and never changes config.
    model._signal_bot_preprocessor = (imputer, scaler)
    return TrainingResult(
        experiment=config,
        model=model,
        feature_columns=features,
        threshold=threshold,
        train_metrics=train_metrics,
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        test_trading=test_trading,
    )
