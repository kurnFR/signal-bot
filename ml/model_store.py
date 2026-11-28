"""Persistent ML model store with strict artifact/feature validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from dataclasses import replace
from contextlib import contextmanager
import stat
import sys
import tempfile
from typing import Any

from ml.file_lock import fsync_directory, lock_exclusive, unlock

try:
    import joblib
except ImportError:  # dependency is optional until ML runtime is installed
    joblib = None

from ml.artifacts import (
    ModelArtifact,
    read_model_manifest_file,
    resolve_model_artifact_paths,
    resolve_model_manifest_path,
)


class ModelStore:
    """Store serialized models and JSON manifests under one configurable root."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _require_joblib():
        if joblib is None:
            raise RuntimeError("joblib is required for ML model persistence")

    @contextmanager
    def _model_save_lock(self, model_id: str):
        """Serialize save/recovery for one model across processes."""
        root = self.root.resolve()
        lock_path = root / f".model-{model_id}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            lock_exclusive(descriptor)
            yield
        finally:
            try:
                unlock(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        # Single shared implementation lives in ml.file_lock; this wrapper
        # keeps existing call sites (and any external callers) working.
        fsync_directory(directory)

    def save(self, artifact: ModelArtifact, model: Any) -> tuple[Path, Path]:
        """Persist a model; the manifest is the commit marker for its weights.

        A crash after publishing weights but before publishing the manifest
        leaves an unusable orphan. Under the per-model lock, a later save may
        safely replace that regular-file orphan. Existing manifests, symlinks,
        and non-regular paths are never overwritten or followed.
        """
        self._require_joblib()
        root = self.root.resolve()
        model_path = root / f"{artifact.model_id}.joblib"
        manifest_path = root / f"{artifact.model_id}.json"

        with self._model_save_lock(artifact.model_id):
            # The manifest is published last and therefore commits the pair.
            # A manifest without weights is inconsistent and must fail closed.
            if manifest_path.exists() or manifest_path.is_symlink():
                raise FileExistsError(f"model artifact already exists: {artifact.model_id}")
            if model_path.is_symlink():
                raise FileExistsError(f"model weights path is a symlink: {artifact.model_id}")
            if model_path.exists():
                if not stat.S_ISREG(model_path.lstat().st_mode):
                    raise FileExistsError(f"model weights path is not a regular file: {artifact.model_id}")
                # No manifest means this file cannot be loaded as a valid
                # artifact. It is the recoverable crash residue from a prior
                # save, so remove it while holding the same per-model lock.
                model_path.unlink()

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=root, prefix=".model-weights-", suffix=".tmp", delete=False
                ) as handle:
                    temp_path = handle.name
                # Compression keeps joblib output in one file so the atomic
                # replace cannot leave required sidecar arrays behind.
                joblib.dump(model, temp_path, compress=3)
                digest = hashlib.sha256()
                # Open read/write, not read-only: os.fsync needs a
                # write-capable handle on Windows (FlushFileBuffers
                # requires GENERIC_WRITE), so fsync on a "rb" handle raises
                # OSError [Errno 9] Bad file descriptor there. Nothing is
                # written through this handle; it only reads the digest and
                # flushes what joblib.dump already wrote.
                with open(temp_path, "r+b") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                    os.fsync(handle.fileno())
                artifact_with_digest = replace(artifact, weights_sha256=digest.hexdigest())
                os.replace(temp_path, model_path)
                temp_path = None
                self._fsync_directory(root)

                # Manifest is the commit marker. If manifest publication fails
                # before replacement, remove the newly published orphan. If
                # replacement already happened, retain the consistent pair.
                try:
                    artifact_with_digest.save_manifest(root)
                except Exception:
                    if not (manifest_path.exists() and manifest_path.is_file()):
                        try:
                            model_path.unlink()
                            self._fsync_directory(root)
                        except OSError:
                            pass
                    raise
                self._fsync_directory(root)
                return model_path, manifest_path
            finally:
                if temp_path is not None:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    def load(
        self,
        artifact: ModelArtifact,
        expected_features: list[str] | tuple[str, ...],
        *,
        manifest: dict[str, Any] | None = None,
    ):
        """Load weights against one consistent manifest snapshot.

        Callers that already validated a manifest should pass that exact
        snapshot so eligibility checks and the digest used for verification
        cannot be split across two different manifest versions.
        """
        self._require_joblib()
        expected = tuple(expected_features)
        if tuple(artifact.feature_columns) != expected:
            raise ValueError("ML artifact feature schema does not match requested features")
        manifest_path, path = resolve_model_artifact_paths(artifact.model_id, self.root)
        if manifest is None:
            manifest = read_model_manifest_file(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("model_id") != artifact.model_id:
            raise ValueError("Model manifest ID does not match requested artifact")

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
        return read_model_manifest_file(path)
