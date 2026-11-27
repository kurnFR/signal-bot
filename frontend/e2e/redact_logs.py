"""Redact and verify CI diagnostics without printing credentials."""

import os
from pathlib import Path
import subprocess
import sys


secrets = [
    os.environ[name] for name in (
        "MYSQL_PASSWORD", "SIGNAL_BOT_TEST_DB_PASSWORD", "SIGNAL_BOT_REAL_E2E_PASSWORD",
    ) if os.environ.get(name)
]
for path in map(Path, sys.argv[1:]):
    if not path.is_file():
        continue
    log = path.read_bytes()
    for secret in secrets:
        log = log.replace(secret.encode(), b"[REDACTED]")
    path.write_bytes(log)
    for secret in secrets:
        # Read the pattern from stdin. Never put a credential in the command line.
        result = subprocess.run(
            ["grep", "-qFf", "-", path], input=(secret + "\n").encode(), check=False,
        )
        if result.returncode != 1:
            path.unlink()
            raise SystemExit("Diagnostic credential check failed")
    print(f"Credential grep found no matches in {path.name}")
