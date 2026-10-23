"""Tests for the read-only ML artifact inventory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ml.audit_artifacts import audit_artifact_directory


class AuditArtifactDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_pair(self, model_id: str, weights: bytes, digest: str | None = None):
        (self.root / f"{model_id}.joblib").write_bytes(weights)
        manifest = {"model_id": model_id}
        if digest is not None:
            manifest["weights_sha256"] = digest
        (self.root / f"{model_id}.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_detects_legacy_manifest_without_digest_without_deserializing(self):
        self.write_pair("legacy-model", b"not a serialized model")
        report = audit_artifact_directory(self.root)
        self.assertEqual(report["counts"], {"missing_or_invalid_digest": 1})
        self.assertFalse(report["weights_deserialized"])

    def test_reports_matching_digest(self):
        weights = b"opaque bytes"
        self.write_pair("valid-model", weights, hashlib.sha256(weights).hexdigest())
        report = audit_artifact_directory(self.root)
        self.assertEqual(report["counts"], {"digest_matches": 1})

    def test_reports_digest_mismatch(self):
        self.write_pair("tampered-model", b"changed bytes", "0" * 64)
        report = audit_artifact_directory(self.root)
        self.assertEqual(report["counts"], {"digest_mismatch": 1})

    def test_rejects_malformed_digest_without_hashing_as_valid(self):
        for model_id, digest in (
            ("short", "abc"),
            ("uppercase", "A" * 64),
            ("non-string", None),
        ):
            with self.subTest(model_id=model_id):
                self.write_pair(model_id, b"opaque bytes", digest)
        (self.root / "non-string.json").write_text(
            json.dumps({"model_id": "non-string", "weights_sha256": 123}),
            encoding="utf-8",
        )
        report = audit_artifact_directory(self.root)
        self.assertEqual(report["counts"], {"missing_or_invalid_digest": 3})

    def test_reports_missing_weights_and_orphan_weights(self):
        (self.root / "no-weights.json").write_text(
            json.dumps({"model_id": "no-weights", "weights_sha256": "0" * 64}),
            encoding="utf-8",
        )
        (self.root / "orphan.joblib").write_bytes(b"opaque")
        report = audit_artifact_directory(self.root)
        self.assertEqual(
            report["counts"],
            {"missing_weights": 1, "orphan_weights": 1},
        )

    def test_reports_invalid_json_non_object_and_manifest_id_mismatch(self):
        (self.root / "broken.json").write_text("{", encoding="utf-8")
        (self.root / "scalar.json").write_text(json.dumps(["not", "an object"]), encoding="utf-8")
        (self.root / "other.json").write_text(
            json.dumps({"model_id": "not-other"}), encoding="utf-8"
        )
        (self.root / "other.joblib").write_bytes(b"opaque")
        report = audit_artifact_directory(self.root)
        self.assertEqual(
            report["counts"],
            {
                "invalid_manifest": 2,
                "manifest_id_mismatch": 1,
            },
        )

    def test_reports_symlinked_manifest_and_weights_without_following_them(self):
        (self.root / "outside.json").write_text(
            json.dumps({"model_id": "linked-manifest"}), encoding="utf-8"
        )
        (self.root / "outside.bin").write_bytes(b"opaque")
        try:
            (self.root / "linked-manifest.json").symlink_to(self.root / "outside.json")
            (self.root / "linked-weights.joblib").symlink_to(self.root / "outside.bin")
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are unavailable in this environment: {exc}")

        report = audit_artifact_directory(self.root)
        self.assertEqual(
            report["counts"],
            {"symlink_manifest": 1, "symlink_weights": 1},
        )
        self.assertFalse(report["weights_deserialized"])

    def test_rejects_nonexistent_directory(self):
        with self.assertRaisesRegex(ValueError, "must exist"):
            audit_artifact_directory(self.root / "missing")


if __name__ == "__main__":
    unittest.main()
