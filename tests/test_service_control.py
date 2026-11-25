import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from db import service_control as sc
from web.app import app
from web.auth import create_access_token, require_admin


def _db_available():
    try:
        sc._pool().get_connection().close()
        with sc._pool().get_connection() as c:  # noqa: F841
            pass
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_available(), reason="MariaDB not available")


@pytest.fixture(autouse=True)
def _clean():
    sc._cache.clear()
    yield
    sc._cache.clear()


@db_required
def test_default_is_enabled_and_toggle_roundtrip():
    conn = sc._pool().get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM service_control")
    conn.commit(); cur.close(); conn.close()

    assert sc.is_service_paused("news_ai", use_cache=False) is False
    assert all(v["enabled"] for v in sc.get_service_states().values())

    sc.set_service_enabled("news_ai", False, "tester")
    assert sc.is_service_paused("news_ai", use_cache=False) is True
    assert sc.get_service_states()["news_ai"]["updated_by"] == "tester"
    assert sc.is_service_paused("paper_engine", use_cache=False) is False  # isolation

    sc.set_service_enabled("news_ai", True, "tester")
    assert sc.is_service_paused("news_ai", use_cache=False) is False


def test_unknown_service_rejected():
    with pytest.raises(ValueError):
        sc.set_service_enabled("nope", False)


def test_fail_open_when_database_errors():
    with patch.object(sc, "_pool", side_effect=RuntimeError("db down")):
        assert sc.is_service_paused("paper_engine", use_cache=False) is False


def test_pause_gate_sleeps_and_reports_paused(monkeypatch):
    slept = []
    monkeypatch.setattr(sc, "is_service_paused", lambda s, **k: True)
    monkeypatch.setattr(sc.time, "sleep", lambda s: slept.append(s))
    with patch("db.db.heartbeat") as hb:
        assert sc.pause_gate("paper_engine", "paper_engine", 5) is True
    assert slept == [5.0]
    hb.assert_called_once()
    monkeypatch.setattr(sc, "is_service_paused", lambda s, **k: False)
    assert sc.pause_gate("paper_engine", "paper_engine", 5) is False


class _Routes:
    def _client(self, role):
        token = create_access_token(user_id=1, username="u", role=role)
        hdr = {"Authorization": f"Bearer {token}"}

        def auth(request):
            if request.headers.get("Authorization") != hdr["Authorization"]:
                raise HTTPException(status_code=401, detail="no")
            return {"id": 1, "username": "u", "role": role, "is_active": 1}

        p = patch("web.app.authenticate_request", side_effect=auth)
        p.start()
        return TestClient(app), hdr, p


@pytest.mark.parametrize("role,code", [("viewer", 403), ("trader", 403), ("admin", 200)])
def test_list_services_is_admin_only(role, code):
    c, h, p = _Routes()._client(role)
    try:
        with patch("web.routes.settings.list_services_with_heartbeats", return_value=[]):
            assert c.get("/api/settings/services", headers=h).status_code == code
    finally:
        p.stop()


def test_toggle_requires_admin_audits_and_validates():
    c, h, p = _Routes()._client("trader")
    try:
        assert c.put("/api/settings/services/paper_engine", json={"enabled": False}, headers=h).status_code == 403
    finally:
        p.stop()
    c, h, p = _Routes()._client("admin")
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "username": "u", "role": "admin"}
    try:
        with patch("web.routes.settings.set_service_enabled") as setter, \
             patch("web.routes.settings.log_audit") as audit:
            r = c.put("/api/settings/services/paper_engine", json={"enabled": False}, headers=h)
            assert r.status_code == 200 and r.json()["enabled"] is False
            setter.assert_called_once_with("paper_engine", False, "u")
            assert audit.call_args.args[0] == "SERVICE_PAUSED"
            assert c.put("/api/settings/services/bogus", json={"enabled": True}, headers=h).status_code == 404
    finally:
        app.dependency_overrides.pop(require_admin, None)
        p.stop()
