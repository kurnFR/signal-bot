"""Persistent ML model store with strict artifact/feature validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from dataclasses import replace
import stat
import tempfile
from typing import Any

try:
    import joblib
except ImportError:  # dependency is optional until ML runtime is installed
    joblib = None

from ml.artifacts import ModelArtifact, resolve_model_artifact_paths, resolve_model_manifest_path


class ModelStore:
    """Store serialized models and JSON manifests under one configurable root."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _require_joblib():
        if joblib is None:
            raise RuntimeError("joblib is required for ML model persistence")

    def save(self, artifact: ModelArtifact, model: Any) -> tuple[Path, Path]:
        """Persist a new artifact without following links or exposing partial weights."""
        self._require_joblib()
        root = self.root.resolve()
        model_path = root / f"{artifact.model_id}.joblib"
        manifest_path = root / f"{artifact.model_id}.json"
        if any(path.exists() or path.is_symlink() for path in (model_path, manifest_path)):
            raise FileExistsError(f"model artifact already exists: {artifact.model_id}")

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=root, prefix=".model-weights-", suffix=".tmp", delete=False
            ) as handle:
                temp_path = handle.name
            # Compression keeps joblib output in one file so the atomic replace
            # cannot leave required sidecar arrays behind.
            joblib.dump(model, temp_path, compress=3)
            digest = hashlib.sha256()
            with open(temp_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                os.fsync(handle.fileno())
            artifact = replace(artifact, weights_sha256=digest.hexdigest())
            # Atomic replacement replaces a destination symlink itself instead
            # of writing through it if another process creates one after the check.
            os.replace(temp_path, model_path)
            temp_path = None
            try:
                artifact.save_manifest(root)
            except Exception:
                # The weight file was created by this call and has no manifest;
                # remove it so a failed save does not leave a misleading artifact.
                try:
                    model_path.unlink()
                except OSError:
                    pass
                raise
            return model_path, manifest_path
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def load(self, artifact: ModelArtifact, expected_features: list[str] | tuple[str, ...]):
        self._require_joblib()
        expected = tuple(expected_features)
        if tuple(artifact.feature_columns) != expected:
            raise ValueError("ML artifact feature schema does not match requested features")
        manifest_path, path = resolve_model_artifact_paths(artifact.model_id, self.root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Open once, then hash and deserialize through the same file descriptor.
        # Otherwise a writer could replace the path after digest verification but
        # before joblib.load(path), causing unverified bytes to be deserialized.
        # O_NOFOLLOW also closes the symlink-swap window on platforms that support it.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError("Model weights could not be opened safely") from exc
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Model weights must be a regular file")
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            expected_digest = manifest.get("weights_sha256") if isinstance(manifest, dict) else None
            actual_digest = digest.hexdigest()
            if (
                not isinstance(expected_digest, str)
                or len(expected_digest) != 64
                or any(char not in "0123456789abcdef" for char in expected_digest)
            ):
                raise ValueError("Model manifest must contain a valid weights_sha256 digest")
            if actual_digest != expected_digest:
                raise ValueError("Model weights SHA-256 does not match manifest")
            handle.seek(0)
            return joblib.load(handle)

    def load_manifest(self, model_id: str) -> dict[str, Any]:
        path = resolve_model_manifest_path(model_id, self.root)
        return json.loads(path.read_text(encoding="utf-8"))
