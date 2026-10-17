"""Static regression checks for security-sensitive dashboard rendering helpers."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "web" / "static" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "web" / "static" / "index.html"
DASHBOARD_CSS = REPO_ROOT / "web" / "static" / "css" / "dashboard_enhancements.css"


class TestDashboardFrontendRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = APP_JS.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")

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


    def test_screener_refresh_failure_is_visible_and_escapes_api_error(self):
        fetch_start = self.app_js.index("    async fetchScreenerData() {")
        fetch_end = self.app_js.index("    // ----------------------------------------------------\n    // WALLET TRACKING", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('statusEl.innerText = "DATA UNAVAILABLE"', fetcher)
        self.assertIn('detailEl.innerText = `Refresh failed: ${message}`', fetcher)
        self.assertIn('this._escapeHtml(message)', fetcher)
        self.assertIn('colspan="10"', fetcher)
        self.assertIn("Unable to refresh Market Screener data", fetcher)


    def test_wallet_refresh_failure_is_visible_and_escapes_api_error(self):
        fetch_start = self.app_js.index("    async fetchWalletData(opts = {}) {")
        fetch_end = self.app_js.index("    async fetchWalletEvents(opts = {}) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('statusEl.innerText = "DATA UNAVAILABLE"', fetcher)
        self.assertIn('detailEl.innerText = `Refresh failed: ${message}`', fetcher)
        self.assertIn("const safeMessage = this._escapeHtml(message)", fetcher)
        for element_id, colspan in (("wt-flow-body", 8), ("wt-candidates-body", 13), ("wt-feed-body", 8)):
            self.assertIn(f'document.getElementById("{element_id}")', fetcher)
            self.assertIn(f"errorRow({colspan})", fetcher)
        self.assertIn('if (!opts.silent) this.showToast', fetcher)


    def test_coverage_refresh_failure_clears_stale_data_and_shows_error(self):
        fetch_start = self.app_js.index("    async fetchCoverage()")
        fetch_end = self.app_js.index("    async fetchStrategies()", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('if (!res.ok) throw new Error(`HTTP ${res.status}`)', fetcher)
        self.assertIn('!Array.isArray(data.coverage)', fetcher)
        self.assertIn('this.coverageData = []', fetcher)
        self.assertIn('this.coverageSummary = {}', fetcher)
        self.assertIn('getElementById("coverage-table-body")', fetcher)
        self.assertIn('role="status"', fetcher)
        self.assertIn('this.showToast("Failed to load historical coverage.', fetcher)

    def test_strategy_list_failure_clears_stale_selection_and_shows_error(self):
        fetcher_start = self.app_js.index("    async fetchStrategies()")
        fetcher_end = self.app_js.index("\n    renderHome()", fetcher_start)
        fetcher = self.app_js[fetcher_start:fetcher_end]
        self.assertIn('if (!res.ok) throw new Error(`HTTP ${res.status}`)', fetcher)
        self.assertIn('!Array.isArray(data.strategies)', fetcher)
        self.assertIn('this.strategiesList = []', fetcher)
        self.assertIn('this.selectedStrategy = null', fetcher)
        self.assertIn('this.updateStrategySelectors()', fetcher)
        self.assertIn('getElementById("bt-strategy-info")', fetcher)
        self.assertIn('role="status"', fetcher)
        self.assertIn('this.showToast("Failed to load strategies.', fetcher)

    def test_popular_markets_failure_replaces_stale_cards_and_allows_retry(self):
        fetch_start = self.app_js.index("    async fetchPopularCoins() {")
        fetch_end = self.app_js.index("    async handleSearchCrypto() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("if (!res.ok) throw new Error", fetcher)
        self.assertIn("!Array.isArray(data.items)", fetcher)
        self.assertIn("Popular markets are temporarily unavailable.", fetcher)
        self.assertIn('role="status"', fetcher)
        self.assertIn("data-retry-popular-coins", fetcher)
        self.assertIn('this.fetchPopularCoins()', fetcher)

    def test_system_status_failure_marks_database_unavailable(self):
        fetch_start = self.app_js.index("    async fetchSystemStatus() {")
        fetch_end = self.app_js.index("    async fetchCoverage() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("if (!res.ok) throw new Error", fetcher)
        self.assertIn('this.systemStatus = null;', fetcher)
        self.assertIn("this.updateStatusBar();", fetcher)
        status_bar_start = self.app_js.index("    updateStatusBar() {")
        status_bar_end = self.app_js.index("    async fetchCoverage() {", status_bar_start)
        status_bar = self.app_js[status_bar_start:status_bar_end]
        self.assertIn("MariaDB: Unavailable", status_bar)
        self.assertIn("this.systemStatus?.database?.status", status_bar)

    def test_recent_backtest_history_failure_replaces_stale_rows_safely(self):
        fetch_start = self.app_js.index("    async fetchRecentBacktests() {")
        fetch_end = self.app_js.index("    renderMasterData() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("if (!res.ok)", fetcher)
        self.assertIn('document.getElementById("home-recent-backtests")', fetcher)
        self.assertIn('colspan="7"', fetcher)
        self.assertIn("Unable to refresh recent backtests", fetcher)
        self.assertIn("this._escapeHtml(message)", fetcher)

    def test_paper_refresh_failure_replaces_stale_data_safely(self):
        fetch_start = self.app_js.index("    async fetchPaperData() {")
        fetch_end = self.app_js.index("    renderPaperView() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("const failed = responses.find(response => !response.ok)", fetcher)
        self.assertIn("// Parse every response before mutating the displayed snapshot.", fetcher)
        for element_id in ("paper-kpi-open", "paper-kpi-total", "paper-kpi-winrate", "paper-kpi-cumr"):
            self.assertIn(f'"{element_id}"', fetcher)
        self.assertIn('if (el) el.innerText = "Unavailable"', fetcher)
        self.assertIn('document.getElementById("paper-positions-body")', fetcher)
        self.assertIn('document.getElementById("paper-configs-container")', fetcher)
        self.assertIn('document.getElementById("paper-trades-body")', fetcher)
        self.assertIn("this._escapeHtml(message)", fetcher)
        self.assertIn('this.showToast(`Failed to load paper trading data:', fetcher)

    def test_screener_refresh_failure_marks_kpis_unavailable(self):
        fetch_start = self.app_js.index("    async fetchScreenerData() {")
        fetch_end = self.app_js.index("    // ----------------------------------------------------", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('statusEl.innerText = "DATA UNAVAILABLE"', fetcher)
        self.assertIn('if (detailEl) detailEl.innerText = `Refresh failed:', fetcher)
        self.assertIn('if (el) el.innerText = "Unavailable"', fetcher)
        for element_id in ("screener-kpi-24h", "screener-kpi-total", "screener-kpi-breakdown", "screener-kpi-db"):
            self.assertIn(f'"{element_id}"', fetcher)
        self.assertIn('this._escapeHtml(message)', fetcher)

    def test_news_ai_refresh_failure_replaces_stale_data_safely(self):
        fetch_start = self.app_js.index("    async fetchNewsData() {")
        fetch_end = self.app_js.index("    _renderNewsKpis(stats) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('const safeMessage = this._escapeHtml(message)', fetcher)
        self.assertIn('Unable to refresh News AI signals', fetcher)
        self.assertIn('News AI event feed unavailable', fetcher)
        self.assertIn('News AI service status unavailable', fetcher)
        self.assertIn('if (el) el.innerText = "Unavailable"', fetcher)
        self.assertIn('colspan="8"', fetcher)

    def test_research_refresh_failure_replaces_stale_data_safely(self):
        fetch_start = self.app_js.index("    async fetchResearchData() {")
        fetch_end = self.app_js.index("    async fetchScreenerData() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('const safeMessage = this._escapeHtml(message)', fetcher)
        for element_id, colspan in (("rb2-positions-table-body", 9), ("rb2-strategy-table-body", 7)):
            self.assertIn(f'document.getElementById("{element_id}")', fetcher)
            self.assertIn(f'colspan="{colspan}"', fetcher)
        self.assertIn('setUnavailable(id)', fetcher)
        self.assertIn('this.showToast(`Failed to load Research Bots data:', fetcher)

    def test_wallet_events_filter_failure_replaces_stale_activity_safely(self):
        fetch_start = self.app_js.index("    async fetchWalletEvents(opts = {}) {")
        fetch_end = self.app_js.index("    _wtUpdateFeedWalletSelect(list) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('document.getElementById("wt-feed-body")', fetcher)
        self.assertIn('const feedCount = document.getElementById("wt-feed-count")', fetcher)
        self.assertIn('feedCount.innerText = "Unavailable"', fetcher)
        self.assertIn('colspan="8"', fetcher)
        self.assertIn("Unable to refresh live wallet activity", fetcher)
        self.assertIn("this._escapeHtml(message)", fetcher)
        self.assertIn('if (!opts.silent) this.showToast', fetcher)

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

    def test_ml_experiment_detail_normalizes_metrics_before_formatting(self):
        renderer_start = self.app_js.index("async inspectMLExperiment(experimentId)")
        renderer_end = self.app_js.index("closeInspectMLModal()", renderer_start)
        renderer = self.app_js[renderer_start:renderer_end]
        for field in ("exp.threshold", "diag.roc_auc", "diag.brier_score", "baseTest.total_outcome_r", "mlTest.total_outcome_r", "baseTest.win_rate_pct", "mlTest.win_rate_pct", "baseTest.profit_factor", "mlTest.profit_factor"):
            self.assertIn(f"this._safeNumber({field}", renderer)
        self.assertIn("Number.isFinite(Number(diag.roc_auc))", renderer)


    def test_toast_notifications_expose_accessible_live_regions(self):
        toast_start = self.app_js.index("    showToast(message, type = \"info\")")
        toast_end = self.app_js.index("\n    // ----------------------------------------------------", toast_start)
        toast = self.app_js[toast_start:toast_end]
        self.assertIn('container.setAttribute("aria-live", "polite")', toast)
        self.assertIn('container.setAttribute("aria-relevant", "additions text")', toast)
        self.assertIn('toast.setAttribute("role", type === "error" ? "alert" : "status")', toast)
        self.assertIn('toast.setAttribute("aria-atomic", "true")', toast)
        self.assertIn('aria-label="Dismiss"', toast)


    def test_mobile_navigation_keeps_sections_reachable_without_sidebar(self):
        self.assertIn("@media (max-width: 767px)", self.dashboard_css)
        self.assertIn("body > aside nav {", self.dashboard_css)
        self.assertIn("overflow-x: auto;", self.dashboard_css)
        self.assertIn("body > aside nav .nav-btn {", self.dashboard_css)
        self.assertIn("min-height: 2.75rem;", self.dashboard_css)
        self.assertIn("body > aside > div:last-child {", self.dashboard_css)
        self.assertIn("dashboard_enhancements.css?v=20261017-2", self.index_html)



    def test_mobile_filters_and_data_tables_remain_usable(self):
        self.assertIn(".view-section .cfg-input", self.dashboard_css)
        self.assertIn("max-width: 100%;", self.dashboard_css)
        self.assertIn(".view-section .overflow-x-auto", self.dashboard_css)
        self.assertIn("overscroll-behavior-x: contain;", self.dashboard_css)
        self.assertIn("min-height: 2.5rem;", self.dashboard_css)
        self.assertIn("flex: 1 1 100%;", self.dashboard_css)
        self.assertIn("dashboard_enhancements.css?v=20261017-2", self.index_html)



    def test_icon_only_dashboard_buttons_have_accessible_names(self):
        self.assertIn('aria-label="Search cryptocurrency"', self.index_html)
        self.assertIn('aria-label="Close backfill dialog"', self.index_html)
        self.assertIn('aria-label="Close deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML run dialog"', self.index_html)
        self.assertIn('aria-label="Close ML deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML inspection dialog"', self.index_html)


if __name__ == "__main__":
    unittest.main()
