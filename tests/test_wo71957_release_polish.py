"""Tests for Work Order 71957: v1.0.0 Commercial Release Readiness & Polish."""
import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_modern_views_includes_trading_desk():
    tree = ast.parse((REPO_ROOT / "web/app.py").read_text(encoding="utf-8"))
    modern_views = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MODERN_VIEWS":
                    modern_views = ast.literal_eval(node.value)
    assert isinstance(modern_views, set)
    assert "trading-desk" in modern_views


def test_license_cli_mint_and_verify_roundtrip():
    secret = "wo71957-cli-signing-key-32-chars-minimum"
    mint = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/generate_license.py"),
            "--customer",
            "North Star Capital",
            "--tier",
            "pro",
            "--max-users",
            "12",
            "--days",
            "90",
            "--signing-key",
            secret,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    key = mint.stdout.strip()
    assert key.startswith("SB1.")

    verify = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/generate_license.py"),
            "--verify",
            key,
            "--signing-key",
            secret,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(verify.stdout)
    assert data["valid"] is True
    assert data["customer"] == "North Star Capital"
    assert data["tier"] == "pro"
    assert data["maxUsers"] == 12


def test_env_example_and_runbook_synced():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in (
        "SIGNAL_BOT_ENV=",
        "APP_SECRET_KEY=",
        "MYSQL_ROOT_PASSWORD=",
        "SIGNAL_BOT_LICENSE_KEY=",
        "SIGNAL_BOT_LICENSE_SIGNING_KEY=",
        "SIGNAL_BOT_BRAND_NAME=",
    ):
        assert var in env_example

    runbook = (REPO_ROOT / "deploy/PRODUCTION_RUNBOOK.md").read_text(encoding="utf-8")
    assert "GET /api/auth/audit-logs" in runbook
    assert "GET /api/system/audit-logs" not in runbook
    assert "scripts/generate_license.py" in runbook
