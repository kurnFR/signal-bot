#!/usr/bin/env python3
"""RetailBot2 Deployment Package Verification Script.

Stage F Production Deployment Readiness Verification Tool.
Verifies the integrity, security defaults, syntax, imports, and systemd unit
definitions of all artifacts in the RetailBot2 Telegram Outbox deployment package.

Usage:
    python3 deploy/verify_deployment_package.py [--json] [--write-manifest] [--check-manifest]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Base repository root is determined relative to this script
REPO_ROOT = Path(__file__).resolve().parent.parent

# Core deployment package files and documentation
PACKAGE_FILES = [
    "deploy/systemd/retailbot2-outbox-worker.service",
    "deploy/env/outbox-worker.env.example",
    "deploy/requirements-worker.txt",
    "paper/outbox_store.py",
    "paper/outbox_worker.py",
    "paper/outbox_transport.py",
    "paper/outbox_health.py",
    "paper/outbox_retention.py",
    "docs/RETAILBOT2_OUTBOX_OPERATIONAL_RUNBOOK.md",
    "docs/RETAILBOT2_AND_MODERN_UI_RELEASE_GATES.md",
]

# Required Python modules
REQUIRED_MODULES = [
    "paper.outbox_store",
    "paper.outbox_worker",
    "paper.outbox_transport",
    "paper.outbox_health",
    "paper.outbox_retention",
]


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_file_presence() -> Tuple[bool, Dict[str, Any]]:
    """Check that all required package files exist and compute SHA-256."""
    results = {}
    all_ok = True
    for rel_path in PACKAGE_FILES:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            results[rel_path] = {"present": False, "sha256": None, "bytes": 0}
            all_ok = False
        else:
            size = full_path.stat().st_size
            sha = compute_sha256(full_path)
            results[rel_path] = {"present": True, "sha256": sha, "bytes": size}
    return all_ok, results


def check_env_security_defaults() -> Tuple[bool, List[str]]:
    """Verify outbox-worker.env.example has safe fail-closed defaults and no secrets."""
    env_file = REPO_ROOT / "deploy/env/outbox-worker.env.example"
    errors = []
    if not env_file.exists():
        return False, ["deploy/env/outbox-worker.env.example missing"]

    content = env_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    config: Dict[str, str] = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()

    # Delivery authorization must default to 0 (master switch + deprecated alias)
    if config.get("TELEGRAM_DELIVERY_ENABLED") != "0":
        errors.append(f"TELEGRAM_DELIVERY_ENABLED must be '0', got {config.get('TELEGRAM_DELIVERY_ENABLED')}")
    if config.get("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY") != "0":
        errors.append(f"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY must be '0', got {config.get('RETAILBOT2_ALLOW_TELEGRAM_DELIVERY')}")

    # Observation authorization must default to 0
    if config.get("RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED") != "0":
        errors.append(f"RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED must be '0', got {config.get('RETAILBOT2_PRODUCTION_OUTBOX_OBSERVATION_ENABLED')}")

    # Ensure no actual credentials or tokens are committed
    for key, val in config.items():
        if "PASSWORD" in key or "TOKEN" in key:
            if val and not (val.startswith("your_") or val == "placeholder"):
                errors.append(f"Potential real credential in template: {key}={val[:4]}***")

    return len(errors) == 0, errors


def check_systemd_unit() -> Tuple[bool, List[str]]:
    """Verify systemd unit directives and run systemd-analyze if available."""
    unit_file = REPO_ROOT / "deploy/systemd/retailbot2-outbox-worker.service"
    errors = []
    if not unit_file.exists():
        return False, ["Systemd unit file missing"]

    content = unit_file.read_text(encoding="utf-8")

    # Essential structural checks
    required_directives = [
        "Description=",
        "User=retailbot",
        "Group=retailbot",
        "WorkingDirectory=/opt/signal-bot",
        "EnvironmentFile=-/etc/retailbot2/outbox-worker.env",
        "KillMode=mixed",
        "TimeoutStopSec=30s",
        "Restart=on-failure",
        "StartLimitIntervalSec=",
        "StartLimitBurst=",
        "WantedBy=multi-user.target",
    ]
    for directive in required_directives:
        if directive not in content:
            errors.append(f"Missing directive in systemd unit: '{directive}'")

    # Default startup must be explicitly observation-only (WO71786)
    exec_start_lines = [l for l in content.splitlines() if l.strip().startswith("ExecStart=")]
    if not exec_start_lines:
        errors.append("Missing ExecStart directive in systemd unit")
    else:
        exec_start = exec_start_lines[0]
        if "--observation" not in exec_start:
            errors.append("ExecStart in systemd unit must include '--observation' for default observation-only startup")

    # Syntax check via systemd-analyze verify if installed
    try:
        proc = subprocess.run(
            ["systemd-analyze", "verify", str(unit_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        # Note: on dev hosts /opt/signal-bot may not exist yet; filter expected missing binary notice
        stderr_lines = [l for l in proc.stderr.splitlines() if l.strip()]
        for line in stderr_lines:
            if "is not executable: No such file or directory" in line:
                # Expected on staging/development host before package deployment to /opt/signal-bot
                continue
            if "Unknown lvalue" in line or "Failed to parse" in line or "Invalid" in line:
                errors.append(f"systemd-analyze syntax error: {line}")
    except FileNotFoundError:
        # systemd-analyze not installed in current environment; directive checks sufficient
        pass

    return len(errors) == 0, errors


def check_python_modules() -> Tuple[bool, List[str]]:
    """Verify required Python modules can be imported without error."""
    errors = []
    sys_path_saved = list(sys.path)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        for mod_name in REQUIRED_MODULES:
            try:
                __import__(mod_name)
            except Exception as e:
                errors.append(f"Failed to import {mod_name}: {e}")
    finally:
        sys.path = sys_path_saved

    return len(errors) == 0, errors


def check_cli_invocations() -> Tuple[bool, List[str]]:
    """Verify that CLI help endpoints execute cleanly with return code 0."""
    errors = []
    commands = [
        [sys.executable, "-m", "paper.outbox_worker", "--help"],
        [sys.executable, "-m", "paper.outbox_retention", "--help"],
    ]
    for cmd in commands:
        try:
            res = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                errors.append(f"Command {' '.join(cmd)} failed with code {res.returncode}: {res.stderr}")
        except Exception as e:
            errors.append(f"Command {' '.join(cmd)} raised exception: {e}")

    return len(errors) == 0, errors


def run_all_checks() -> Dict[str, Any]:
    """Execute all verification checks and return structured results."""
    files_ok, file_data = check_file_presence()
    env_ok, env_errors = check_env_security_defaults()
    unit_ok, unit_errors = check_systemd_unit()
    modules_ok, module_errors = check_python_modules()
    cli_ok, cli_errors = check_cli_invocations()

    overall_pass = all([files_ok, env_ok, unit_ok, modules_ok, cli_ok])

    return {
        "status": "PASS" if overall_pass else "FAIL",
        "checks": {
            "file_presence_and_checksums": {
                "pass": files_ok,
                "files": file_data,
            },
            "env_security_defaults": {
                "pass": env_ok,
                "errors": env_errors,
            },
            "systemd_unit": {
                "pass": unit_ok,
                "errors": unit_errors,
            },
            "python_modules": {
                "pass": modules_ok,
                "errors": module_errors,
            },
            "cli_endpoints": {
                "pass": cli_ok,
                "errors": cli_errors,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RetailBot2 Deployment Package Verifier")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--write-manifest", action="store_true", help="Write package manifest to deploy/package_manifest.json")
    parser.add_argument("--check-manifest", action="store_true", help="Verify checksums against deploy/package_manifest.json")
    parser.add_argument("--manifest", type=str, default=None, help="Custom path to package manifest JSON file")
    args = parser.parse_args()

    results = run_all_checks()
    manifest_path = Path(args.manifest) if args.manifest else (REPO_ROOT / "deploy/package_manifest.json")

    if args.write_manifest:
        manifest_data = {
            "version": "1.0.0",
            "stage": "F",
            "files": {
                rel_path: data["sha256"]
                for rel_path, data in results["checks"]["file_presence_and_checksums"]["files"].items()
                if data["present"]
            },
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
        if not args.json:
            print(f"Package manifest written to {manifest_path}")

    if args.check_manifest:
        if not manifest_path.exists():
            print(f"ERROR: Manifest file not found at {manifest_path}", file=sys.stderr)
            return 1
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_files = manifest_data.get("files", {})
        current_files = results["checks"]["file_presence_and_checksums"]["files"]
        manifest_mismatches = []
        for rel_path, expected_sha in manifest_files.items():
            current = current_files.get(rel_path)
            if not current or not current["present"]:
                manifest_mismatches.append(f"{rel_path}: file missing")
            elif current["sha256"] != expected_sha:
                manifest_mismatches.append(f"{rel_path}: SHA mismatch (expected {expected_sha}, got {current['sha256']})")
        if manifest_mismatches:
            results["status"] = "FAIL"
            results["checks"]["manifest_verification"] = {"pass": False, "mismatches": manifest_mismatches}
        else:
            results["checks"]["manifest_verification"] = {"pass": True, "mismatches": []}

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("=" * 78)
        print(" RetailBot2 Deployment Package Verification Report (Stage F)")
        print("=" * 78)
        print(f"Overall Status: {results['status']}\n")

        print("1. Package File Integrity & Checksums:")
        for rel_path, data in results["checks"]["file_presence_and_checksums"]["files"].items():
            status_str = f"OK  (SHA-256: {data['sha256'][:16]}..., {data['bytes']} bytes)" if data["present"] else "MISSING"
            print(f"  - {rel_path:45} : {status_str}")

        print("\n2. Environment Safety Defaults:")
        if results["checks"]["env_security_defaults"]["pass"]:
            print("  - Verified: Fail-closed switches default to 0; no leaked credentials.")
        else:
            print(f"  - FAILED: {results['checks']['env_security_defaults']['errors']}")

        print("\n3. Systemd Unit Configuration:")
        if results["checks"]["systemd_unit"]["pass"]:
            print("  - Verified: KillMode=mixed, TimeoutStopSec=30s, User=retailbot, ExecStart has --observation, directives valid.")
        else:
            print(f"  - FAILED: {results['checks']['systemd_unit']['errors']}")

        print("\n4. Python Module Imports:")
        if results["checks"]["python_modules"]["pass"]:
            print("  - Verified: All 4 outbox modules imported cleanly.")
        else:
            print(f"  - FAILED: {results['checks']['python_modules']['errors']}")

        print("\n5. CLI Help Endpoints:")
        if results["checks"]["cli_endpoints"]["pass"]:
            print("  - Verified: outbox_worker and outbox_retention CLI help flags returned 0.")
        else:
            print(f"  - FAILED: {results['checks']['cli_endpoints']['errors']}")

        print("=" * 78)

    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
