"""Check that React preview API fetches use registered FastAPI HTTP methods.

The source scan covers literal fetch paths, local path constants passed to fetch,
and paths supplied to shared mutation helpers. It inspects source and route metadata
only; it sends no requests and does not start the application lifespan.
"""
from pathlib import Path
import re
import unittest

from web.app import app


ROOT = Path(__file__).resolve().parents[1]
FETCH_CALL = re.compile(
    r"""fetch\s*\(\s*(?:(['"`])([^'"`]+)\1|([A-Za-z_$][\w$]*))\s*,\s*\{"""
)
METHOD = re.compile(r"""\bmethod\s*:\s*(['"])(GET|POST|PUT|PATCH|DELETE)\1""", re.IGNORECASE)
FUNCTION_BOUNDARY = re.compile(r"""(?:async\s+)?function\b|=>\s*\{""")
MUTATION_HELPERS = {
    "postMasterDataJob": "POST",
}


def _normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "1", path)
    path = re.sub(r"\{[^}]+\}", "1", path)
    return path


def _add_request(
    requests: dict[tuple[str, str], list[str]],
    method: str,
    raw_path: str,
    source: Path,
    text: str,
    position: int,
) -> None:
    if not raw_path.startswith("/api/"):
        return
    line = text.count("\n", 0, position) + 1
    requests.setdefault((method.upper(), _normalize_path(raw_path)), []).append(
        f"{source.relative_to(ROOT)}:{line}: {method.upper()} {raw_path}"
    )


def _frontend_fetch_requests() -> dict[tuple[str, str], list[str]]:
    requests: dict[tuple[str, str], list[str]] = {}
    source_root = ROOT / "frontend" / "src"
    for source in sorted((*source_root.rglob("*.ts"), *source_root.rglob("*.tsx"))):
        if ".test." in source.name or source.name == "setup.ts":
            continue
        text = source.read_text(encoding="utf-8")
        assignments: dict[str, list[tuple[int, str]]] = {}
        for name_match in re.finditer(r"""\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(['"`])(/api/[^'"`]+)\2""", text):
            assignments.setdefault(name_match.group(1), []).append((name_match.start(), name_match.group(3)))

        for match in FETCH_CALL.finditer(text):
            literal_path, variable = match.group(2), match.group(3)
            options_prefix = text[match.end():match.end() + 350]
            method_match = METHOD.search(options_prefix)
            method = method_match.group(2).upper() if method_match else "GET"
            raw_path = literal_path
            if variable:
                scope_matches = list(FUNCTION_BOUNDARY.finditer(text, 0, match.start()))
                scope_start = scope_matches[-1].start() if scope_matches else 0
                candidates = [
                    (position, value)
                    for position, value in assignments.get(variable, [])
                    if scope_start < position < match.start()
                ]
                if not candidates:
                    continue
                raw_path = max(candidates, key=lambda item: item[0])[1]
            _add_request(requests, method, raw_path, source, text, match.start())

        # Shared helpers receive endpoint literals at their call sites; the HTTP
        # method is fixed by the helper implementation, not by fetch()'s argument.
        for helper, method in MUTATION_HELPERS.items():
            for match in re.finditer(
                rf"""{helper}\s*\(\s*(['"`])(/api/[^'"`]+)\1""",
                text,
            ):
                _add_request(requests, method, match.group(2), source, text, match.start())
        for match in re.finditer(
            r"""mutateStrategyLibrary\s*\(\s*(['"`])(/api/[^'"`]+)\1\s*,\s*(['"])(POST|DELETE)\3""",
            text,
        ):
            _add_request(requests, match.group(4), match.group(2), source, text, match.start())
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
            "Expected to discover preview fetch paths, local path constants, and mutation helper call sites",
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
