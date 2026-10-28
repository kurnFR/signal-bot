"""Ensure read-only frontend API helpers target real registered FastAPI GET routes.

This is a source-to-runtime contract check: when a preview data helper changes an
endpoint, the test catches typos, stale paths, and routes that are not mounted.
It does not send requests or start the application lifespan.
"""
from pathlib import Path
import re
import unittest

from web.app import app, _role_allowed


ROOT = Path(__file__).resolve().parents[1]
ADMIN_ONLY_FRONTEND_GETS = {
    "/api/settings/status": "frontend/src/SettingsPanel.tsx",
}
GET_JSON_CALL = re.compile(
    r"""getJson(?:<[^;]*>)?\s*\(\s*(['"`])([^'"`]+)\1""",
    re.DOTALL,
)


def _normalize_path(path: str) -> str:
    # Query strings are not part of route matching. Convert both TypeScript
    # template expressions and FastAPI path parameters to the same placeholder.
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "1", path)
    path = re.sub(r"\{[^}]+\}", "1", path)
    return path


def _frontend_get_paths() -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    source_root = ROOT / "frontend" / "src"
    for source in sorted((*source_root.rglob("*.ts"), *source_root.rglob("*.tsx"))):
        if ".test." in source.name or source.name == "setup.ts":
            continue
        text = source.read_text(encoding="utf-8")
        for match in GET_JSON_CALL.finditer(text):
            raw_path = match.group(2)
            if not raw_path.startswith("/api/"):
                continue
            normalized = _normalize_path(raw_path)
            paths.setdefault(normalized, []).append(f"{source.relative_to(ROOT)}: {raw_path}")
    return paths



def _legacy_static_api_paths() -> dict[str, list[str]]:
    """Discover literal API path references used by the production legacy dashboard."""
    paths: dict[str, list[str]] = {}
    static_root = ROOT / "web" / "static"
    for source in sorted((*static_root.rglob("*.js"), *static_root.rglob("*.html"))):
        source_text = source.read_text(encoding="utf-8")
        for match in re.finditer(r"/api/[^\s"'\x60]+", source_text):
            raw_path = match.group(0).rstrip("),;")
            normalized = _normalize_path(raw_path)
            line_number = source_text.count("\n", 0, match.start()) + 1
            paths.setdefault(normalized, []).append(f"{source.relative_to(ROOT)}:{line_number}: {raw_path}")
    return paths


def _registered_get_paths() -> set[str]:
    registered: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path.startswith("/api/") and "GET" in methods:
            registered.add(_normalize_path(path))
    return registered


class TestFrontendApiRouteParity(unittest.TestCase):
    def test_every_frontend_get_json_endpoint_is_a_registered_fastapi_get_route(self):
        frontend_paths = _frontend_get_paths()
        self.assertGreaterEqual(len(frontend_paths), 10, "Expected to discover the preview API read surface")
        registered = _registered_get_paths()
        missing = {
            path: locations
            for path, locations in frontend_paths.items()
            if path not in registered
        }
        self.assertFalse(
            missing,
            "Frontend GET helpers reference unregistered FastAPI routes: "
            + repr(missing),
        )


    def test_every_legacy_dashboard_api_reference_is_registered(self):
        legacy_paths = _legacy_static_api_paths()
        self.assertGreaterEqual(
            len(legacy_paths),
            30,
            "Expected to discover the legacy dashboard's explicit API surface",
        )
        registered = {
            _normalize_path(getattr(route, "path", ""))
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/")
        }
        missing = {
            path: locations
            for path, locations in legacy_paths.items()
            if path not in registered
        }
        self.assertFalse(
            missing,
            "Legacy dashboard references unregistered FastAPI routes: "
            + repr(missing),
        )

    def test_preview_get_endpoints_are_readable_by_the_trader_role(self):
        # The browser smoke suite signs in as a trader. Keep every connected
        # read-only panel usable for that role instead of silently rendering
        # a permission error because its endpoint is admin-only.
        frontend_paths = _frontend_get_paths()
        denied = {
            path: locations
            for path, locations in frontend_paths.items()
            if path not in ADMIN_ONLY_FRONTEND_GETS and not _role_allowed("GET", path, "trader")
        }
        self.assertFalse(
            denied,
            "Preview GET endpoints are not readable by the trader role: "
            + repr(denied),
        )

    def test_admin_only_preview_gets_have_an_explicit_component_role_gate(self):
        frontend_paths = _frontend_get_paths()
        for path, component_path in ADMIN_ONLY_FRONTEND_GETS.items():
            with self.subTest(path=path):
                self.assertIn(path, frontend_paths)
                self.assertTrue(_role_allowed("GET", path, "admin"))
                self.assertFalse(_role_allowed("GET", path, "trader"))
                source = (ROOT / component_path).read_text(encoding="utf-8")
                call_position = source.index("fetchAdminSystemStatus(controller.signal)")
                guard_position = source.rfind("if (!isAdmin)", 0, call_position)
                self.assertGreater(call_position, guard_position)
                self.assertIn("return;", source[guard_position:call_position])
