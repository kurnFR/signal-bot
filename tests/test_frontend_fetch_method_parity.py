"""Check that literal API fetch calls in the React preview use registered HTTP methods.

This complements GET-helper path checks: direct fetch calls also carry POST/DELETE
requests and must not drift away from the mounted FastAPI route methods.
The test inspects source and app route metadata only; it sends no requests.
"""
from pathlib import Path
import re
import unittest

from web.app import app


ROOT = Path(__file__).resolve().parents[1]
FETCH_PATH = re.compile(r"""fetch\s*\(\s*(['"`])([^'"`]+)\1""")
METHOD = re.compile(r"""\bmethod\s*:\s*(['"])(GET|POST|PUT|PATCH|DELETE)\1""", re.IGNORECASE)


def _normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "1", path)
    path = re.sub(r"\{[^}]+\}", "1", path)
    return path


def _frontend_fetch_requests() -> dict[tuple[str, str], list[str]]:
    requests: dict[tuple[str, str], list[str]] = {}
    source_root = ROOT / "frontend" / "src"
    for source in sorted((*source_root.rglob("*.ts"), *source_root.rglob("*.tsx"))):
        if ".test." in source.name or source.name == "setup.ts":
            continue
        text = source.read_text(encoding="utf-8")
        for match in FETCH_PATH.finditer(text):
            raw_path = match.group(2)
            if not raw_path.startswith("/api/"):
                continue
            # The method option is near the beginning of the fetch options object.
            # Fetch defaults to GET when no method is provided.
            options_prefix = text[match.end():match.end() + 300]
            method_match = METHOD.search(options_prefix)
            method = method_match.group(2).upper() if method_match else "GET"
            normalized = _normalize_path(raw_path)
            line = text.count("\n", 0, match.start()) + 1
            requests.setdefault((method, normalized), []).append(
                f"{source.relative_to(ROOT)}:{line}: {method} {raw_path}"
            )
    return requests


def _registered_api_methods() -> set[tuple[str, str]]:
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path.startswith("/api/"):
            normalized = _normalize_path(path)
            registered.update((method.upper(), normalized) for method in methods)
    return registered


class TestFrontendFetchMethodParity(unittest.TestCase):
    def test_literal_preview_fetch_requests_use_registered_route_methods(self):
        requests = _frontend_fetch_requests()
        self.assertGreaterEqual(
            len(requests),
            8,
            "Expected to discover direct preview API fetch calls, including mutations",
        )
        registered = _registered_api_methods()
        missing = {
            key: locations
            for key, locations in requests.items()
            if key not in registered
        }
        self.assertFalse(
            missing,
            "Preview fetch calls use unregistered API method/path pairs: " + repr(missing),
        )
