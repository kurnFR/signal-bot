"""Inference-only runtime for independently generated ML trading signals."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from ml.generator_artifacts import (
    GENERATOR_CLASS_MAPPING,
    load_generator_artifact,
)


@lru_cache(maxsize=32)
def _load_cached_generator(model_id: str, models_dir: str, require_eligible: bool):
    return load_generator_artifact(
        model_id, models_dir, require_eligible=require_eligible
    )


def load_generator_predictor(
    model_id: str,
    models_dir: str = "ml_models",
    *,
    require_eligible: bool = False,
):
    """Load one immutable typed generator artifact and its verified model."""
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("ml_generator_model_id is required")
    return _load_cached_generator(model_id, str(models_dir), bool(require_eligible))


def predict_generator_signals(
    frame: pd.DataFrame,
    artifact: Any,
    model: Any,
) -> pd.DataFrame:
    """Return per-candle LONG/HOLD/SHORT decisions using point-in-time features.

    Missing feature values are imputed using the fitted TRAIN-only imputer.
    Infinite rows, all-missing rows, invalid probabilities, and prediction
    failures fail closed to HOLD (decision class 1).
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    features = tuple(artifact.feature_columns)
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError("generator inference is missing features: " + ", ".join(missing))

    preprocessor = getattr(model, "_signal_bot_generator_preprocessor", None)
    if not isinstance(preprocessor, tuple) or len(preprocessor) != 2:
        raise ValueError("generator model has no fitted preprocessing pipeline")
    imputer, scaler = preprocessor
    if tuple(getattr(model, "_signal_bot_generator_feature_columns", ())) != features:
        raise ValueError("generator model feature schema does not match artifact")

    output = pd.DataFrame(
        {
            "decision": np.ones(len(frame), dtype=int),
            "p_short": np.nan,
            "p_hold": np.nan,
            "p_long": np.nan,
            "confidence": 0.0,
        },
        index=frame.index,
    )
    if frame.empty:
        return output

    try:
        numeric = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
        values = numeric.to_numpy(dtype=float, na_value=np.nan)
        valid = np.isfinite(values).any(axis=1) & ~np.isinf(values).any(axis=1)
        if not valid.any():
            return output
        transformed = imputer.transform(values[valid])
        transformed = scaler.transform(transformed)
        probabilities = np.asarray(model.predict_proba(transformed), dtype=float)
        classes = np.asarray(getattr(model, "classes_", []), dtype=int)
        if set(classes.tolist()) != set(GENERATOR_CLASS_MAPPING.values()):
            return output
        if probabilities.shape != (int(valid.sum()), len(classes)):
            return output
        if not np.isfinite(probabilities).all() or (probabilities < 0).any():
            return output
        if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-5):
            return output
        ordered = np.column_stack([
            probabilities[:, int(np.where(classes == class_id)[0][0])]
            for class_id in (0, 1, 2)
        ])
        policy = artifact.decision_policy
        confidence_threshold = float(policy["min_confidence"])
        margin_threshold = float(policy["min_directional_margin"])
        short_p, hold_p, long_p = ordered[:, 0], ordered[:, 1], ordered[:, 2]
        decisions = np.ones(len(ordered), dtype=int)
        long_mask = (
            (long_p >= confidence_threshold)
            & (long_p >= hold_p)
            & ((long_p - short_p) >= margin_threshold)
        )
        short_mask = (
            (short_p >= confidence_threshold)
            & (short_p >= hold_p)
            & ((short_p - long_p) >= margin_threshold)
        )
        decisions[long_mask] = GENERATOR_CLASS_MAPPING["LONG"]
        decisions[short_mask] = GENERATOR_CLASS_MAPPING["SHORT"]
        positions = np.flatnonzero(valid)
        output.iloc[positions, output.columns.get_loc("decision")] = decisions
        output.iloc[positions, output.columns.get_loc("p_short")] = short_p
        output.iloc[positions, output.columns.get_loc("p_hold")] = hold_p
        output.iloc[positions, output.columns.get_loc("p_long")] = long_p
        output.iloc[positions, output.columns.get_loc("confidence")] = ordered.max(axis=1)
    except Exception:
        # Runtime prediction is a gate, never a reason to open a trade.
        return output

    return output
