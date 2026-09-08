"""Persistent ML model store with strict artifact/feature validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib

from ml.artifacts import ModelArtifact


class ModelStore:
    """Store serialized models and immutable JSON manifests under one root."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, artifact: ModelArtifact, model: Any) -> tuple[Path, Path]:
        model_path = self.root / f"{artifact.model_id}.joblib"
        manifest_path = self.root / f"{artifact.model_id}.json"
        if model_path.exists() or manifest_path.exists():
            raise FileExistsError(f"model artifact already exists: {artifact.model_id}")
        joblib.dump(model, model_path)
        artifact.save_manifest(self.root)
        return model_path, manifest_path

    def load(self, artifact: ModelArtifact, expected_features: list[str] | tuple[str, ...]):
        expected = tuple(expected_features)
        if tuple(artifact.feature_columns) != expected:
            raise ValueError("ML artifact feature schema does not match requested features")
        path = self.root / f"{artifact.model_id}.joblib"
        if not path.is_file():
            raise FileNotFoundError(f"model file not found: {artifact.model_id}")
        return joblib.load(path)

    def load_manifest(self, model_id: str) -> dict[str, Any]:
        path = self.root / f"{model_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"model manifest not found: {model_id}")
        return json.loads(path.read_text(encoding="utf-8"))
