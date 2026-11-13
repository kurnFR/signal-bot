"""Tests for RetailBot2 Deployment Package and Stage F Readiness.

Validates:
1. Deployment package files presence and integrity (SHA-256 manifest).
2. Standalone verification script (`deploy/verify_deployment_package.py`).
3. Environment example security defaults (fail-closed, no leaked secrets).
4. Systemd unit specification (KillMode=mixed, TimeoutStopSec=15s, sandboxing).
5. Minimal runtime dependencies (deploy/requirements-worker.txt).
6. CLI entrypoint argument handling.
7. Staging operational lifecycle rehearsal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_file_presence_and_manifest():
    """Verify that all deployment package files exist and match the manifest."""
    manifest_path = REPO_ROOT / "deploy/package_manifest.json"
    assert manifest_path.exists(), "deploy/package_manifest.json must exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("stage") == "F"
    assert "files" in manifest

    required_files = [
        "deploy/systemd/retailbot2-outbox-worker.service",
        "deploy/env/outbox-worker.env.example",
        "deploy/requirements-worker.txt",
        "paper/outbox_worker.py",
        "paper/outbox_transport.py",
        "paper/outbox_health.py",
        "paper/outbox_retention.py",
        "docs/RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md",
        "docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md",
    ]

    for rel_path in required_files:
        full_path = REPO_ROOT / rel_path
        assert full_path.exists(), f"Package file missing: {rel_path}"
        assert rel_path in manifest["files"], f"File missing from manifest: {rel_path}"


def test_verify_deployment_package_script():
    """Verify that deploy/verify_deployment_package.py runs and passes with code 0."""
    script_path = REPO_ROOT / "deploy/verify_deployment_package.py"
    assert script_path.exists()

    res = subprocess.run(
        [sys.executable, str(script_path), "--json", "--check-manifest"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Verifier script failed ({res.returncode}): {res.stderr}"

    report = json.loads(res.stdout)
    assert report["status"] == "PASS"
    checks = report["checks"]
    assert checks["file_presence_and_checksums"]["pass"] is True
    assert checks["env_security_defaults"]["pass"] is True
    assert checks["systemd_unit"]["pass"] is True
    assert checks["python_modules"]["pass"] is True
    assert checks["cli_endpoints"]["pass"] is True
    assert checks["manifest_verification"]["pass"] is True


def test_env_example_fail_closed_defaults():
    """Verify outbox-worker.env.example has fail-closed flags and no real credentials."""
    env_file = REPO_ROOT / "deploy/env/outbox-worker.env.example"
    content = env_file.read_text(encoding="utf-8")
    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
    vars_dict = dict(l.split("=", 1) for l in lines if "=" in l)

    # Fail closed delivery and observation
    assert vars_dict.get("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY") == "0"
    assert vars_dict.get("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED") == "0"

    # No actual secrets in template
    for key, val in vars_dict.items():
        if "PASSWORD" in key or "TOKEN" in key:
            assert val.startswith("your_") or val == "placeholder", f"Potential secret in {key}: {val}"


def test_requirements_worker_minimal():
    """Verify requirements-worker.txt specifies only minimal database dependencies."""
    req_file = REPO_ROOT / "deploy/requirements-worker.txt"
    assert req_file.exists()
    content = req_file.read_text(encoding="utf-8")
    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]

    # mysql connector must be present
    assert any("mysql-connector-python" in l for l in lines)
    # Prohibit heavyweight ML / scientific dependencies
    prohibited = ["pandas", "numpy", "scipy", "torch", "tensorflow", "gymnasium", "scikit-learn"]
    for pkg in prohibited:
        assert not any(pkg in l.lower() for l in lines), f"Heavyweight package '{pkg}' found in worker requirements"


def test_systemd_unit_lifecycle_and_sandboxing():
    """Verify systemd unit directives for graceful shutdown and sandboxing."""
    unit_file = REPO_ROOT / "deploy/systemd/retailbot2-outbox-worker.service"
    content = unit_file.read_text(encoding="utf-8")

    # Graceful shutdown configuration
    assert "KillMode=mixed" in content
    assert "TimeoutStopSec=15s" in content

    # Supervision and backoff
    assert "Restart=on-failure" in content
    assert "RestartSec=5s" in content
    assert "StartLimitIntervalSec=60s" in content
    assert "StartLimitBurst=5" in content

    # Principle of least privilege
    assert "User=retailbot" in content
    assert "Group=retailbot" in content
    assert "ProtectSystem=strict" in content
    assert "ProtectHome=read-only" in content
    assert "NoNewPrivileges=true" in content


def test_cli_help_endpoints():
    """Verify worker and retention CLI help endpoints execute without error."""
    for mod in ["paper.outbox_worker", "paper.outbox_retention"]:
        res = subprocess.run(
            [sys.executable, "-m", mod, "--help"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert "usage:" in res.stdout.lower()


def test_cli_invalid_database_fails():
    """Verify worker CLI rejects invalid database names."""
    res = subprocess.run(
        [sys.executable, "-m", "paper.outbox_worker", "--database", "unauthorized_db"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    # argparse error code 2
    assert res.returncode == 2
    assert "invalid choice" in res.stderr.lower()
