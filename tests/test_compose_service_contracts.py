"""Compose service contract tests.

Regression guard for the Docker deployment surface: each Compose service's
command/environment must satisfy the fail-closed requirements of the entry
point it runs. These are intentionally text-level checks (matching the
existing compose assertions in test_wo71956_commercial_launch.py) so they
run without Docker or a database.

Covered contracts:
- funding-collector: collectors/funding_poller.py (via config.py) requires
  dedicated FUNDING_DB_* credentials and refuses MYSQL_* fallbacks and the
  users 'cms'/'root'. Compose must pass FUNDING_DB_USER/PASSWORD as
  required variables, and .env.example must document them.
- outbox-worker: paper/outbox_worker.py defaults --database to the TEST
  database (signal_bot_test) and, for crypto_signals, requires
  RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1. Compose must name crypto_signals
  explicitly (as run_all.py does), pass the delivery gate, and keep the
  service opt-in behind a profile.
- web: the dashboard has no TLS of its own, so the base Compose file must
  bind it to loopback only (the prod Caddy overlay is the public path).
- .env.example must document the LLM provider selection that config.py
  actually defaults to (9router), not only the Anthropic key.
- Runtime data files must be dockerignored so a fresh image does not ship
  an operator's saved strategies / screener output.
- The unauthenticated /readyz probe must not leak raw exception text.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _compose():
    return (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _service_block(compose, name):
    """Return the raw text block for one top-level service."""
    lines = compose.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == f"  {name}:":
            start = i
            break
    assert start is not None, f"service {name!r} not found in docker-compose.yml"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            end = j
            break
        if line and not line.startswith(" "):
            end = j
            break
    return "\n".join(lines[start:end])


class TestFundingCollectorContract:
    def test_compose_passes_dedicated_funding_credentials(self):
        block = _service_block(_compose(), "funding-collector")
        assert "FUNDING_DB_USER" in block
        assert "FUNDING_DB_PASSWORD" in block
        # Required (fail-fast) interpolation, like MYSQL_PASSWORD/APP_SECRET_KEY
        assert "${FUNDING_DB_USER:?" in block
        assert "${FUNDING_DB_PASSWORD:?" in block
        assert "FUNDING_DB_HOST" in block
        assert "FUNDING_DB_NAME" in block

    def test_env_example_documents_funding_credentials(self):
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "FUNDING_DB_USER" in env_example
        assert "FUNDING_DB_PASSWORD" in env_example


class TestOutboxWorkerContract:
    def test_compose_names_production_database_explicitly(self):
        block = _service_block(_compose(), "outbox-worker")
        assert "--database" in block
        assert "crypto_signals" in block
        # The module form is what run_all.py uses
        assert "paper.outbox_worker" in block

    def test_compose_passes_telegram_delivery_gate(self):
        block = _service_block(_compose(), "outbox-worker")
        assert "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY" in block

    def test_outbox_worker_is_opt_in_via_profile(self):
        block = _service_block(_compose(), "outbox-worker")
        assert "profiles:" in block


class TestWebExposureContract:
    def test_web_binds_loopback_only_in_base_compose(self):
        block = _service_block(_compose(), "web")
        assert "127.0.0.1:8050:8050" in block
        assert '"8050:8050"' not in block


class TestEnvExampleLlmProvider:
    def test_env_example_documents_llm_provider_selection(self):
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "LLM_PROVIDER" in env_example
        assert "NINEROUTER_BASE_URL" in env_example
        assert "OPENROUTER_API_KEY" in env_example


class TestRuntimeDataNotShipped:
    def test_dockerignore_excludes_runtime_data(self):
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        assert "custom_strategies.json" in dockerignore
        assert "Screen/signals.jsonl" in dockerignore


class TestReadyzRedaction:
    def test_readyz_does_not_return_raw_exception_text(self):
        app_source = (REPO_ROOT / "web" / "app.py").read_text(encoding="utf-8")
        readyz_section = app_source.split("def readyz", 1)[1].split("PUBLIC_API_PATHS", 1)[0]
        assert '"reason": str(exc)' not in readyz_section
        assert "database unavailable" in readyz_section
