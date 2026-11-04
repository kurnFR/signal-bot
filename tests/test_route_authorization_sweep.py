"""Sweep every /api route for authentication, role and first-login rules.

Built from the live route table so a new router cannot silently skip authorization.
The security middleware runs before handlers, so no database is needed here.
"""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app, _role_allowed

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Mutating endpoints every authenticated user (incl. viewer) legitimately needs.
VIEWER_ALLOWED_MUTATIONS = {"/api/auth/logout", "/api/auth/change-password"}
PUBLIC = {("POST", "/api/auth/login")}


def _collect_routes(routes):
    collected = []
    for route in routes:
        if hasattr(route, "original_router") and hasattr(route.original_router, "routes"):
            collected.extend(_collect_routes(route.original_router.routes))
        elif hasattr(route, "routes"):
            collected.extend(_collect_routes(route.routes))
        else:
            collected.append(route)
    return collected


def _operations():
    ops = []
    for route in _collect_routes(app.routes):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/"):
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            ops.append((method, concrete))
    return ops


def _user(role, must_change=False):
    return {"id": 1, "username": "u", "email": None, "role": role, "is_active": 1,
            "must_change_password": 1 if must_change else 0}


class TestRouteAuthorizationSweep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.ops = _operations()

    def test_unclassified_mutations_are_denied_by_default(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertFalse(
                _role_allowed(method, "/api/new-unclassified-control", "admin"),
                f"unclassified {method} endpoint must fail closed",
            )
        self.assertFalse(
            _role_allowed("GET", "/api/new-read-endpoint", "viewer"),
            "unclassified GET endpoints must fail closed until reviewed",
        )

    def test_every_registered_get_has_an_explicit_viewer_policy(self):
        restricted_prefixes = (
            "/api/users",
            "/api/settings",
            "/api/auth/audit-logs",
            "/api/custom-strategy",
        )
        for method, path in self.ops:
            if method != "GET":
                continue
            expected_viewer_access = not path.startswith(restricted_prefixes)
            self.assertEqual(
                _role_allowed(method, path, "viewer"),
                expected_viewer_access,
                f"GET {path} is missing an explicit viewer-read policy or restriction",
            )

    def test_unregistered_get_preserves_fastapi_not_found_response(self):
        with patch("web.app.authenticate_request", return_value=_user("viewer")):
            response = self.client.get("/api/not-registered")
        self.assertEqual(response.status_code, 404)

    def test_route_table_is_not_empty(self):
        self.assertGreater(len(self.ops), 40)

    def test_every_registered_mutation_has_an_explicit_role_policy(self):
        # New mutating routes must be deliberately classified, not merely
        # denied by the fail-closed fallback. This catches policy drift as
        # router modules add endpoints.
        for method, path in self.ops:
            if method not in MUTATING or (method, path) in PUBLIC:
                continue
            self.assertTrue(
                any(_role_allowed(method, path, role) for role in ("admin", "trader", "viewer")),
                f"{method} {path} has no explicit role policy; classify it before merging",
            )

    def test_every_api_operation_requires_authentication(self):
        for method, path in self.ops:
            if (method, path) in PUBLIC:
                continue
            res = self.client.request(method, path)
            self.assertEqual(res.status_code, 401, f"{method} {path} reachable without credentials")

    def test_viewer_cannot_call_any_mutating_operation(self):
        with patch("web.app.authenticate_request", return_value=_user("viewer")):
            for method, path in self.ops:
                if method not in MUTATING or path in VIEWER_ALLOWED_MUTATIONS or (method, path) in PUBLIC:
                    continue
                res = self.client.request(method, path, json={})
                self.assertEqual(res.status_code, 403, f"viewer can reach {method} {path}")

    def test_custom_strategy_catalog_reads_remain_admin_or_trader_only(self):
        # Current policy intentionally classifies the whole custom-strategy
        # prefix as admin/trader-only. Protect the observed behavior until the
        # product/security owner decides whether viewers should read catalogs.
        paths = ("/api/custom-strategy/list", "/api/custom-strategy/indicators")
        with patch("web.app.authenticate_request", return_value=_user("viewer")):
            for path in paths:
                res = self.client.get(path)
                self.assertEqual(res.status_code, 403, f"viewer can read restricted catalog path {path}")

    def test_only_admin_can_reach_admin_prefixes(self):
        admin_paths = [(m, p) for m, p in self.ops if p.startswith(("/api/users", "/api/settings", "/api/auth/audit-logs"))]
        self.assertTrue(admin_paths)
        for role in ("viewer", "trader"):
            with patch("web.app.authenticate_request", return_value=_user(role)):
                for method, path in admin_paths:
                    res = self.client.request(method, path, json={})
                    self.assertEqual(res.status_code, 403, f"{role} can reach {method} {path}")

    def test_must_change_password_blocks_everything_except_the_fix(self):
        allowed = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}
        with patch("web.app.authenticate_request", return_value=_user("admin", must_change=True)):
            for method, path in self.ops:
                if (method, path) in PUBLIC or path in allowed:
                    continue
                res = self.client.request(method, path, json={})
                self.assertEqual(res.status_code, 403, f"{method} {path} not blocked during forced password change")
                self.assertEqual(res.json().get("code"), "password_change_required", f"{method} {path}")

    def test_password_change_endpoints_stay_reachable_when_flagged(self):
        flagged = _user("viewer", must_change=True)
        # Patch both the middleware's reference and the route dependency's reference.
        with patch("web.app.authenticate_request", return_value=flagged), \
             patch("web.auth.authenticate_request", return_value=flagged):
            me = self.client.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertTrue(me.json()["must_change_password"])
            # Empty body reaches the handler's validation (422) instead of the 403 gate.
            res = self.client.post("/api/auth/change-password", json={})
            self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
