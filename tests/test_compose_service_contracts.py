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


class TestExternalMysqlSupport:
    """Compose must also work when MySQL/MariaDB runs OUTSIDE Docker
    (e.g. on the host server): the bundled database is an opt-in profile,
    app services must not hard-require it, DB hosts are configurable,
    and containers can reach a host database via host.docker.internal."""

    def test_bundled_mariadb_is_profile_gated(self):
        block = _service_block(_compose(), "mariadb")
        assert 'profiles: ["bundled-db"]' in block

    def test_app_services_do_not_require_bundled_db(self):
        compose = _compose()
        for service in ("web", "collectors", "funding-collector", "outbox-worker"):
            block = _service_block(compose, service)
            assert "required: false" in block, service
            assert "host.docker.internal:host-gateway" in block, service

    def test_db_hosts_are_configurable(self):
        compose = _compose()
        assert "${MYSQL_HOST:-mariadb}" in compose
        assert "${MYSQL_PORT:-3306}" in compose
        funding = _service_block(compose, "funding-collector")
        assert "${FUNDING_DB_HOST:-mariadb}" in funding

    def test_env_example_defaults_to_bundled_db_and_documents_external(self):
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "COMPOSE_PROFILES=bundled-db" in env_example
        assert "host.docker.internal" in env_example

    def test_readme_documents_external_mysql_mode(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "External MySQL" in readme
        assert "host.docker.internal" in readme


class TestReadyzRedaction:
    def test_readyz_does_not_return_raw_exception_text(self):
        app_source = (REPO_ROOT / "web" / "app.py").read_text(encoding="utf-8")
        readyz_section = app_source.split("def readyz", 1)[1].split("PUBLIC_API_PATHS", 1)[0]
        assert '"reason": str(exc)' not in readyz_section
        assert "database unavailable" in readyz_section


ROOT = REPO_ROOT


def _read(relpath):
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


class TestAuditCleanupContracts:
    """Contracts for the remaining first-audit findings: unified defaults,
    no legacy personal defaults, repo hygiene, toolchain alignment."""

    def test_enable_futures_default_is_false_everywhere(self):
        compose = _read("docker-compose.yml")
        assert compose.count("ENABLE_FUTURES: ${ENABLE_FUTURES:-False}") == 2
        env = _read(".env.example")
        assert "\nENABLE_FUTURES=False" in env
        config = _read("config.py")
        assert 'os.getenv("ENABLE_FUTURES", "false")' in config

    def test_no_legacy_cms_user_defaults(self):
        assert 'DB_USER", "cms"' not in _read("config.py")
        assert 'MYSQL_USER", "cms"' not in _read("paper/outbox_worker.py")
        assert "DB_USER', 'cms'" not in _read("paper/outbox_store.py")
        assert '"signal_bot"' in _read("config.py")

    def test_systemd_units_are_templates_without_personal_account(self):
        for unit in (
            "signal-funding-collector.service",
            "deploy/systemd/signal-bot.service",
            "deploy/systemd/signal-web.service",
        ):
            text = _read(unit)
            assert "BIS" not in text, unit
            assert "/home/" not in text, unit
        assert "SIGNAL_BOT_USE_MODERN_UI=1" in _read(
            "deploy/systemd/signal-bot.service"
        )

    def test_env_example_has_no_dead_binance_key(self):
        assert "BINANCE_API_KEY" not in _read(".env.example")

    def test_pytest_available_via_requirements_dev(self):
        # Full split (pytest out of requirements.txt) lands together with
        # the .github/workflows install-step changes; see the note in
        # requirements-dev.txt.
        dev = _read("requirements-dev.txt")
        assert "-r requirements.txt" in dev
        assert "pytest==8.3.5" in dev

    def test_toolchain_versions_aligned(self):
        # Dockerfile is the alignment target (Python 3.12 / Node 22). The
        # matching CI workflow bumps (3.11 -> 3.12, Node 20 -> 22) are
        # listed in PR #787's description: .github/workflows/ files cannot
        # be modified by this repo's GitHub App (no workflows permission).
        dockerfile = _read("Dockerfile")
        assert "python:3.12" in dockerfile and "node:22" in dockerfile

    def test_root_clutter_moved(self):
        for name in (
            "validate_step1.py",
            "validate_step2.py",
            "check_funding_range.py",
            "check_futures_access.py",
        ):
            assert not (ROOT / name).exists(), name
            assert (ROOT / "tools" / name).exists(), name
        assert not (ROOT / "work_order_71937_production_readiness_remediation.md").exists()
        assert (ROOT / "docs/work-orders/work_order_71937_production_readiness_remediation.md").exists()

    def test_product_versions_aligned(self):
        import json

        pkg = json.loads(_read("frontend/package.json"))
        app = _read("web/app.py")
        assert f'version="{pkg["version"]}"' in app

    def test_architecture_doc_marks_liquidations_deprecated(self):
        arch = _read("docs/ARCHITECTURE.md")
        assert "liquidations collector is deprecated and not started" in arch
