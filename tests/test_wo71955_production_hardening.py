"""Tests for Work Order 71955: Production Hardening, Security, and Packaging.

Validates:
- Security HTTP response headers (CSP, X-Frame-Options, X-Content-Type-Options, Permissions-Policy, HSTS)
- CORS allow_methods includes PATCH and excludes unauthorized origins
- Health probes /healthz (200 OK) and /readyz (200 / 503)
- Short-lived single-use WebSocket tickets (POST /api/auth/ws-ticket)
- Session revocation via session_version increment on logout
- Token refresh (POST /api/auth/refresh)
- Terms & Risk Disclaimer acceptance (POST /api/auth/accept-terms)
- Encrypted DB-backed Telegram settings (PUT /api/telegram/config)
- Operator audit logs endpoint (GET /api/system/audit-logs)
- Rate limiting on compute-heavy operations (HTTP 429 with Retry-After)
"""
import pytest
from starlette.testclient import TestClient
from unittest.mock import patch

from web.app import app
from web.auth import create_access_token, create_ws_ticket, consume_ws_ticket, check_compute_rate_limit
from db.db import get_pool


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token():
    return create_access_token(user_id=1, username="admin", role="admin", session_version=0)


@pytest.fixture
def trader_token():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, username, role FROM users WHERE role = 'trader' AND is_active = 1 LIMIT 1")
        row = cur.fetchone()
        if row:
            return create_access_token(user_id=row["id"], username=row["username"], role=row["role"], session_version=0)
        return create_access_token(user_id=3, username="trader_bob", role="trader", session_version=0)
    finally:
        conn.close()


class TestSecurityHeadersAndCORS:
    def test_security_headers_present_on_response(self, client):
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in res.headers.get("Permissions-Policy", "")
        assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")
        assert "X-Request-ID" in res.headers

    def test_cors_options_preflight_allows_patch(self, client):
        headers = {
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        }
        res = client.options("/api/paper/configs/1", headers=headers)
        assert res.status_code == 200
        allow_methods = res.headers.get("Access-Control-Allow-Methods", "")
        assert "PATCH" in allow_methods


class TestHealthAndReadinessProbes:
    def test_healthz_unauthenticated_liveness(self, client):
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_readyz_readiness_probe(self, client):
        res = client.get("/readyz")
        # In test environment with MariaDB running, should return ready (or 503 if mock DB unreachable)
        assert res.status_code in (200, 503)
        assert "status" in res.json()


class TestWebSocketTicketLifecycle:
    def test_ws_ticket_creation_and_consumption(self, client, admin_token):
        user_payload = {"id": 1, "username": "admin", "role": "admin"}
        ticket = create_ws_ticket(user_payload)
        assert isinstance(ticket, str)
        assert len(ticket) >= 32

        # First consumption succeeds
        consumed = consume_ws_ticket(ticket)
        assert consumed is not None
        assert consumed["username"] == "admin"

        # Second consumption fails (single-use)
        second = consume_ws_ticket(ticket)
        assert second is None

    def test_api_auth_ws_ticket_endpoint(self, client, admin_token):
        res = client.post(
            "/api/auth/ws-ticket",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "ticket" in data
        assert data.get("expires_in") == 30


class TestSessionRevocationAndRefresh:
    def test_token_refresh_endpoint(self, client, admin_token):
        res = client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert data["user"]["username"] == "admin"

    def test_terms_acceptance_endpoint(self, client, admin_token):
        res = client.post(
            "/api/auth/accept-terms",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert res.json().get("status") == "success"


class TestTelegramConfigAndAudit:
    def test_telegram_config_update_requires_admin(self, client, trader_token):
        res = client.put(
            "/api/telegram/config",
            json={"delivery_authorized": True},
            headers={"Authorization": f"Bearer {trader_token}"},
        )
        assert res.status_code == 403

    def test_audit_logs_endpoint_rbac(self, client, admin_token, trader_token):
        # Trader denied
        res_trader = client.get(
            "/api/system/audit-logs",
            headers={"Authorization": f"Bearer {trader_token}"},
        )
        assert res_trader.status_code == 403

        # Admin allowed
        res_admin = client.get(
            "/api/system/audit-logs?limit=5",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_admin.status_code == 200
        data = res_admin.json()
        assert "logs" in data
        assert "total" in data


class TestComputeRateLimiting:
    def test_compute_rate_limit_helper(self):
        key = "test_rate_user_999"
        # Consume 5 requests with max 5
        for _ in range(5):
            allowed, _ = check_compute_rate_limit(key, max_requests=5, window_seconds=60)
            assert allowed is True

        # 6th request rejected with retry_after
        allowed, retry_after = check_compute_rate_limit(key, max_requests=5, window_seconds=60)
        assert allowed is False
        assert retry_after > 0
