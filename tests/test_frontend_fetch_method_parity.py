"""Check that React preview API fetches use registered FastAPI HTTP methods.

The source scan covers literal fetch paths, local path constants passed to fetch,
and paths supplied to shared mutation helpers. It inspects source and route metadata
only; it sends no requests and does not start the application lifespan.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
from datetime import date
import unittest

from web.app import app


ROOT = Path(__file__).resolve().parents[1]
FETCH_CALL = re.compile(
    r"""(?:fetch|apiRequest|paperMutationRequest|walletMutationRequest|userRequest|mlMutationRequest)\s*(?:<[^\n]*?>)?\s*\(\s*(?:(['"`])((?:\$\{[^}]*\}|[^'"`])+?)\1|([A-Za-z_$][\w$]*))\s*(?:,\s*\{|\))"""
)
METHOD = re.compile(r"""\bmethod\s*:\s*(['"])(GET|POST|PUT|PATCH|DELETE)\1""", re.IGNORECASE)
FUNCTION_BOUNDARY = re.compile(r"""(?:async\s+)?function\b|=>\s*\{""")
MUTATION_HELPERS = {
    "postMasterDataJob": "POST",
    "postJson": "POST",
    "authPost": "POST",
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
                rf"""{helper}(?:<[^\n]*?>)?\s*\(\s*(['"`])(/api/[^'"`]+)\1""",
                text,
            ):
                _add_request(requests, method, match.group(2), source, text, match.start())
        for match in re.finditer(
            r"""mutateStrategyLibrary\s*\(\s*(['"`])(/api/[^'"`]+)\1\s*,\s*(['"])(POST|PUT|PATCH|DELETE)\3""",
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


# Each exception is an existing backend surface that has no Modern caller.
# Fixed audit clock keeps this parity test deterministic.
AUDIT_DATE = date(2026, 11, 27)
MUTATION_EXCEPTIONS = {
    ("DELETE", "/api/wallets/tracked"): (
        "Address-based legacy alias. Modern uses the ID route.", "Wallet Tracking", date(2027, 2, 25),
    ),
    ("POST", "/api/ml/rl/train"): (
        "Research endpoint awaiting a UI or retirement decision.", "ML Studio", date(2026, 12, 27),
    ),
    ("POST", "/api/ml/walk-forward"): (
        "Research endpoint awaiting a UI or retirement decision.", "ML Studio", date(2026, 12, 27),
    ),
    ("POST", "/api/research/retailbot2/control"): (
        "RetailBot2 research navigation retired.", "RetailBot2 Archive", date(2027, 2, 25),
    ),
    ("POST", "/api/research/retailbot2/strategies"): (
        "RetailBot2 research navigation retired.", "RetailBot2 Archive", date(2027, 2, 25),
    ),
}


def mutation_matrix():
    requests = _frontend_fetch_requests()
    return {
        key: requests.get(key, [])
        for key in sorted(_registered_api_methods())
        if key[0] in {"POST", "PUT", "PATCH", "DELETE"}
    }


class TestModernMutationCoverage(unittest.TestCase):
    def test_missing_modern_mutation_callers_require_explicit_exceptions(self):
        missing = {key for key, callers in mutation_matrix().items() if not callers}
        self.assertEqual(missing, MUTATION_EXCEPTIONS.keys())
        for key, (reason, owner, review_by) in MUTATION_EXCEPTIONS.items():
            with self.subTest(endpoint=key):
                self.assertTrue(reason and owner)
                self.assertGreaterEqual(review_by, AUDIT_DATE)


class LegacyControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []

    def handle_starttag(self, tag, attrs):
        control_id = dict(attrs).get("id")
        if control_id and tag in {"button", "input", "select", "textarea"}:
            self.controls.append((control_id, tag))


def print_control_matrix():
    # React generally uses labels/state instead of preserving Legacy DOM IDs.
    # Record reviewed equivalents explicitly so a name similarity never asserts parity.
    reviewed = {
        "login-username": "AuthGate.tsx / Username",
        "login-password": "AuthGate.tsx / Password",
        "force-old-pwd": "AuthGate.tsx / Current password",
        "force-new-pwd": "AuthGate.tsx / New password",
        "force-conf-pwd": "AuthGate.tsx / Confirm new password",
        "screener-ctl-enabled": "ScreenerControlsPanel.tsx / Screener enabled",
        "bt-symbol-select": "BacktestStudioPanel.tsx / Symbol",
        "bt-market-select": "BacktestStudioPanel.tsx / Market",
        "bt-timeframe-select": "BacktestStudioPanel.tsx / Timeframe",
        "bt-strategy-select": "BacktestStudioPanel.tsx / Strategy",
        "bt-segment-select": "BacktestStudioPanel.tsx / Data segment",
        "bt-capital-input": "BacktestStudioPanel.tsx / Initial capital",
        "bt-risk-input": "BacktestStudioPanel.tsx / Risk per trade",
        "btn-run-backtest": "BacktestStudioPanel.tsx / Run Backtest",
        "paper-config-symbol": "PaperTradingPanel.tsx / Deploy bot / Symbol",
        "paper-config-market": "PaperTradingPanel.tsx / Deploy bot / Market",
        "paper-config-timeframe": "PaperTradingPanel.tsx / Deploy bot / Timeframe",
        "paper-config-strategy": "PaperTradingPanel.tsx / Deploy bot / Strategy",
        "paper-config-capital": "PaperTradingPanel.tsx / Deploy bot / Allocated capital",
        "paper-config-risk": "PaperTradingPanel.tsx / Deploy bot / Risk per trade",
        "wt-add-chain": "WalletTrackingPanel.tsx / Track wallet / Chain",
        "wt-add-wallet": "WalletTrackingPanel.tsx / Track wallet / Address",
        "wt-add-label": "WalletTrackingPanel.tsx / Track wallet / Label",
        "strat-catalog-search": "StrategyLibraryPanel.tsx / Search strategies",
        "strat-catalog-filter": "StrategyLibraryPanel.tsx / Strategy type",
        "btn-toggle-sidebar": "App.tsx / sidebar toggle",
        "btn-top-toggle-sidebar": "App.tsx / sidebar toggle",
    }
    screener_source = (ROOT / "frontend/src/ScreenerControlsPanel.tsx").read_text(encoding="utf-8")
    for key, label in re.findall(r'key: "([a-z0-9_]+)", label: "([^"]+)"', screener_source):
        reviewed[f"screener-ctl-{key}"] = f"ScreenerControlsPanel.tsx / {label}"
    print("| Legacy file | Control | Modern equivalent |")
    print("|---|---|---|")
    for source in sorted((ROOT / "web" / "static").rglob("*.html")):
        parser = LegacyControls()
        parser.feed(source.read_text(encoding="utf-8"))
        for control_id, tag in parser.controls:
            print(f"| {source.relative_to(ROOT)} | `{tag}#{control_id}` | {reviewed.get(control_id, 'none recorded')} |")
    print("\n| Backend mutation | Modern caller or exception |")
    print("|---|---|")
    for (method, path), callers in mutation_matrix().items():
        locations = ", ".join(f"`{caller.split(': ', 1)[0]}`" for caller in callers)
        exemption = MUTATION_EXCEPTIONS.get((method, path))
        result = locations or f"None. {exemption[0] if exemption else 'Unallowlisted.'}"
        print(f"| `{method} {path}` | {result} |")


if __name__ == "__main__":
    print_control_matrix()
