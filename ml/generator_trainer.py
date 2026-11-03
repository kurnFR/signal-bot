"""Leakage-safe multiclass training and validation-locked policy for ML generators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ml.generator_dataset import GENERATOR_CLASSES
from ml.models import create_model, supported_models


_NON_FEATURE_COLUMNS = {
    "open_time", "entry_time", "exit_time", "label_end_time",
    "entry_price", "label_exit_price", "target", "target_name",
    "gross_return", "net_return", "forward_return",
    "long_net_return", "short_net_return", "label_reason", "is_closed",
}


@dataclass(frozen=True)
class GeneratorTrainingConfig:
    model_type: str
    feature_columns: tuple[str, ...]
    model_params: Mapping[str, Any] | None = None
    seed: int = 42
    min_validation_trades: int = 10
    confidence_candidates: tuple[float, ...] = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
    margin_candidates: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)


@dataclass(frozen=True)
class GeneratorPolicy:
    min_confidence: float
    min_directional_margin: float
    validation_trades: int
    validation_total_net_return: float
    validation_expectancy: float


@dataclass(frozen=True)
class GeneratorTrainingResult:
    model: Any
    imputer: Any
    scaler: Any
    feature_columns: tuple[str, ...]
    class_mapping: Mapping[str, int]
    policy: GeneratorPolicy
    train_metrics: Mapping[str, Any]
    validation_metrics: Mapping[str, Any]
    test_metrics: Mapping[str, Any]


def _validate_frame(frame: pd.DataFrame, features: Sequence[str], name: str) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{name} must be a non-empty DataFrame")
    required = set(features) | {
        "open_time", "exit_time", "target", "long_net_return", "short_net_return",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")
    if frame["open_time"].isna().any() or frame["exit_time"].isna().any():
        raise ValueError(f"{name} contains missing temporal boundaries")
    if not pd.to_numeric(frame["target"], errors="coerce").isin(
        list(GENERATOR_CLASSES.values())
    ).all():
        raise ValueError(f"{name}.target contains unsupported class IDs")
    for column in ("long_net_return", "short_net_return"):
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{name}.{column} must contain finite values")
    for column in features:
        if not pd.api.types.is_numeric_dtype(frame[column]):
            raise ValueError(f"{name}.{column} must be numeric")
        values = frame[column].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError(f"{name}.{column} contains infinite values")
        if frame[column].isna().all():
            raise ValueError(f"{name}.{column} is entirely missing")


def _validate_temporal_partitions(train: pd.DataFrame, validation: pd.DataFrame, test: pd.DataFrame) -> None:
    frames = (train, validation, test)
    for left, right in ((train, validation), (validation, test), (train, test)):
        if set(left["open_time"]) & set(right["open_time"]):
            raise ValueError("generator partitions must have disjoint signal timestamps")
    if int(train["exit_time"].max()) >= int(validation["open_time"].min()):
        raise ValueError("training labels must resolve before validation begins; purge overlapping outcomes")
    if int(validation["exit_time"].max()) >= int(test["open_time"].min()):
        raise ValueError("validation labels must resolve before test begins; purge overlapping outcomes")
    if not (
        int(train["open_time"].max()) < int(validation["open_time"].min())
        < int(test["open_time"].min())
    ):
        raise ValueError("train, validation, and test must be strictly chronological")


def _prepare_features(train: pd.DataFrame, validation: pd.DataFrame, test: pd.DataFrame,
                      feature_columns: tuple[str, ...]):
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for ML generator training") from exc

    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train.loc[:, feature_columns])
    x_validation = imputer.transform(validation.loc[:, feature_columns])
    x_test = imputer.transform(test.loc[:, feature_columns])
    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_validation = scaler.transform(x_validation)
    x_test = scaler.transform(x_test)
    if x_train.shape[1] != len(feature_columns):
        raise ValueError("preprocessing changed the generator feature schema")
    return x_train, x_validation, x_test, imputer, scaler


def _class_probabilities(model, matrix: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = np.asarray(getattr(model, "classes_", []), dtype=int)
    expected = np.asarray(sorted(GENERATOR_CLASSES.values()), dtype=int)
    if probabilities.ndim != 2 or probabilities.shape != (len(matrix), len(classes)):
        raise ValueError("generator model returned an invalid probability matrix")
    if set(classes.tolist()) != set(expected.tolist()):
        raise ValueError("generator model must produce probabilities for SHORT, HOLD, and LONG")
    if not np.isfinite(probabilities).all() or (probabilities < 0).any():
        raise ValueError("generator model returned invalid probabilities")
    if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-5):
        raise ValueError("generator probabilities must sum to one")
    ordered = np.column_stack([probabilities[:, int(np.where(classes == class_id)[0][0])] for class_id in expected])
    return ordered


def _decisions(probabilities: np.ndarray, confidence: float, margin: float) -> np.ndarray:
    short_p, hold_p, long_p = probabilities[:, 0], probabilities[:, 1], probabilities[:, 2]
    decisions = np.full(len(probabilities), GENERATOR_CLASSES["HOLD"], dtype=int)
    long_mask = (long_p >= confidence) & (long_p >= hold_p) & ((long_p - short_p) >= margin)
    short_mask = (short_p >= confidence) & (short_p >= hold_p) & ((short_p - long_p) >= margin)
    decisions[long_mask] = GENERATOR_CLASSES["LONG"]
    decisions[short_mask] = GENERATOR_CLASSES["SHORT"]
    return decisions


def _policy_returns(frame: pd.DataFrame, decisions: np.ndarray) -> np.ndarray:
    long_returns = frame["long_net_return"].to_numpy(dtype=float)
    short_returns = frame["short_net_return"].to_numpy(dtype=float)
    selected = np.zeros(len(frame), dtype=float)
    selected[decisions == GENERATOR_CLASSES["LONG"]] = long_returns[
        decisions == GENERATOR_CLASSES["LONG"]
    ]
    selected[decisions == GENERATOR_CLASSES["SHORT"]] = short_returns[
        decisions == GENERATOR_CLASSES["SHORT"]
    ]
    return selected[decisions != GENERATOR_CLASSES["HOLD"]]


def _trading_metrics(returns: np.ndarray, opportunities: int) -> dict[str, Any]:
    count = int(len(returns))
    positive = float(returns[returns > 0].sum()) if count else 0.0
    negative = float(-returns[returns < 0].sum()) if count else 0.0
    total = float(returns.sum()) if count else 0.0
    cumulative = np.cumsum(returns) if count else np.asarray([], dtype=float)
    peak = np.maximum.accumulate(np.concatenate(([0.0], cumulative)))[1:] if count else cumulative
    drawdown = peak - cumulative if count else cumulative
    return {
        "opportunities": int(opportunities),
        "trades": count,
        "long_trades": None,
        "short_trades": None,
        "exposure_pct": round(count / opportunities * 100.0, 4) if opportunities else 0.0,
        "total_net_return": round(total, 10),
        "expectancy": round(total / count, 10) if count else None,
        "win_rate_pct": round(float((returns > 0).mean() * 100.0), 4) if count else None,
        "profit_factor": round(positive / negative, 8) if negative > 0 else None,
        "max_drawdown": round(float(drawdown.max()), 10) if count else 0.0,
    }


def _evaluate_policy(frame: pd.DataFrame, probabilities: np.ndarray,
                     confidence: float, margin: float) -> tuple[dict[str, Any], np.ndarray]:
    decisions = _decisions(probabilities, confidence, margin)
    long_count = int((decisions == GENERATOR_CLASSES["LONG"]).sum())
    short_count = int((decisions == GENERATOR_CLASSES["SHORT"]).sum())
    returns = _policy_returns(frame, decisions)
    metrics = _trading_metrics(returns, len(frame))
    metrics["long_trades"] = long_count
    metrics["short_trades"] = short_count
    return metrics, decisions


def _select_policy(validation: pd.DataFrame, probabilities: np.ndarray,
                   config: GeneratorTrainingConfig) -> tuple[GeneratorPolicy, dict[str, Any]]:
    if config.min_validation_trades < 1:
        raise ValueError("min_validation_trades must be positive")
    best = None
    for confidence in config.confidence_candidates:
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence candidates must be between zero and one")
        for margin in config.margin_candidates:
            if not 0.0 < margin < 1.0:
                raise ValueError("margin candidates must be between zero and one")
            metrics, _ = _evaluate_policy(validation, probabilities, confidence, margin)
            if metrics["trades"] < config.min_validation_trades:
                continue
            score = (
                metrics["total_net_return"],
                metrics["expectancy"] if metrics["expectancy"] is not None else float("-inf"),
                -confidence,
                -margin,
            )
            if best is None or score > best[0]:
                best = (score, confidence, margin, metrics)
    if best is None or best[3]["total_net_return"] <= 0:
        raise ValueError("no validation-only generator policy meets minimum trades with positive net return")
    _, confidence, margin, metrics = best
    policy = GeneratorPolicy(
        min_confidence=float(confidence),
        min_directional_margin=float(margin),
        validation_trades=int(metrics["trades"]),
        validation_total_net_return=float(metrics["total_net_return"]),
        validation_expectancy=float(metrics["expectancy"]),
    )
    return policy, metrics


def train_generator(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    config: GeneratorTrainingConfig,
) -> GeneratorTrainingResult:
    """Fit on TRAIN, select the direction policy on VALIDATION, test once."""
    features = tuple(config.feature_columns)
    if not features or len(set(features)) != len(features):
        raise ValueError("feature_columns must be non-empty and unique")
    forbidden = sorted(set(features) & _NON_FEATURE_COLUMNS)
    if forbidden:
        raise ValueError("feature_columns contain target/future metadata: " + ", ".join(forbidden))
    if config.model_type not in supported_models():
        raise ValueError(f"unsupported generator model_type: {config.model_type}")
    for name, frame in (("train", train), ("validation", validation), ("test", test)):
        _validate_frame(frame, features, name)
    _validate_temporal_partitions(train, validation, test)
    if set(train["target"].astype(int).unique()) != set(GENERATOR_CLASSES.values()):
        raise ValueError("training data must contain all three generator classes")

    x_train, x_validation, x_test, imputer, scaler = _prepare_features(
        train, validation, test, features
    )
    params = dict(config.model_params or {})
    params.setdefault("random_state", config.seed)
    model = create_model(config.model_type, params)
    model.fit(x_train, train["target"].astype(int))
    if set(np.asarray(model.classes_, dtype=int).tolist()) != set(GENERATOR_CLASSES.values()):
        raise ValueError("fitted generator model does not support all three classes")

    train_probabilities = _class_probabilities(model, x_train)
    validation_probabilities = _class_probabilities(model, x_validation)
    policy, validation_metrics = _select_policy(validation, validation_probabilities, config)

    # The untouched TEST set is evaluated only after the policy is locked.
    test_probabilities = _class_probabilities(model, x_test)
    test_metrics, _ = _evaluate_policy(
        test, test_probabilities, policy.min_confidence, policy.min_directional_margin
    )
    train_metrics = {
        "rows": len(train),
        "accuracy": float((np.argmax(train_probabilities, axis=1) == train["target"].to_numpy(dtype=int)).mean()),
    }
    model._signal_bot_generator_preprocessor = (imputer, scaler)
    model._signal_bot_generator_feature_columns = features
    model._signal_bot_generator_class_mapping = dict(GENERATOR_CLASSES)
    return GeneratorTrainingResult(
        model=model,
        imputer=imputer,
        scaler=scaler,
        feature_columns=features,
        class_mapping=dict(GENERATOR_CLASSES),
        policy=policy,
        train_metrics=train_metrics,
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
    )
