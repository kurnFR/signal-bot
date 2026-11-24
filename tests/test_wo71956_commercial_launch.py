"""Tests for Work Order 71956: Final Production Hardening & Commercial Launch Readiness."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from web.auth import (
    _ws_tickets,
    check_compute_rate_limit,
    consume_ws_ticket,
    create_ws_ticket,
    validate_production_secret_config,
)
from web.licensing import get_branding_config, sign_license_key, verify_license_key

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestContainerHardening:
    def test_dockerignore_excludes_secrets_and_dev_artifacts(self):
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        for expected in (".git", ".env", ".app_secret", "frontend/node_modules/", "venv/", "__pycache__/"):
            assert expected in dockerignore

    def test_docker_compose_enforces_required_secrets(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "${APP_SECRET_KEY:?" in compose
        assert "${MYSQL_PASSWORD:?" in compose
        assert "${MYSQL_ROOT_PASSWORD:?" in compose
        assert "${APP_SECRET_KEY:-}" not in compose

    def test_validate_production_secret_config_fails_fast(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_ENV", "production")
        monkeypatch.setenv("APP_SECRET_KEY", "too-short")
        with pytest.raises(RuntimeError, match="at least 32 characters"):
            validate_production_secret_config()

        monkeypatch.setenv("APP_SECRET_KEY", "a" * 32)
        validate_production_secret_config()  # passes

    def test_validate_production_secret_config_skips_non_prod(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_ENV", "development")
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        validate_production_secret_config()


class TestMultiWorkerDurableWsTickets:
    def test_db_backed_ticket_works_across_workers(self):
        """Simulates Worker A issuing a ticket to DB and Worker B (empty local memory) consuming it."""
        db_store = {}

        class FakeCursor:
            def __init__(self):
                self.rowcount = 0
                self._result = None

            def execute(self, sql, params=None):
                if sql.startswith("DELETE FROM ws_auth_tickets"):
                    self.rowcount = 0
                elif sql.startswith("INSERT INTO ws_auth_tickets"):
                    t_hash, payload, exp = params
                    db_store[t_hash] = {"user_payload": payload, "expires_at": exp, "consumed_at": None}
                    self.rowcount = 1
                elif sql.startswith("UPDATE ws_auth_tickets SET consumed_at"):
                    now_ts, t_hash, now_check = params
                    row = db_store.get(t_hash)
                    if row and row["consumed_at"] is None and row["expires_at"] >= now_check:
                        row["consumed_at"] = now_ts
                        self.rowcount = 1
                    else:
                        self.rowcount = 0
                elif sql.startswith("SELECT user_payload FROM ws_auth_tickets"):
                    t_hash = params[0]
                    row = db_store.get(t_hash)
                    self._result = {"user_payload": row["user_payload"]} if row else None

            def fetchone(self):
                return self._result

            def close(self):
                pass

        class FakeConn:
            def cursor(self, dictionary=False):
                return FakeCursor()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        fake_pool = MagicMock()
        fake_pool.get_connection.side_effect = lambda: FakeConn()

        with patch("web.auth.get_pool", return_value=fake_pool):
            _ws_tickets.clear()
            ticket = create_ws_ticket({"id": 7, "username": "desk_trader", "role": "trader"})
            # Ensure local worker memory does NOT hold the ticket (DB persisted it)
            assert len(_ws_tickets) == 0

            # Worker B consumes it atomically
            user = consume_ws_ticket(ticket)
            assert user == {"id": 7, "username": "desk_trader", "role": "trader"}

            # Replay fails (single-use)
            assert consume_ws_ticket(ticket) is None

    def test_in_memory_fallback_when_db_unavailable(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("no db")):
            ticket = create_ws_ticket({"id": 1, "username": "admin", "role": "admin"})
            assert consume_ws_ticket(ticket)["username"] == "admin"
            assert consume_ws_ticket(ticket) is None

    def test_compute_rate_limit_fallback_enforces_cap(self):
        with patch("web.auth.get_pool", side_effect=RuntimeError("no db")):
            key = f"wo71956_rate_{time.time()}"
            for _ in range(3):
                allowed, retry = check_compute_rate_limit(key, max_requests=3, window_seconds=60)
                assert allowed is True
                assert retry == 0
            allowed, retry = check_compute_rate_limit(key, max_requests=3, window_seconds=60)
            assert allowed is False
            assert retry >= 1


class TestCommercialLicensingAndBranding:
    def test_sign_and_verify_valid_license(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_ENV", "production")
        secret = "super-secret-signing-key-32-chars-min"
        future = int(time.time()) + 86400 * 30
        key = sign_license_key(
            customer="Acme Quant Desk",
            tier="desk",
            max_users=25,
            expires_at=future,
            signing_key=secret,
        )
        status = verify_license_key(key, signing_key=secret)
        assert status["valid"] is True
        assert status["mode"] == "licensed"
        assert status["customer"] == "Acme Quant Desk"
        assert status["tier"] == "desk"
        assert status["maxUsers"] == 25
        assert status["expiresAt"] is not None

    def test_expired_and_tampered_license_rejected(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_ENV", "production")
        secret = "super-secret-signing-key-32-chars-min"
        expired_key = sign_license_key(
            customer="Expired Co",
            tier="pro",
            max_users=5,
            expires_at=int(time.time()) - 60,
            signing_key=secret,
        )
        exp_status = verify_license_key(expired_key, signing_key=secret)
        assert exp_status["valid"] is False
        assert exp_status["mode"] == "expired"

        tampered = expired_key[:-4] + "AAAA"
        tamp_status = verify_license_key(tampered, signing_key=secret)
        assert tamp_status["valid"] is False
        assert tamp_status["mode"] == "invalid"

    def test_development_mode_unrestricted_without_key(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_ENV", "development")
        monkeypatch.delenv("SIGNAL_BOT_LICENSE_KEY", raising=False)
        status = verify_license_key()
        assert status["valid"] is True
        assert status["mode"] == "development"
        assert status["maxUsers"] is None

    def test_branding_config_sanitizes_logo_url(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_BOT_BRAND_NAME", "Alpha Desk Pro")
        monkeypatch.setenv("SIGNAL_BOT_LOGO_URL", "https://cdn.example.com/logo.png")
        cfg = get_branding_config()
        assert cfg == {"brandName": "Alpha Desk Pro", "logoUrl": "https://cdn.example.com/logo.png"}

        monkeypatch.setenv("SIGNAL_BOT_LOGO_URL", "javascript:alert(1)")
        assert get_branding_config()["logoUrl"] is None
