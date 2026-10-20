"""Static regression checks for security-sensitive dashboard rendering helpers."""

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "web" / "static" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "web" / "static" / "index.html"
DASHBOARD_CSS = REPO_ROOT / "web" / "static" / "css" / "dashboard_enhancements.css"
DASHBOARD_ENHANCEMENTS_JS = REPO_ROOT / "web" / "static" / "js" / "dashboard_enhancements.js"


class TestDashboardFrontendRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = APP_JS.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")
        cls.dashboard_enhancements_js = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")

    def test_auto_refresh_prevents_overlapping_poll_cycles(self):
        self.assertIn("autoRefreshInProgress: false", self.app_js)
        start = self.app_js.index("// Keep slow API responses from spawning overlapping polling cycles.")
        end = self.app_js.index("}, 15000);", start)
        polling = self.app_js[start:end]
        self.assertIn("if (!this.authToken || this.autoRefreshInProgress || document.hidden) return;", polling)
        self.assertIn("this.autoRefreshInProgress = true;", polling)
        self.assertIn("finally {", polling)
        self.assertIn("this.autoRefreshInProgress = false;", polling)
        self.assertIn("Promise.allSettled(refreshTasks)", polling)
        self.assertIn('result.status === "rejected"', polling)
        self.assertIn("Dashboard panel auto-refresh failed", polling)
        for method in ("fetchSystemStatus", "fetchPaperData", "fetchScreenerData", "fetchWalletData"):
            self.assertIn(f"this.{method}(", polling)
        self.assertIn("this.autoRefreshInProgress = false;", polling)

    def test_current_dashboard_navigation_item_is_announced_to_assistive_technology(self):
        switch_start = self.app_js.index("    switchTab(tabId) {")
        switch_end = self.app_js.index("\n    },", switch_start)
        switch_tab = self.app_js[switch_start:switch_end]
        self.assertIn('b.setAttribute("aria-current", "page")', switch_tab)
        self.assertIn('b.removeAttribute("aria-current")', switch_tab)
        self.assertIn('b.classList.toggle("active", isCurrent)', switch_tab)

    def test_initial_dashboard_navigation_state_matches_visible_home_view(self):
        nav = re.findall(r'<button\b[^>]*data-tab="([^"]+)"([^>]*)>', self.index_html)
        current = [(tab, attrs) for tab, attrs in nav if re.search(r'\baria-current="page"', attrs)]
        active = [(tab, attrs) for tab, attrs in nav if re.search(r'\bactive\b', attrs)]
        self.assertEqual([tab for tab, _ in current], ["home"])
        self.assertEqual([tab for tab, _ in active], ["home"])

    def test_dashboard_view_title_changes_are_announced_to_assistive_technology(self):
        title = re.search(r'<h1 id="view-header-title"([^>]*)>', self.index_html)
        self.assertIsNotNone(title)
        self.assertRegex(title.group(1), r'aria-live="polite"')
        self.assertRegex(title.group(1), r'aria-atomic="true"')

    def test_dashboard_view_sections_use_the_current_header_as_their_accessible_name(self):
        sections = re.findall(r'<section id="(view-[^"]+)"([^>]*)>', self.index_html)
        self.assertEqual(len(sections), 11)
        for section_id, attrs in sections:
            with self.subTest(section=section_id):
                self.assertRegex(attrs, r'aria-labelledby="view-header-title"')
        self.assertRegex(self.index_html, r'<h1 id="view-header-title"[^>]*>')

    def test_dashboard_view_sections_reference_the_current_header_description(self):
        sections = re.findall(r'<section id="(view-[^"]+)"([^>]*)>', self.index_html)
        self.assertEqual(len(sections), 11)
        for section_id, attrs in sections:
            with self.subTest(section=section_id):
                self.assertRegex(attrs, r'aria-describedby="view-header-desc"')
        self.assertRegex(self.index_html, r'<p id="view-header-desc"[^>]*>')

    def test_dashboard_navigation_controls_reference_matching_sections(self):
        nav = re.findall(r'<button\b[^>]*data-tab="([^"]+)"[^>]*aria-controls="([^"]+)"', self.index_html)
        sections = set(re.findall(r'<section id="(view-[^"]+)"', self.index_html))
        self.assertGreater(len(nav), 0)
        self.assertEqual(len(nav), len(set(tab for tab, _ in nav)))
        for tab, controls in nav:
            with self.subTest(tab=tab):
                self.assertEqual(controls, f"view-{tab}")
                self.assertIn(controls, sections)


    def test_sort_announcement_includes_table_row_count(self):
        self.assertIn("const rowCount = tbodyRows(table).length;", self.dashboard_enhancements_js)
        self.assertIn('const rowLabel = rowCount === 1 ? "row" : "rows";', self.dashboard_enhancements_js)
        self.assertIn("${rowCount} ${rowLabel}.", self.dashboard_enhancements_js)
        self.assertIn('statusRegion.setAttribute("aria-live", "polite")', self.dashboard_enhancements_js)

    def test_dashboard_auto_refresh_pauses_while_document_is_hidden(self):
        start = self.app_js.index("// Keep slow API responses from spawning overlapping polling cycles.")
        end = self.app_js.index("}, 15000);", start)
        polling = self.app_js[start:end]
        self.assertIn("document.hidden", polling)
        self.assertIn("if (!this.authToken || this.autoRefreshInProgress || document.hidden) return;", polling)

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
        self.assertIn('signalsData.signals.some(signal => !signal || typeof signal !== "object" || Array.isArray(signal))', fetcher)
        validation_end = fetcher.index('throw new Error("Invalid Market Screener response")')
        render_start = fetcher.index("this._renderScreenerStatus(status)")
        self.assertLess(validation_end, render_start)


    def test_wallet_settings_save_success_is_not_overwritten_by_refresh_failure(self):
        fetch_start = self.app_js.index("    async saveWalletSettings() {")
        fetch_end = self.app_js.index("    async fetchWalletData(opts = {}) {", fetch_start)
        saver = self.app_js[fetch_start:fetch_end]
        self.assertIn('throw new Error("Invalid Wallet Tracker settings save response")', saver)
        self.assertIn('this._wtSettingsLoaded = false', saver)
        self.assertIn('Settings saved, but status refresh failed:', saver)
        self.assertIn('this._wtRenderStatus(scannerStatus, tracked.tracked.length)', saver)
        self.assertLess(saver.index('this.showToast("Wallet scanner settings saved", "success")'),
                        saver.index('Settings saved, but status refresh failed:'))


    def test_wallet_settings_validates_optional_array_fields_before_mutating_controls(self):
        fetch_start = self.app_js.index("    async _wtLoadSettings() {")
        fetch_end = self.app_js.index("    async saveWalletSettings() {", fetch_start)
        loader = self.app_js[fetch_start:fetch_end]
        self.assertIn('throw new Error("Invalid Wallet Tracker settings response")', loader)
        self.assertIn('(s.networks !== undefined && !Array.isArray(s.networks))', loader)
        self.assertIn('(s.token_addresses !== undefined && !Array.isArray(s.token_addresses))', loader)
        validation_end = loader.index('throw new Error("Invalid Wallet Tracker settings response")')
        mutation_start = loader.index('set("wt-setting-enabled", s.enabled)')
        self.assertLess(validation_end, mutation_start)
        self.assertIn('this._wtSettingsLoaded = true', loader)


    def test_news_refresh_validates_all_payload_shapes_before_rendering(self):
        fetch_start = self.app_js.index("    async fetchNewsData() {")
        fetch_end = self.app_js.index("    _renderNewsKpis(stats) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("!validObject(stats)", fetcher)
        self.assertIn("!Array.isArray(stats.heartbeats)", fetcher)
        self.assertIn("!Array.isArray(signalsData.signals)", fetcher)
        self.assertIn("!Array.isArray(eventsData.events)", fetcher)
        self.assertIn('stats.heartbeats.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('signalsData.signals.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('eventsData.events.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        validation_end = fetcher.index('throw new Error("Invalid News AI response")')
        render_start = fetcher.index("this._renderNewsKpis(stats)")
        self.assertLess(validation_end, render_start)
        self.assertIn("Unable to refresh News AI signals", fetcher)
        self.assertIn("News AI event feed unavailable", fetcher)


    def test_research_bots_refresh_validates_payloads_before_rendering(self):
        fetch_start = self.app_js.index("    async fetchResearchData() {")
        fetch_end = self.app_js.index("    async fetchScreenerData() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("!validObject(positionsData)", fetcher)
        self.assertIn("!Array.isArray(positionsData.positions)", fetcher)
        self.assertIn("!validObject(rb2Stats)", fetcher)
        self.assertIn("!Array.isArray(rb2Stats.by_strategy)", fetcher)
        self.assertIn('positionsData.positions.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('rb2Stats.by_strategy.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        validation_end = fetcher.index('throw new Error("Invalid Research Bots response")')
        render_start = fetcher.index("this._renderRb2Kpis(rb2Stats)")
        self.assertLess(validation_end, render_start)
        self.assertIn("Unable to refresh Research Bots positions", fetcher)
        self.assertIn("Unable to refresh Research Bots strategy statistics", fetcher)


    def test_paper_refresh_validates_all_payload_shapes_before_mutating_snapshot(self):
        fetch_start = self.app_js.index("    async fetchPaperData() {")
        fetch_end = self.app_js.index("    renderPaperView() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("!Array.isArray(positionsData.positions)", fetcher)
        self.assertIn("!Array.isArray(tradesData.trades)", fetcher)
        self.assertIn("!Array.isArray(configsData.configs)", fetcher)
        self.assertIn("!validObject(metricsData.metrics)", fetcher)
        self.assertIn('positionsData.positions.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('tradesData.trades.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('configsData.configs.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        validation_end = fetcher.index('throw new Error("Invalid paper trading response")')
        assignments_start = fetcher.index("this.paperPositions = positionsData.positions")
        self.assertLess(validation_end, assignments_start)
        self.assertIn('if (posBody) posBody.innerHTML =', fetcher)
        self.assertIn('this.showToast(`Failed to load paper trading data:', fetcher)


    def test_wallet_refresh_failure_is_visible_and_escapes_api_error(self):
        fetch_start = self.app_js.index("    async fetchWalletData(opts = {}) {")
        fetch_end = self.app_js.index("    async fetchWalletEvents(opts = {}) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('statusEl.innerText = "DATA UNAVAILABLE"', fetcher)
        self.assertIn('detailEl.innerText = `Refresh failed: ${message}`', fetcher)
        self.assertIn("const safeMessage = this._escapeHtml(message)", fetcher)
        for expression in (
            'pos.positions.some(item => !item || typeof item !== "object" || Array.isArray(item))',
            'flow.flows.some(item => !item || typeof item !== "object" || Array.isArray(item))',
            'ev.events.some(item => !item || typeof item !== "object" || Array.isArray(item))',
            'tracked.tracked.some(item => !item || typeof item !== "object" || Array.isArray(item))',
        ):
            self.assertIn(expression, fetcher)
        events_start = self.app_js.index("    async fetchWalletEvents(opts = {}) {")
        events_end = self.app_js.index("    _wtUpdateFeedWalletSelect(list) {", events_start)
        events_fetcher = self.app_js[events_start:events_end]
        self.assertIn('throw new Error("Invalid Wallet Tracker events response")', events_fetcher)
        self.assertIn('this._wtRenderFeed(ev.events)', events_fetcher)
        self.assertLess(events_fetcher.index('throw new Error("Invalid Wallet Tracker events response")'), events_fetcher.index("this._wtRenderFeed(ev.events)"))
        for element_id, colspan in (("wt-flow-body", 8), ("wt-candidates-body", 13), ("wt-feed-body", 8)):
            self.assertIn(f'document.getElementById("{element_id}")', fetcher)
            self.assertIn(f"errorRow({colspan})", fetcher)
        self.assertIn('if (!opts.silent) this.showToast', fetcher)


    def test_admin_users_refresh_failure_clears_stale_rows_and_shows_error(self):
        fetch_start = self.app_js.index("    async fetchUsers()")
        fetch_end = self.app_js.index("    renderUsersTable()", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('if (!res.ok) throw new Error(`HTTP ${res.status}`)', fetcher)
        self.assertIn('!Array.isArray(data.users)', fetcher)
        self.assertIn('data.users.some(user => !user || typeof user !== "object" || Array.isArray(user))', fetcher)
        self.assertIn('this.usersList = []', fetcher)
        self.assertIn('getElementById("users-table-body")', fetcher)
        self.assertIn('role="status"', fetcher)
        self.assertIn('this.showToast("Failed to load user management data.', fetcher)

    def test_coverage_refresh_failure_clears_stale_data_and_shows_error(self):
        fetch_start = self.app_js.index("    async fetchCoverage()")
        fetch_end = self.app_js.index("    async fetchStrategies()", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn('if (!res.ok) throw new Error(`HTTP ${res.status}`)', fetcher)
        self.assertIn('!Array.isArray(data.coverage)', fetcher)
        self.assertIn('data.coverage.some(item => !item || typeof item !== "object" || Array.isArray(item))', fetcher)
        self.assertIn('Array.isArray(data.summary)', fetcher)
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
        self.assertIn('data.strategies.some(strategy => !strategy || typeof strategy !== "object" || Array.isArray(strategy))', fetcher)
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

    def test_recent_backtest_history_rejects_malformed_payload_before_rendering(self):
        fetch_start = self.app_js.index("    async fetchRecentBacktests() {")
        fetch_end = self.app_js.index("    renderMasterData() {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("!Array.isArray(data.runs)", fetcher)
        self.assertIn("data.runs.some(run => run === null || typeof run !== \"object\" || Array.isArray(run))", fetcher)
        validation_end = fetcher.index('throw new Error("Invalid backtest history response")')
        render_start = fetcher.index("if (data.runs.length === 0)")
        self.assertLess(validation_end, render_start)
        self.assertIn("Unable to refresh recent backtests", fetcher)


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

    def test_screener_refresh_rejects_malformed_payloads(self):
        fetch_start = self.app_js.index("    async fetchScreenerData()")
        fetch_end = self.app_js.index("    async fetchUsers()", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        self.assertIn("!Array.isArray(signalsData.signals)", fetcher)
        self.assertIn('throw new Error("Invalid Market Screener response")', fetcher)
        self.assertIn("const signals = signalsData.signals", fetcher)
        self.assertIn('statusEl.innerText = "DATA UNAVAILABLE"', fetcher)
        self.assertIn('this.showToast(`Failed to load Market Screener:', fetcher)

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


    def test_wallet_refresh_validates_all_payload_shapes_before_rendering(self):
        fetch_start = self.app_js.index("    async fetchWalletData(opts = {}) {")
        fetch_end = self.app_js.index("    async fetchWalletEvents(opts = {}) {", fetch_start)
        fetcher = self.app_js[fetch_start:fetch_end]
        for expected in (
            "Invalid Wallet Tracker response",
            "!validObject(status)",
            "!Array.isArray(pos.positions)",
            "!Array.isArray(flow.flows)",
            "!Array.isArray(ev.events)",
            "!Array.isArray(tracked.tracked)",
        ):
            self.assertIn(expected, fetcher)
        validation_end = fetcher.index('throw new Error("Invalid Wallet Tracker response")')
        render_start = fetcher.index("this._wtRenderStatus(status, tracked.tracked.length)")
        self.assertLess(validation_end, render_start)
        self.assertIn("Unable to refresh Wallet Tracking data", fetcher)
        self.assertIn("this._escapeHtml(message)", fetcher)


    def test_follow_wallet_validates_tracked_list_before_toggling(self):
        fetch_start = self.app_js.index("    async followWallet(wallet, chain) {")
        fetch_end = self.app_js.index("    async walletTrack(btn) {", fetch_start)
        follow = self.app_js[fetch_start:fetch_end]
        self.assertIn('!Array.isArray(payload.tracked)', follow)
        self.assertIn('throw new Error("Invalid tracked-wallet response")', follow)
        self.assertLess(follow.index('await getTrackedWallets()'), follow.index('"/api/wallets/tracked/toggle"'))
        self.assertIn('this._wtUpdateFeedWalletSelect(await getTrackedWallets())', follow)
        self.assertNotIn('(tracked.tracked || [])', follow)
        self.assertNotIn('(refreshed.tracked || [])', follow)


    def test_ml_dashboard_validates_payloads_and_surfaces_refresh_failures(self):
        fetch_start = self.app_js.index("    async fetchMLData() {")
        fetch_end = self.app_js.index("    async ", fetch_start + 15)
        fetcher = self.app_js[fetch_start:fetch_end]
        for expected in (
            "if (!cbRes.ok) throw new Error",
            "Invalid circuit-breaker response",
            "if (!modelsRes.ok) throw new Error",
            "Invalid ML models response",
            "if (!expRes.ok) throw new Error",
            "Invalid ML experiments response",
            'badge.innerText = "UNAVAILABLE"',
            'el.innerText = "Unavailable"',
            "Unable to refresh ML experiments",
            "this._escapeHtml(message)",
        ):
            self.assertIn(expected, fetcher)
        validation_end = fetcher.index('throw new Error("Invalid ML experiments response")')
        render_start = fetcher.index("const statExp = document.getElementById")
        self.assertLess(validation_end, render_start)


    def test_mobile_skip_link_target_clears_sticky_header(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("#main-content {", mobile_rules)
        self.assertIn("scroll-margin-top: 5rem;", mobile_rules)
        html = self.index_html
        self.assertIn('class="dashboard-skip-link" href="#main-content"', html)
        self.assertIn('id="main-content"', html)

    def test_mobile_viewport_uses_safe_areas_for_sticky_header_and_content(self):
        html = self.index_html
        self.assertIn('content="width=device-width, initial-scale=1.0, viewport-fit=cover"', html)
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        header_start = mobile_rules.index("body > div.flex-1.flex.flex-col.min-w-0 > header {")
        header_end = mobile_rules.index("\n    }", header_start)
        header = mobile_rules[header_start:header_end]
        self.assertIn("padding: max(0.75rem, env(safe-area-inset-top)) 1rem 0.75rem;", header)
        self.assertNotIn("padding: 0.75rem 1rem;", header)
        main_start = mobile_rules.index("body > div.flex-1.flex.flex-col.min-w-0 > main {")
        main_end = mobile_rules.index("\n    }", main_start)
        main = mobile_rules[main_start:main_end]
        self.assertIn("padding-right: max(1rem, env(safe-area-inset-right));", main)
        self.assertIn("padding-bottom: max(1rem, env(safe-area-inset-bottom));", main)
        self.assertIn("padding-left: max(1rem, env(safe-area-inset-left));", main)

    def test_mobile_toast_container_respects_safe_areas(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("#toast-container {", mobile_rules)
        self.assertIn("top: max(1rem, env(safe-area-inset-top)) !important;", mobile_rules)
        self.assertIn("right: max(1rem, env(safe-area-inset-right)) !important;", mobile_rules)
        self.assertIn("left: max(1rem, env(safe-area-inset-left)) !important;", mobile_rules)
        self.assertIn("width: auto !important;", mobile_rules)

    def test_mobile_toast_dismiss_button_has_44px_touch_target(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("#toast-container button {", mobile_rules)
        self.assertIn("min-width: 2.75rem !important;", mobile_rules)
        self.assertIn("min-height: 2.75rem !important;", mobile_rules)

    def test_toast_notifications_expose_accessible_live_regions(self):
        toast_start = self.app_js.index("    showToast(message, type = \"info\")")
        toast_end = self.app_js.index("\n    // ----------------------------------------------------", toast_start)
        toast = self.app_js[toast_start:toast_end]
        self.assertNotIn('container.setAttribute("aria-live"', toast)
        self.assertIn('"width:calc(100vw - 2rem)"', toast)
        self.assertIn('"max-width:22rem"', toast)
        self.assertIn('"flex:1;min-width:0;line-height:1.35;overflow-wrap:anywhere;word-break:break-word;"', toast)
        self.assertIn('"box-sizing:border-box"', toast)
        self.assertIn('"max-height:calc(100vh - 2rem)"', toast)
        self.assertIn('"max-height:calc(100dvh - 2rem)"', toast)
        self.assertIn('"overflow-y:auto"', toast)
        self.assertNotIn('container.setAttribute("aria-relevant"', toast)
        self.assertIn('toast.setAttribute("role", notificationType === "error" ? "alert" : "status")', toast)
        self.assertIn('"alert" : "status")', toast)
        self.assertIn('toast.setAttribute("aria-atomic", "true")', toast)
        self.assertIn('toast.setAttribute("aria-atomic", "true")', toast)
        self.assertIn('aria-label="Dismiss ${notificationType} notification"', toast)
        self.assertIn('const notificationType = Object.prototype.hasOwnProperty.call(palette, type) ? type : "info";', toast)
        self.assertIn('toast.setAttribute("role", notificationType === "error" ? "alert" : "status")', toast)
        self.assertIn("min-width:24px;min-height:24px;", toast)
        self.assertIn("display:flex;align-items:center;justify-content:center;", toast)
        self.assertIn('type="button"', toast)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)").matches', toast)
        self.assertIn('if (reduceMotion) {\n                toast.remove();\n                return;\n            }', toast)
        self.assertIn('let remainingMs = type === "error" ? 8000 : type === "warning" ? 6000 : 4500;', toast)
        self.assertIn('toast.addEventListener("mouseenter", pauseDismissTimer)', toast)
        self.assertIn('toast.addEventListener("mouseleave", resumeDismissTimer)', toast)
        self.assertIn('toast.addEventListener("focusin", pauseDismissTimer)', toast)
        self.assertIn('toast.addEventListener("focusout", resumeDismissTimer)', toast)
        self.assertIn('remainingMs = Math.max(0, remainingMs - (Date.now() - timerStartedAt));', toast)
        self.assertIn('if (toast.matches(":hover") || toast.contains(document.activeElement)) return;', toast)


    def test_toast_dismiss_button_has_visible_keyboard_focus(self):
        self.assertIn("#toast-container button:focus-visible", self.dashboard_css)
        self.assertIn("outline: 2px solid var(--dashboard-focus, #38bdf8);", self.dashboard_css)
        self.assertIn("outline-offset: 2px;", self.dashboard_css)
        self.assertIn("border-radius: 0.25rem;", self.dashboard_css)


    def test_mobile_navigation_keeps_sections_reachable_without_sidebar(self):
        self.assertIn("@media (max-width: 767px)", self.dashboard_css)
        self.assertIn("body > aside nav {", self.dashboard_css)
        self.assertIn("overflow-x: auto;", self.dashboard_css)
        self.assertIn("body > aside nav .nav-btn {", self.dashboard_css)
        self.assertIn("min-height: 2.75rem;", self.dashboard_css)
        self.assertIn("body > aside > div:last-child {", self.dashboard_css)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.css\?v=[^\"]+")

    def test_theme_defaults_to_system_preference_without_overriding_saved_choice(self):
        theme_start = self.dashboard_enhancements_js.index("    function systemPreference()")
        theme_end = self.dashboard_enhancements_js.index("    function applyTheme(theme)", theme_start)
        theme_logic = self.dashboard_enhancements_js[theme_start:theme_end]
        self.assertIn('window.matchMedia("(prefers-color-scheme: light)")', theme_logic)
        self.assertIn('saved === "light" || saved === "dark" ? saved : systemPreference()', theme_logic)
        self.assertIn("catch (_) {\n            return systemPreference();\n        }", theme_logic)

    def test_dashboard_table_cells_remain_selectable_for_copying(self):
        self.assertIn(".dashboard-table-scroll td,", self.dashboard_css)
        self.assertIn(".dashboard-table-scroll th {", self.dashboard_css)
        self.assertIn("-webkit-user-select: text;", self.dashboard_css)
        self.assertIn("user-select: text;", self.dashboard_css)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.css\?v=[^\"]+")

    def test_dynamic_table_rows_keep_stable_sort_order(self):
        self.assertIn("let nextOriginalIndex = rows.reduce((max, row) => {", self.dashboard_enhancements_js)
        self.assertIn("Number.isFinite(value) ? Math.max(max, value) : max", self.dashboard_enhancements_js)
        self.assertIn("row.dataset.originalIndex = String(nextOriginalIndex++);", self.dashboard_enhancements_js)
        self.assertIn("Assign newly inserted rows indices after all existing rows", self.dashboard_enhancements_js)

    def test_sortable_tables_announce_sort_changes_to_screen_readers(self):
        self.assertIn('statusRegion.setAttribute("role", "status")', self.dashboard_enhancements_js)
        self.assertIn('statusRegion.setAttribute("aria-live", "polite")', self.dashboard_enhancements_js)
        self.assertIn('statusRegion.setAttribute("aria-atomic", "true")', self.dashboard_enhancements_js)
        self.assertIn('const tableLabel = table.getAttribute("aria-label") || (table.caption && table.caption.textContent.trim()) || table.dataset.label;', self.dashboard_enhancements_js)
        self.assertIn('statusRegion.textContent = `Sorted${tableLabel ? ` ${tableLabel}` : ""} by ${label} ${direction === 1 ? "ascending" : "descending"}. ${rowCount} ${rowLabel}.`;', self.dashboard_enhancements_js)
        self.assertIn(".dashboard-sort-status {", self.dashboard_css)
        self.assertIn("clip: rect(0, 0, 0, 0);", self.dashboard_css)

    def test_sortable_table_headers_describe_keyboard_activation(self):
        self.assertIn('th.setAttribute("aria-description", "Press Enter or Space to sort this column.")', self.dashboard_enhancements_js)
        self.assertIn('if (!th.hasAttribute("scope")) th.setAttribute("scope", "col");', self.dashboard_enhancements_js)
        self.assertIn('if ((event.key === "Enter" || event.key === " ") && !event.repeat)', self.dashboard_enhancements_js)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.js\?v=[^\"]+")

    def test_horizontal_table_scroll_region_is_keyboard_accessible(self):
        self.assertIn('bar.setAttribute("role", "region")', self.dashboard_enhancements_js)
        self.assertIn('bar.setAttribute("tabindex", "0")', self.dashboard_enhancements_js)
        self.assertIn('table.getAttribute("aria-label")', self.dashboard_enhancements_js)
        self.assertIn('table.caption.textContent.trim()', self.dashboard_enhancements_js)
        self.assertIn('table.dataset.label', self.dashboard_enhancements_js)
        self.assertIn('bar.setAttribute("aria-label", "Horizontal scroll for " + tableLabel)', self.dashboard_enhancements_js)
        self.assertIn('bar.setAttribute("aria-description", "Use the left and right arrow keys to scroll horizontally.")', self.dashboard_enhancements_js)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.js\?v=[^\"]+")

    def test_ml_data_refresh_button_has_explicit_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh ML data" onclick="App.fetchMLData()"', self.index_html)
        self.assertIn('title="Refresh ML Data"', self.index_html)

    def test_database_coverage_refresh_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh database coverage" onclick="App.fetchCoverage(); App.renderCoverageTable();"', self.index_html)
        self.assertIn('Database Coverage & Feature Status', self.index_html)

    def test_news_refresh_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh news data" onclick="App.fetchNewsData()"', self.index_html)
        self.assertIn('fetchNewsData()', self.index_html)

    def test_research_refresh_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh research data" onclick="App.fetchResearchData()"', self.index_html)

    def test_screener_refresh_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh screener data" onclick="App.fetchScreenerData()"', self.index_html)

    def test_wallet_refresh_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Refresh wallet data" onclick="App.fetchWalletData()"', self.index_html)

    def test_circuit_breaker_controls_have_contextual_accessible_names(self):
        self.assertIn('<button type="button" aria-label="Activate emergency stop" onclick="App.tripCircuitBreaker()"', self.index_html)
        self.assertIn('<button type="button" aria-label="Reset circuit breaker" onclick="App.resetCircuitBreaker()"', self.index_html)

    def test_battle_royale_rerun_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Re-run battle royale simulation" onclick="App.runBattleRoyale()"', self.index_html)

    def test_new_strategy_draft_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Create new strategy draft" onclick="App.newStrategyDraft()"', self.index_html)

    def test_battle_royale_run_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Run all models in battle royale" id="btn-run-battle-royale" onclick="App.runBattleRoyale()"', self.index_html)

    def test_selected_strategy_backtest_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Run selected strategy backtest" id="btn-run-backtest" onclick="App.runBacktest()"', self.index_html)

    def test_paper_market_tick_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Evaluate paper trading market tick" id="btn-sync-paper" onclick="App.syncPaperMarketTick()"', self.index_html)

    def test_telegram_test_ping_button_has_contextual_accessible_name(self):
        self.assertIn('<button type="button" aria-label="Send Telegram bot test ping" onclick="App.sendTelegramTestPing()" id="btn-tg-test"', self.index_html)

    def test_workflow_dialog_cancel_and_close_buttons_do_not_submit_forms(self):
        for handler in (
            "closeBackfillModal",
            "closeDeployModal",
            "closeRunMLModal",
            "closeDeployMLModal",
            "closeInspectMLModal",
        ):
            self.assertIn(f'<button type="button" onclick="App.{handler}()"', self.index_html)

    def test_strategy_builder_helper_buttons_do_not_submit_forms(self):
        for button in (
            '<button type="button" onclick="App.useStrategyPromptChip(\'Create an aggressive momentum strategy',
            '<button type="button" onclick="App.useStrategyPromptChip(\'Create an oversold mean reversion strategy',
            '<button type="button" onclick="App.useStrategyPromptChip(\'Create a support bounce strategy',
            '<button type="button" onclick="App.addRuleRow(\'long\')"',
            '<button type="button" onclick="App.addRuleRow(\'short\')"',
            '<button type="button" onclick="App.runQuickTestCustomStrategy()"',
        ):
            self.assertIn(button, self.index_html)

    def test_workflow_dialog_primary_actions_do_not_submit_forms(self):
        for button in (
            '<button type="button" onclick="App.submitBackfill()"',
            '<button type="button" id="btn-confirm-deploy" onclick="App.confirmDeploy()"',
            '<button type="button" id="btn-submit-ml-run" onclick="App.submitMLRun()"',
            '<button type="button" id="btn-confirm-deploy-ml" onclick="App.confirmDeployML()"',
        ):
            self.assertIn(button, self.index_html)

    def test_all_static_dashboard_buttons_have_explicit_types(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for match in re.finditer(r"<button\b[^>]*>", html, re.IGNORECASE | re.DOTALL):
            with self.subTest(button=match.group(0)[:120]):
                self.assertRegex(match.group(0), r"\btype\s*=\s*[\"'](?:button|submit|reset)[\"']", "Every button should declare its intended type")

    def test_mobile_filters_and_data_tables_remain_usable(self):
        self.assertIn(".view-section .cfg-input", self.dashboard_css)
        self.assertIn("max-width: 100%;", self.dashboard_css)
        self.assertIn(".view-section .overflow-x-auto", self.dashboard_css)
        self.assertIn("overscroll-behavior-x: contain;", self.dashboard_css)
        self.assertIn("min-height: 2.75rem;", self.dashboard_css)
        self.assertIn("flex: 1 1 100%;", self.dashboard_css)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.css\?v=[^\"]+")

    def test_mobile_dashboard_header_actions_meet_minimum_touch_target_height(self):
        self.assertIn("body > div.flex-1.flex.flex-col.min-w-0 > header > div:last-child > button {", self.dashboard_css)
        self.assertIn("min-height: 2.75rem;", self.dashboard_css)
        self.assertIn("@media (max-width: 767px)", self.dashboard_css)

    def test_reduced_motion_preference_disables_dashboard_css_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.dashboard_css)
        self.assertIn("animation-duration: 0.01ms !important;", self.dashboard_css)
        self.assertIn("transition-duration: 0.01ms !important;", self.dashboard_css)
        self.assertIn("scroll-behavior: auto !important;", self.dashboard_css)

    def test_keyboard_focus_indicators_cover_interactive_controls(self):
        self.assertIn(":focus-visible", self.dashboard_css)
        self.assertIn("outline: 2px solid var(--dashboard-focus, #38bdf8);", self.dashboard_css)
        self.assertIn(':root[data-theme="light"]', self.dashboard_css)
        self.assertIn("--dashboard-focus: #0369a1;", self.dashboard_css)
        self.assertIn("--dashboard-focus: #38bdf8;", self.dashboard_css)
        self.assertIn("outline-offset: 2px;", self.dashboard_css)
        for control in ("button", "a", "input", "select", "textarea", "[tabindex]"):
            self.assertIn(control, self.dashboard_css)

    def test_escape_dismisses_visible_workflow_dialogs_without_bypassing_login(self):
        escape_handler = self.app_js[self.app_js.index('document.addEventListener("keydown", (event) => {', self.app_js.index("window.App = App;")):]
        self.assertIn('if (event.key !== "Escape") return;', escape_handler)
        self.assertIn('["modal-inspect-ml", "closeInspectMLModal"]', escape_handler)
        self.assertIn('["modal-deploy-ml", "closeDeployMLModal"]', escape_handler)
        self.assertIn('["modal-run-ml", "closeRunMLModal"]', escape_handler)
        self.assertIn('["deploy-modal", "closeDeployModal"]', escape_handler)
        self.assertIn('["backfill-modal", "closeBackfillModal"]', escape_handler)
        self.assertIn('modal && !modal.classList.contains("hidden")', escape_handler)
        self.assertNotIn('"login-modal"', escape_handler)

    def test_workflow_modal_wrappers_support_small_screens_and_safe_areas(self):
        for modal_id in (
            "login-modal",
            "backfill-modal",
            "deploy-modal",
            "modal-run-ml",
            "modal-deploy-ml",
            "modal-inspect-ml",
        ):
            modal_start = self.index_html.index(f'id="{modal_id}"')
            wrapper_end = self.index_html.index(">", modal_start)
            wrapper = self.index_html[modal_start:wrapper_end + 1]
            self.assertIn("overflow-y-auto", wrapper, modal_id)
            self.assertIn("env(safe-area-inset-top)", wrapper, modal_id)
            self.assertIn("env(safe-area-inset-bottom)", wrapper, modal_id)

    def test_modal_dialogs_have_accessible_names_and_modal_semantics(self):
        for modal_id, title_id in (
            ("login-modal", "login-modal-title"),
            ("backfill-modal", "backfill-modal-title"),
            ("deploy-modal", "deploy-modal-title"),
            ("modal-run-ml", "modal-run-ml-title"),
            ("modal-deploy-ml", "modal-deploy-ml-title"),
            ("modal-inspect-ml", "inspect-ml-title"),
        ):
            self.assertIn(f'id="{modal_id}"', self.index_html)
            self.assertIn(f'aria-labelledby="{title_id}"', self.index_html)
            self.assertIn(f'id="{title_id}"', self.index_html)
        self.assertGreaterEqual(self.index_html.count('role="dialog" aria-modal="true"'), 6)

    def test_icon_only_dashboard_buttons_have_accessible_names(self):
        self.assertIn('aria-label="Search cryptocurrency"', self.index_html)
        self.assertIn('aria-label="Close backfill dialog"', self.index_html)
        self.assertIn('aria-label="Close deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML run dialog"', self.index_html)
        self.assertIn('aria-label="Close ML deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML inspection dialog"', self.index_html)


    def test_theme_toggle_is_named_for_its_action_not_exposed_as_a_pressed_toggle(self):
        theme_start = self.dashboard_enhancements_js.index("    function applyTheme(theme)")
        theme_end = self.dashboard_enhancements_js.index("    function mountToggle()", theme_start)
        apply_theme = self.dashboard_enhancements_js[theme_start:theme_end]
        self.assertIn('button.setAttribute("aria-label", "Switch to " + nextTheme + " theme");', apply_theme)
        self.assertIn('button.setAttribute("title", "Switch to " + nextTheme + " theme");', apply_theme)
        self.assertNotIn("aria-pressed", apply_theme)

    def test_theme_tracks_system_changes_until_user_selects_a_theme(self):
        theme_start = self.dashboard_enhancements_js.index("    function initTheme()")
        theme_end = self.dashboard_enhancements_js.index("\n    if (document.readyState", theme_start)
        theme_init = self.dashboard_enhancements_js[theme_start:theme_end]
        self.assertIn('window.matchMedia && window.matchMedia("(prefers-color-scheme: light)")', theme_init)
        self.assertIn('mediaQuery.addEventListener("change", onSystemThemeChange)', theme_init)
        self.assertIn('mediaQuery.addListener(onSystemThemeChange)', theme_init)
        self.assertIn("if (userOverride) return;", theme_init)
        self.assertIn('if (saved === "light" || saved === "dark") return;', theme_init)
        self.assertIn("applyTheme(systemPreference());", theme_init)
        self.assertIn('document.addEventListener("click", event => {', theme_init)
        self.assertIn('event.target.closest("#dashboard-theme-toggle")', theme_init)
        self.assertNotIn('button.addEventListener("click", () => { userOverride = true; });', theme_init)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.js\?v=[^\"]+")

    def test_skip_link_bypasses_sidebar_navigation(self):
        self.assertIn('<a class="dashboard-skip-link" href="#main-content">Skip to main content</a>', self.index_html)
        self.assertIn('<main id="main-content" tabindex="-1"', self.index_html)
        self.assertIn(".dashboard-skip-link:focus", self.dashboard_css)
        self.assertIn("transform: translateY(0);", self.dashboard_css)
        self.assertRegex(self.index_html, r"dashboard_enhancements\.css\?v=[^\"]+")

    def test_keyboard_focus_indicator_is_visible_and_supports_forced_colors(self):
        self.assertIn(":focus-visible", self.index_html)
        self.assertIn("outline: 2px solid var(--dashboard-focus, #38bdf8) !important;", self.index_html)
        self.assertIn("outline-offset: 3px;", self.index_html)
        self.assertIn("@media (forced-colors: active)", self.index_html)
        self.assertIn("outline-color: Highlight !important;", self.index_html)

    def test_reduced_motion_preference_disables_nonessential_css_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.index_html)
        self.assertIn("scroll-behavior: auto !important;", self.index_html)
        self.assertIn("animation-duration: 0.01ms !important;", self.index_html)
        self.assertIn("animation-iteration-count: 1 !important;", self.index_html)
        self.assertIn("transition-duration: 0.01ms !important;", self.index_html)

    def test_mobile_login_controls_meet_44px_touch_target(self):
        css = self.dashboard_css
        mobile_start = css.index("/* Mobile-first navigation:")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("#login-modal input,", mobile_rules)
        self.assertIn("#login-modal button {", mobile_rules)
        self.assertIn("min-height: 2.75rem;", mobile_rules)

    def test_mobile_dialogs_fit_short_viewports_and_scroll_internally(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn('.fixed.inset-0 [role="dialog"] {', mobile_rules)
        self.assertIn("max-height: calc(100vh - 2rem);", mobile_rules)
        self.assertIn("overflow-y: auto;", mobile_rules)
        self.assertIn("@supports (height: 100dvh)", mobile_rules)
        self.assertIn("max-height: calc(100dvh - 2rem);", mobile_rules)

    def test_mobile_modal_actions_have_44px_touch_targets(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 767px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn('.fixed.inset-0 [role="dialog"] button {', mobile_rules)
        self.assertIn("min-height: 2.75rem;", mobile_rules)
        self.assertIn('button[aria-label*="Close"]', mobile_rules)
        self.assertIn("min-width: 2.75rem;", mobile_rules)

    def test_mobile_backtest_actions_meet_44px_touch_target(self):
        css = self.dashboard_css
        mobile_start = css.index("/* Mobile-first navigation:")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("#btn-run-battle-royale,", mobile_rules)
        self.assertIn("#btn-run-backtest {", mobile_rules)
        self.assertIn("min-height: 2.75rem;", mobile_rules)

    def test_mobile_sortable_headers_have_larger_touch_area(self):
        css = self.dashboard_css
        mobile_start = css.index("/* Consistent control sizing")
        mobile_rules = css[mobile_start:]
        self.assertIn(".table-sortable-header {", mobile_rules)
        self.assertIn("padding-top: 0.75rem;", mobile_rules)
        self.assertIn("padding-bottom: 0.75rem;", mobile_rules)

    def test_mobile_dashboard_header_can_shrink_for_long_titles(self):
        css = self.dashboard_css
        mobile_start = css.index("@media (max-width: 768px)")
        mobile_end = css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = css[mobile_start:mobile_end]
        self.assertIn("header > div:first-child {", mobile_rules)
        self.assertIn("flex: 1 1 auto;", mobile_rules)
        self.assertIn("min-width: 0;", mobile_rules)
        self.assertIn("overflow-wrap: anywhere;", mobile_rules)

    def test_dashboard_navigation_has_accessible_landmark_name(self):
        self.assertIn('<nav aria-label="Dashboard sections"', self.index_html)

    def test_login_fields_have_accessible_labels_and_password_manager_hints(self):
        self.assertRegex(
            self.index_html,
            r'<label for="login-username"[^>]*>Username</label>',
        )
        self.assertRegex(
            self.index_html,
            r'<input type="text" id="login-username"[^>]*name="username"[^>]*autocomplete="username"[^>]*required',
        )
        self.assertRegex(
            self.index_html,
            r'<label for="login-password"[^>]*>Password</label>',
        )
        self.assertRegex(
            self.index_html,
            r'<input type="password" id="login-password"[^>]*name="password"[^>]*autocomplete="current-password"[^>]*required',
        )

    def test_login_error_message_is_announced_accessibly(self):
        self.assertRegex(
            self.index_html,
            r'<div id="login-error-msg"[^>]*role="alert"[^>]*aria-live="assertive"[^>]*aria-atomic="true"',
        )


    def test_login_error_is_described_by_both_credential_fields(self):
        self.assertRegex(
            self.index_html,
            r'<input type="text" id="login-username"[^>]*aria-describedby="login-error-msg"',
        )
        self.assertRegex(
            self.index_html,
            r'<input type="password" id="login-password"[^>]*aria-describedby="login-error-msg"',
        )


    def test_workflow_modals_close_on_backdrop_click_without_closing_from_panel_clicks(self):
        start = self.app_js.index('// Allow clicks on the backdrop (not the dialog panel) to dismiss workflow dialogs.')
        end = self.app_js.index('// Allow Escape to dismiss the topmost visible workflow dialog.', start)
        backdrop_handler = self.app_js[start:end]
        self.assertIn('document.addEventListener("click", (event) => {', backdrop_handler)
        self.assertIn('event.target === modal', backdrop_handler)
        self.assertIn('!modal.classList.contains("hidden")', backdrop_handler)
        self.assertIn('App[closeMethod]();', backdrop_handler)
        for modal_id in ("modal-inspect-ml", "modal-deploy-ml", "modal-run-ml", "deploy-modal", "backfill-modal"):
            self.assertIn(f'["{modal_id}",', backdrop_handler)
        self.assertIn("app.js?v=20261020-27", self.index_html)

    def test_workflow_modals_restore_focus_to_the_opening_control(self):
        self.assertIn("rememberModalReturnFocus(modal)", self.app_js)
        self.assertIn("modal._returnFocusElement = activeElement", self.app_js)
        self.assertIn("const returnFocus = modal?._returnFocusElement", self.app_js)
        self.assertIn("returnFocus.isConnected && !returnFocus.disabled", self.app_js)
        self.assertIn("returnFocus.focus()", self.app_js)
        for close_method in (
            "closeBackfillModal()", "closeRunMLModal()", "closeDeployMLModal()",
            "closeDeployModal()", "closeInspectMLModal()",
        ):
            start = self.app_js.index(f"    {close_method} {{")
            end = self.app_js.index("\n    },", start)
            self.assertIn("this.restoreModalReturnFocus(modal)", self.app_js[start:end])
        self.assertIn("app.js?v=20261020-27", self.index_html)

    def test_workflow_modals_trap_tab_and_focus_an_initial_control(self):
        self.assertIn("getModalFocusableElements(modal)", self.app_js)
        self.assertIn("initializeModalFocusTrap(modal)", self.app_js)
        self.assertIn('if (event.key !== "Tab") return;', self.app_js)
        self.assertIn("event.shiftKey", self.app_js)
        self.assertIn("last.focus()", self.app_js)
        self.assertIn("first.focus()", self.app_js)
        self.assertIn("this.focusModalInitialControl(modal)", self.app_js)
        self.assertIn('modal.dataset.focusTrapInitialized = "1"', self.app_js)
        self.assertIn("app.js?v=20261020-27", self.index_html)

    def test_login_modal_places_focus_on_username_when_opened(self):
        start = self.app_js.index("    showLoginModal()")
        end = self.app_js.index("    hideLoginModal()", start)
        show_modal = self.app_js[start:end]
        self.assertIn('modal.classList.remove("hidden")', show_modal)
        self.assertIn('document.getElementById("login-username")?.focus()', show_modal)

    def test_login_modal_remembers_and_restores_invoking_control_focus(self):
        start = self.app_js.index("    showLoginModal()")
        end = self.app_js.index("    async handleLogin()", start)
        modal_methods = self.app_js[start:end]
        self.assertIn("this.loginModalReturnFocus = activeElement", modal_methods)
        self.assertIn("activeElement !== document.body", modal_methods)
        self.assertIn("const returnFocus = this.loginModalReturnFocus", modal_methods)
        self.assertIn("returnFocus.isConnected && !returnFocus.disabled", modal_methods)
        self.assertIn("returnFocus.focus()", modal_methods)
        self.assertIn("this.loginModalReturnFocus = null", modal_methods)

    def test_login_modal_traps_keyboard_focus_until_closed(self):
        start = self.app_js.index("    showLoginModal()")
        end = self.app_js.index("    hideLoginModal()", start)
        show_modal = self.app_js[start:end]
        self.assertIn('modal.dataset.focusTrapInitialized !== "true"', show_modal)
        self.assertIn('modal.addEventListener("keydown", (event) => {', show_modal)
        self.assertIn('if (event.key !== "Tab") return;', show_modal)
        self.assertIn('event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))', show_modal)
        self.assertIn('!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))', show_modal)
        self.assertIn('last.focus();', show_modal)
        self.assertIn('first.focus();', show_modal)

    def test_reduced_motion_disables_dashboard_sort_header_transition(self):
        css = self.dashboard_css
        reduced_motion_start = css.index("@media (prefers-reduced-motion: reduce)")
        reduced_motion_end = css.index("\n}", reduced_motion_start) + 2
        reduced_motion = css[reduced_motion_start:reduced_motion_end]
        self.assertIn(".table-sortable-header", reduced_motion)
        self.assertIn("transition: none;", reduced_motion)

    def test_theme_toggle_is_remounted_if_dashboard_sidebar_is_rebuilt(self):
        init_start = self.dashboard_enhancements_js.index("    function initTheme()")
        init_end = self.dashboard_enhancements_js.index("\n    if (document.readyState === \"loading\")", init_start)
        init_theme = self.dashboard_enhancements_js[init_start:init_end]
        self.assertIn("const observeCurrentSidebar = () => {", init_theme)
        self.assertIn('document.querySelector("body > aside")', init_theme)
        self.assertIn('sidebarObserver.observe(sidebar, { childList: true, subtree: true });', init_theme)
        self.assertIn("sidebarRootObserver.observe(document.body, { childList: true });", init_theme)
        self.assertIn('if (!document.getElementById("dashboard-theme-toggle")) mountToggle();', init_theme)
        self.assertIn('event.target.closest("#dashboard-theme-toggle")', init_theme)
        self.assertIn('event.target.closest("#dashboard-theme-toggle")', init_theme)
        self.assertNotIn('button.addEventListener("click", () => { userOverride = true; });', init_theme)
        self.assertIn("function mountToggle()", self.dashboard_enhancements_js)
        self.assertIn("applyTheme(root.dataset.theme || readPreference());", self.dashboard_enhancements_js)


class TestDashboardForcedColorsAccessibility(unittest.TestCase):
    def test_forced_colors_preserves_focus_and_scroll_region_affordances(self):
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        forced_start = css.index("@media (forced-colors: active)")
        forced_colors = css[forced_start:]
        self.assertIn("outline-color: Highlight;", forced_colors)
        self.assertIn("color: HighlightText;", forced_colors)
        self.assertIn("background-color: Highlight;", forced_colors)
        self.assertIn("background: Canvas;", forced_colors)
        self.assertIn("color: CanvasText;", forced_colors)
        self.assertIn(".dashboard-horizontal-scroll", forced_colors)
        self.assertIn("border: 1px solid CanvasText;", forced_colors)



class TestDashboardThemeToggleTouchTarget(unittest.TestCase):
    def test_theme_toggle_meets_minimum_touch_target_dimensions(self):
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        toggle_start = css.index(".dashboard-theme-toggle {")
        toggle_end = css.index("\n}", toggle_start)
        toggle_rules = css[toggle_start:toggle_end]
        self.assertIn("min-height: 2.75rem;", toggle_rules)
        self.assertIn("min-width: 2.75rem;", toggle_rules)



class TestToastVisibilityAccessibility(unittest.TestCase):
    def test_toast_auto_dismiss_pauses_while_document_is_hidden(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        toast_start = app_js.index("    showToast(message, type = \"info\") {")
        toast_end = app_js.index("\n    // ----------------------------------------------------\n    // NEWS AI OVERLAY SIGNALS", toast_start)
        toast = app_js[toast_start:toast_end]
        self.assertIn("dismissTimer !== null || document.hidden", toast)
        self.assertIn("if (document.hidden) pauseDismissTimer();", toast)
        self.assertIn("document.addEventListener(\"visibilitychange\", handleVisibilityChange)", toast)
        self.assertIn("document.removeEventListener(\"visibilitychange\", handleVisibilityChange)", toast)
        self.assertIn("remainingMs = Math.max(0, remainingMs - (Date.now() - timerStartedAt));", toast)


class TestWorkflowDialogEscapeStacking(unittest.TestCase):
    def test_escape_closes_only_the_topmost_visible_workflow_dialog(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        handler_start = app_js.index('document.addEventListener("keydown", (event) => {', app_js.index("window.App = App;"))
        handler = app_js[handler_start:]
        self.assertIn(".filter(({ modal }) => modal && !modal.classList.contains(\"hidden\"))", handler)
        self.assertIn("window.getComputedStyle(a.modal).zIndex", handler)
        self.assertIn("window.getComputedStyle(b.modal).zIndex", handler)
        self.assertIn("a.modal.compareDocumentPosition(b.modal) & Node.DOCUMENT_POSITION_FOLLOWING", handler)
        self.assertIn("const topmostModal = visibleModals[visibleModals.length - 1];", handler)
        self.assertIn("App[topmostModal.closeMethod]();", handler)
        self.assertNotIn('App[closeMethod]();\n            event.preventDefault();\n            return;', handler)
        self.assertNotIn('"login-modal"', handler)


class TestDashboardEnhancementObserver(unittest.TestCase):
    def test_mutation_observer_enhances_only_added_table_subtrees(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        init_start = enhancements.index("    function init() {")
        init_end = enhancements.index("\n    if (document.readyState === \"loading\")", init_start)
        init = enhancements[init_start:init_end]
        self.assertIn("new MutationObserver(mutations => {", init)
        self.assertIn("for (const mutation of mutations)", init)
        self.assertIn("for (const node of mutation.addedNodes)", init)
        self.assertIn("node.matches(SORTABLE_TABLE_SELECTOR)", init)
        self.assertIn("node.querySelectorAll(SORTABLE_TABLE_SELECTOR).forEach(table => tablesToEnhance.add(table))", init)
        self.assertIn("const tablesToEnhance = new Set();", init)
        self.assertIn("tablesToEnhance.forEach(enhanceTable);", init)
        self.assertNotIn("enhanceAllTables();\n            installTelegramPing();", init)
        self.assertIn("installTelegramPing();", init)


class TestDashboardResizeScheduling(unittest.TestCase):
    def test_window_resize_enhancement_is_coalesced_per_animation_frame(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        init_start = enhancements.index("    function init() {")
        init_end = enhancements.index("\n    if (document.readyState === \"loading\")", init_start)
        init = enhancements[init_start:init_end]
        self.assertIn("let resizeFrame = null;", init)
        self.assertIn('window.addEventListener("resize", () => {', init)
        self.assertIn("if (resizeFrame !== null) return;", init)
        self.assertIn("window.requestAnimationFrame(() => {", init)
        self.assertIn("resizeFrame = null;\n                enhanceAllTables();", init)


class TestDashboardMutationBatchDeduplication(unittest.TestCase):
    def test_added_table_subtrees_are_deduplicated_per_observer_batch(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        init_start = enhancements.index("    function init() {")
        init_end = enhancements.index("\n    if (document.readyState === \"loading\")", init_start)
        init = enhancements[init_start:init_end]
        self.assertIn("const tablesToEnhance = new Set();", init)
        self.assertIn("tablesToEnhance.add(node)", init)
        self.assertIn("tablesToEnhance.add(table)", init)
        self.assertIn("tablesToEnhance.forEach(enhanceTable);", init)


class TestDashboardHorizontalScrollResizeFallback(unittest.TestCase):
    def test_resize_refreshes_existing_horizontal_scrollbars_without_reenhancing_tables(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        self.assertIn('function syncHorizontalScrollbars(tables) {', enhancements)
        self.assertIn('Array.from(document.querySelectorAll(".dashboard-horizontal-scroll"))', enhancements)
        self.assertIn('viewport.querySelector(":scope > table")', enhancements)
        init_start = enhancements.index("    function init() {")
        init_end = enhancements.index("\n    if (document.readyState === \"loading\")", init_start)
        init = enhancements[init_start:init_end]
        self.assertIn("syncHorizontalScrollbars();", init)
        self.assertIn("enhanceAllTables();\n                // ResizeObserver is not available", init)


class TestDashboardResizeObserverLifecycle(unittest.TestCase):
    def test_table_resize_observer_is_shared_and_released_when_table_is_removed(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        observer_start = enhancements.index("if (window.ResizeObserver) {")
        observer_end = enhancements.index("            syncWidth();", observer_start)
        observer_setup = enhancements[observer_start:observer_end]
        self.assertIn("const resizeObserver = new ResizeObserver(syncWidth);", observer_setup)
        self.assertIn("resizeObserver.observe(viewport);", observer_setup)
        self.assertIn("resizeObserver.observe(table);", observer_setup)
        self.assertIn("table._dashboardResizeObserver = resizeObserver;", observer_setup)
        self.assertEqual(observer_setup.count("new ResizeObserver("), 1)

        init_start = enhancements.index("    function init() {")
        init_end = enhancements.index("\n    if (document.readyState === \"loading\")", init_start)
        init = enhancements[init_start:init_end]
        self.assertIn("for (const node of mutation.removedNodes)", init)
        self.assertIn("if (table.isConnected || !table._dashboardResizeObserver) return;", init)
        self.assertIn("table._dashboardResizeObserver.disconnect();", init)
        self.assertIn("delete table._dashboardResizeObserver;", init)


class TestDashboardScrollbarContentUpdates(unittest.TestCase):
    def test_mutation_observer_resyncs_scrollbars_for_changed_enhanced_tables(self):
        enhancements = DASHBOARD_ENHANCEMENTS_JS.read_text(encoding="utf-8")
        self.assertIn("function syncHorizontalScrollbars(tables) {", enhancements)
        self.assertIn("const tablesToSync = new Set();", enhancements)
        self.assertIn("observer.observe(document.body, { childList: true, characterData: true, subtree: true });", enhancements)
        self.assertIn("const changedTable = target && target.closest(SORTABLE_TABLE_SELECTOR);", enhancements)
        self.assertIn('changedTable.dataset.dashboardEnhanced === "1"', enhancements)
        self.assertIn("tablesToSync.add(changedTable);", enhancements)
        self.assertIn("if (tablesToSync.size) scheduleHorizontalScrollbarSync(tablesToSync);", enhancements)
        self.assertIn('table.parentElement.querySelector(":scope > .dashboard-horizontal-scroll")', enhancements)
        self.assertIn("const pendingScrollbarTables = new Set();", enhancements)
        self.assertIn("if (scrollbarSyncFrame !== null) return;", enhancements)
        self.assertIn("window.requestAnimationFrame(() => {", enhancements)
        self.assertIn("pendingScrollbarTables.clear();", enhancements)

class TestMobileHeaderIconButtonAccessibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")

    def test_mobile_header_actions_keep_accessible_names_when_labels_are_hidden(self):
        new_backfill = re.search(r'<button([^>]*)onclick="App\.openBackfillModal\(\)"([^>]*)>(.*?)</button>', self.index_html, re.DOTALL)
        self.assertIsNotNone(new_backfill)
        self.assertRegex(new_backfill.group(1) + new_backfill.group(2), r'aria-label="New Backfill"')

        sign_out = re.search(r'<button([^>]*)onclick="App\.handleLogout\(\)"([^>]*)>', self.index_html)
        self.assertIsNotNone(sign_out)
        self.assertRegex(sign_out.group(1) + sign_out.group(2), r'aria-label="Sign Out"')

        mobile_icon_only = self.dashboard_css[self.dashboard_css.index("@media (max-width: 420px)"):]
        self.assertIn("header > div:last-child > button span", mobile_icon_only)
        self.assertIn("display: none;", mobile_icon_only)


    def test_mobile_dashboard_header_actions_meet_minimum_touch_target_width(self):
        mobile_start = self.dashboard_css.index("@media (max-width: 767px)")
        mobile_end = self.dashboard_css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = self.dashboard_css[mobile_start:mobile_end]
        self.assertIn("body > div.flex-1.flex.flex-col.min-w-0 > header > div:last-child > button {", mobile_rules)
        self.assertIn("min-width: 2.75rem;", mobile_rules)
        self.assertIn("min-height: 2.75rem;", mobile_rules)
        self.assertIn("justify-content: center;", mobile_rules)


class TestMobileDynamicViewportHeight(unittest.TestCase):
    def test_mobile_dashboard_shell_uses_dynamic_viewport_height_with_legacy_fallback(self):
        dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")
        mobile_start = dashboard_css.index("@media (max-width: 767px)")
        mobile_end = dashboard_css.index("@media (max-width: 420px)", mobile_start)
        mobile_rules = dashboard_css[mobile_start:mobile_end]
        shell = mobile_rules.split("body > div.flex-1.flex.flex-col.min-w-0 {", 1)[1].split("overflow: visible;", 1)[0]
        self.assertIn("min-height: calc(100vh - 7rem);", shell)
        self.assertIn("min-height: calc(100dvh - 7rem);", shell)


class TestResponsiveMobileTableScrollHeight(unittest.TestCase):
    def test_mobile_table_scroll_region_respects_visible_viewport_height(self):
        dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")
        mobile_start = dashboard_css.index("@media (max-width: 768px)")
        mobile_rules = dashboard_css[mobile_start:mobile_start + 180]
        self.assertIn("max-height: min(440px, 55vh);", mobile_rules)
        self.assertIn("max-height: min(440px, 55dvh);", mobile_rules)


class TestHorizontalScrollbarKeyboardFocus(unittest.TestCase):
    def test_horizontal_scroll_region_keeps_focus_ring_inside_overflow_clip(self):
        dashboard_css = DASHBOARD_CSS.read_text(encoding="utf-8")
        focus_start = dashboard_css.index(".dashboard-horizontal-scroll:focus-visible {")
        focus_end = dashboard_css.index("\n}", focus_start)
        focus_rules = dashboard_css[focus_start:focus_end]
        self.assertIn("outline: 2px solid var(--dashboard-focus, #38bdf8);", focus_rules)
        self.assertIn("outline-offset: -2px;", focus_rules)


if __name__ == "__main__":
    unittest.main()


class TestWalletNetworkTouchTargets(unittest.TestCase):
    def test_wallet_network_options_have_mobile_touch_target_styles(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        css = DASHBOARD_CSS.read_text(encoding="utf-8")
        for network in ("eth", "solana", "base", "bsc", "arbitrum", "polygon_pos"):
            with self.subTest(network=network):
                self.assertRegex(html, r'<label class="wt-network-option"><input type="checkbox" class="wt-setting-network" value="' + re.escape(network) + r'">')
        self.assertIn(".wt-network-option {", css)
        self.assertIn("min-height: 2.75rem;", css)
        self.assertIn('.wt-network-option input[type="checkbox"]', css)
        self.assertIn("dashboard_enhancements.css?v=20261020-31", html)


class TestWorkflowDialogAccessibleNames(unittest.TestCase):
    def test_workflow_dialogs_expose_one_named_dialog_without_nested_dialogs(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        dialogs = {
            "backfill-modal": "backfill-modal-title",
            "deploy-modal": "deploy-modal-title",
            "modal-run-ml": "modal-run-ml-title",
            "modal-deploy-ml": "modal-deploy-ml-title",
            "modal-inspect-ml": "inspect-ml-title",
        }
        for dialog_id, title_id in dialogs.items():
            with self.subTest(dialog=dialog_id):
                match = re.search(r'<div id="' + re.escape(dialog_id) + r'"[^>]*>', html)
                self.assertIsNotNone(match, f"Missing workflow dialog wrapper: {dialog_id}")
                self.assertNotIn('role="dialog"', match.group(0))
                inner = re.search(
                    r'<div role="dialog" aria-modal="true" aria-labelledby="' + re.escape(title_id) + r'"[^>]*>',
                    html[match.end():],
                )
                self.assertIsNotNone(inner, f"Missing named dialog panel: {dialog_id}")
                title = re.search(r'<h3 id="' + re.escape(title_id) + r'"[^>]*>', html[match.end():match.end() + 1800])
                self.assertIsNotNone(title, f"Missing visible title for dialog: {dialog_id}")


class TestDashboardSearchAndBacktestInputLabels(unittest.TestCase):
    def test_market_symbol_search_has_an_explicit_accessible_name(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        match = re.search(r'<input\b[^>]*id="search-crypto-input"[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertIn('aria-label="Search cryptocurrency symbols"', match.group(0))

    def test_backtest_sizing_labels_are_programmatically_associated(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'<label\s+for="bt-capital-input">Capital:</label>')
        self.assertRegex(html, r'<label\s+for="bt-risk-input">Risk %:</label>')
        for input_id in ("bt-capital-input", "bt-risk-input"):
            with self.subTest(input_id=input_id):
                self.assertRegex(html, r'<input\b[^>]*id="' + re.escape(input_id) + r'"')


class TestCustomStrategyInputLabelAssociations(unittest.TestCase):
    def test_custom_strategy_fields_have_associated_visible_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        fields = {
            "cust-strat-disp": "Display Title",
            "cust-strat-name": "Strategy ID (Unique machine identifier)",
            "cust-strat-desc": "Summary Description",
            "cust-sl-atr": "Stop-Loss (x ATR)",
            "cust-rr-ratio": "Risk:Reward Ratio",
            "cust-trail-act": "Trail Activation (R)",
            "cust-trail-dist": "Trail Distance (x ATR)",
        }
        for input_id, label in fields.items():
            with self.subTest(input_id=input_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(input_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )
                self.assertRegex(html, r'<input\b[^>]*id="' + re.escape(input_id) + r'"')


class TestStrategySearchAndSymbolInputLabels(unittest.TestCase):
    def test_strategy_catalog_search_has_an_explicit_accessible_name(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        match = re.search(r'<input\b[^>]*id="strat-catalog-search"[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertIn('aria-label="Search strategy catalog"', match.group(0))

    def test_symbol_fields_in_backfill_and_ml_workflows_have_associated_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        expected = {
            "modal-backfill-symbol": "Cryptocurrency Symbol",
            "ml-run-symbol": "Symbol",
        }
        for input_id, label in expected.items():
            with self.subTest(input_id=input_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(input_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )
                self.assertRegex(html, r'<input\b[^>]*id="' + re.escape(input_id) + r'"')


class TestRemainingDashboardInputLabelAssociations(unittest.TestCase):
    def test_wallet_tracker_api_key_and_wallet_inputs_have_accessible_names(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'<label\s+for="wt-setting-birdeye-key"[^>]*>[\s\S]*?Birdeye API Key \(Trade Fallback\)')
        self.assertIn('id="wt-add-wallet" aria-label="Wallet address"', html)
        self.assertIn('id="wt-add-label" aria-label="Wallet label (optional)"', html)

    def test_user_account_and_password_inputs_have_associated_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        fields = {
            "new-user-username": "Username",
            "new-user-email": "Email",
            "new-user-password": "Password (min 6)",
            "chg-old-pwd": "Current Password",
            "chg-new-pwd": "New Password",
            "chg-conf-pwd": "Confirm New Password",
        }
        for input_id, label in fields.items():
            with self.subTest(input_id=input_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(input_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )

    def test_deployment_and_ml_numeric_inputs_have_associated_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        fields = {
            "deploy-modal-capital": "Allocated Virtual Capital ($)",
            "deploy-modal-risk": "Risk per Trade (%)",
            "ml-run-min-trades": "Min Validation Trades",
            "deploy-ml-capital": "Allocated Virtual Capital ($)",
            "deploy-ml-risk": "Risk per Trade (%)",
        }
        for input_id, label in fields.items():
            with self.subTest(input_id=input_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(input_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )


class TestDashboardSelectAndPromptAccessibleNames(unittest.TestCase):
    def test_search_and_filter_controls_have_accessible_names(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="search-market-select" aria-label="Market to backfill"', html)
        self.assertIn('id="strat-catalog-filter" aria-label="Filter strategy catalog"', html)
        self.assertIn('id="wt-add-chain" aria-label="Blockchain network"', html)
        self.assertIn('id="strat-ai-prompt" aria-label="Strategy builder prompt"', html)

    def test_backtest_selects_have_associated_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        fields = {
            "bt-symbol-select": "Symbol",
            "bt-market-select": "Market",
            "bt-timeframe-select": "Timeframe",
            "bt-strategy-select": "Strategy",
            "bt-segment-select": "Data Segment",
        }
        for control_id, label in fields.items():
            with self.subTest(control_id=control_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(control_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )

    def test_user_role_backfill_and_ml_selects_have_associated_labels(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        fields = {
            "new-user-role": "Role",
            "modal-backfill-market": "Market",
            "modal-backfill-timeframe": "Timeframe",
            "modal-backfill-years": "Historical Duration",
            "ml-run-market": "Market",
            "ml-run-timeframe": "Timeframe",
            "ml-run-strategy": "Base Strategy",
            "ml-run-model": "Candidate Model Grid",
            "ml-run-target": "Target Definition",
        }
        for control_id, label in fields.items():
            with self.subTest(control_id=control_id):
                self.assertRegex(
                    html,
                    r'<label\s+for="' + re.escape(control_id) + r'"[^>]*>' + re.escape(label) + r'</label>',
                )
