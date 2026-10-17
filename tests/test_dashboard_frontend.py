"""Static regression checks for security-sensitive dashboard rendering helpers."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "web" / "static" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "web" / "static" / "index.html"


class TestDashboardFrontendRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = APP_JS.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")

    def test_dynamic_dashboard_handlers_are_not_inline(self):
        # Dynamic templates must bind handlers through addEventListener/data attributes.
        self.assertNotRegex(self.app_js, re.compile(r"\bonclick\s*=", re.IGNORECASE))

    def test_api_links_are_restricted_to_http_schemes(self):
        helper_start = self.app_js.index("_safeHttpUrl(value)")
        helper_end = self.app_js.index("\n    },", helper_start)
        helper = self.app_js[helper_start:helper_end]
        self.assertIn('url.protocol === "http:" || url.protocol === "https:" ? url.href : ""', helper)

    def test_dashboard_datetime_preserves_explicit_offsets_and_rejects_invalid_values(self):
        formatter_start = self.app_js.index("_formatDateTime(isoString)")
        formatter_end = self.app_js.index("\n    },", formatter_start)
        formatter = self.app_js[formatter_start:formatter_end]
        self.assertIn(r"(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)", formatter)
        self.assertIn('if (!Number.isFinite(parsedMs)) return "--";', formatter)

    def test_screener_numeric_rendering_uses_finite_number_fallbacks(self):
        renderer_start = self.app_js.index("    _renderScreenerSignals(signals) {")
        renderer_end = self.app_js.index("\n    // ----------------------------------------------------\n    // AUTHENTICATION & SESSIONS", renderer_start)
        renderer = self.app_js[renderer_start:renderer_end]
        for field in ("rvol", "volume_velocity", "quality_score", "price", "price_change_pct", "quote_volume"):
            self.assertIn(f"this._safeNumber(s.{field})", renderer)
        self.assertNotIn("Number(s.rvol || 0)", renderer)
        self.assertNotIn("Number(s.volume_velocity || 0)", renderer)
        self.assertNotIn("Number(s.quote_volume || 0)", renderer)

    def test_dashboard_script_cache_token_is_present(self):
        match = re.search(r'/static/js/app\.js\?v=([^"\']+)', self.index_html)
        self.assertIsNotNone(match, "index.html must version the dashboard app.js asset")
        self.assertTrue(match.group(1).strip(), "app.js cache token must not be empty")

    def test_ml_experiment_rows_normalize_numeric_fields_before_formatting(self):
        renderer_start = self.app_js.index("tbody.innerHTML = experiments.map(exp => {", self.app_js.index("async fetchML"))
        renderer_end = self.app_js.index('}).join("");', renderer_start)
        renderer = self.app_js[renderer_start:renderer_end]
        for field in ("threshold", "roc_auc", "baseline_test_r", "ml_test_r", "ml_test_win_rate", "ml_test_trades"):
            self.assertIn(f"this._safeNumber(exp.{field}", renderer)
        self.assertIn("Number.isFinite(Number(exp.roc_auc))", renderer)


if __name__ == "__main__":
    unittest.main()
