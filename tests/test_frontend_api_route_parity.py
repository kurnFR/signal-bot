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

    def test_preview_get_endpoints_are_readable_by_the_trader_role(self):
        # The browser smoke suite signs in as a trader. Keep every connected
        # read-only panel usable for that role instead of silently rendering
        # a permission error because its endpoint is admin-only.
        frontend_paths = _frontend_get_paths()
        denied = {
            path: locations
            for path, locations in frontend_paths.items()
            if not _role_allowed("GET", path, "trader")
        }
        self.assertFalse(
            denied,
            "Preview GET endpoints are not readable by the trader role: "
            + repr(denied),
        )
