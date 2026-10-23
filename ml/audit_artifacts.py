"""Read-only inventory of ML model manifests and serialized-weight digests.

This command never deserializes model weights and never edits or repairs artifacts.
A matching adjacent SHA-256 digest checks byte consistency only; it does not prove
model provenance if both the manifest and weights can be replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_artifact_directory(directory: str | Path) -> dict[str, Any]:
    """Return a stable, metadata-only inventory without loading model weights."""
    requested_root = Path(directory)
    if not requested_root.exists() or not requested_root.is_dir():
        raise ValueError("artifact directory must exist and be a directory")
    root = requested_root.resolve()

    # Non-recursive by design: the artifact store contract places each pair at
    # the root. Never follow symlinks or traverse subdirectories.
    manifest_paths = sorted(root.glob("*.json"), key=lambda path: path.name)
    weight_paths = sorted(root.glob("*.joblib"), key=lambda path: path.name)
    manifests_by_id: dict[str, Path] = {}
    weights_by_id: dict[str, Path] = {}
    findings: list[dict[str, str]] = []

    for path in manifest_paths:
        if path.is_symlink():
            findings.append({"model_id": path.stem, "status": "symlink_manifest"})
            continue
        if not path.is_file():
            continue
        manifests_by_id[path.stem] = path

    for path in weight_paths:
        if path.is_symlink():
            findings.append({"model_id": path.stem, "status": "symlink_weights"})
            continue
        if path.is_file():
            weights_by_id[path.stem] = path

    for model_id, manifest_path in manifests_by_id.items():
        # Validate the manifest independently of its weight file so malformed
        # metadata is not misreported as merely a missing-weight condition.
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append({"model_id": model_id, "status": "invalid_manifest"})
            continue
        if not isinstance(manifest, dict):
            findings.append({"model_id": model_id, "status": "invalid_manifest"})
            continue
        if manifest.get("model_id") != model_id:
            findings.append({"model_id": model_id, "status": "manifest_id_mismatch"})
            continue
        weight_path = weights_by_id.get(model_id)
        if weight_path is None:
            findings.append({"model_id": model_id, "status": "missing_weights"})
            continue
        expected = manifest.get("weights_sha256")
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            findings.append({"model_id": model_id, "status": "missing_or_invalid_digest"})
            continue
        try:
            actual = _sha256(weight_path)
        except OSError:
            findings.append({"model_id": model_id, "status": "unreadable_weights"})
            continue
        if actual != expected:
            findings.append({"model_id": model_id, "status": "digest_mismatch"})
        else:
            findings.append({"model_id": model_id, "status": "digest_matches"})

    for model_id, weight_path in weights_by_id.items():
        if model_id not in manifests_by_id:
            findings.append({"model_id": model_id, "status": "orphan_weights"})

    findings.sort(key=lambda item: (item["model_id"], item["status"]))
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "artifact_directory": str(root),
        "weights_deserialized": False,
        "artifacts": len(set(manifests_by_id) | set(weights_by_id)),
        "counts": dict(sorted(counts.items())),
        "findings": findings,
        "note": (
            "digest_matches means only that current weight bytes match the adjacent "
            "manifest digest; it does not establish trusted provenance."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory ML artifact manifests and SHA-256 digests without deserializing weights."
    )
    parser.add_argument("--directory", default="ml_models", help="artifact root (default: ml_models)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        report = audit_artifact_directory(args.directory)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Artifact root: {report['artifact_directory']}")
        print(f"Artifacts found: {report['artifacts']}")
        for status, count in report["counts"].items():
            print(f"{status}: {count}")
        if not report["findings"]:
            print("No .json manifests or .joblib weights found.")
        print(report["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
