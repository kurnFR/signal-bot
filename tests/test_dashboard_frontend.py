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

    def test_auto_refresh_prevents_overlapping_poll_cycles(self):
        self.assertIn("autoRefreshInProgress: false", self.app_js)
        start = self.app_js.index("// Keep slow API responses from spawning overlapping polling cycles.")
        end = self.app_js.index("}, 15000);", start)
        polling = self.app_js[start:end]
        self.assertIn("if (!this.authToken || this.autoRefreshInProgress) return;", polling)
        self.assertIn("this.autoRefreshInProgress = true;", polling)
        self.assertIn("finally {", polling)
        self.assertIn("this.autoRefreshInProgress = false;", polling)
        self.assertIn("Promise.allSettled(refreshTasks)", polling)
        self.assertIn('result.status === "rejected"', polling)
        self.assertIn("Dashboard panel auto-refresh failed", polling)
        for method in ("fetchSystemStatus", "fetchPaperData", "fetchScreenerData", "fetchWalletData"):
            self.assertIn(f"this.{method}(", polling)
        self.assertIn("this.autoRefreshInProgress = false;", polling)

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


    def test_toast_notifications_expose_accessible_live_regions(self):
        toast_start = self.app_js.index("    showToast(message, type = \"info\")")
        toast_end = self.app_js.index("\n    // ----------------------------------------------------", toast_start)
        toast = self.app_js[toast_start:toast_end]
        self.assertIn('container.setAttribute("aria-live", "polite")', toast)
        self.assertIn('"width:calc(100vw - 2rem)"', toast)
        self.assertIn('"max-width:22rem"', toast)
        self.assertIn('"box-sizing:border-box"', toast)
        self.assertIn('"max-height:calc(100vh - 2rem)"', toast)
        self.assertIn('"max-height:calc(100dvh - 2rem)"', toast)
        self.assertIn('"overflow-y:auto"', toast)
        self.assertIn('container.setAttribute("aria-relevant", "additions text")', toast)
        self.assertIn('toast.setAttribute("role", type === "error" ? "alert" : "status")', toast)
        self.assertIn('toast.setAttribute("aria-atomic", "true")', toast)
        self.assertIn('aria-label="Dismiss"', toast)
        self.assertIn("min-width:24px;min-height:24px;", toast)
        self.assertIn("display:flex;align-items:center;justify-content:center;", toast)
        self.assertIn('type="button"', toast)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)").matches', toast)
        self.assertIn('if (reduceMotion) {\n                toast.remove();\n                return;\n            }', toast)


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
        self.assertIn("dashboard_enhancements.css?v=20261019-5", self.index_html)



    def test_mobile_filters_and_data_tables_remain_usable(self):
        self.assertIn(".view-section .cfg-input", self.dashboard_css)
        self.assertIn("max-width: 100%;", self.dashboard_css)
        self.assertIn(".view-section .overflow-x-auto", self.dashboard_css)
        self.assertIn("overscroll-behavior-x: contain;", self.dashboard_css)
        self.assertIn("min-height: 2.5rem;", self.dashboard_css)
        self.assertIn("flex: 1 1 100%;", self.dashboard_css)
        self.assertIn("dashboard_enhancements.css?v=20261019-5", self.index_html)



    def test_reduced_motion_preference_disables_dashboard_css_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.dashboard_css)
        self.assertIn("animation-duration: 0.01ms !important;", self.dashboard_css)
        self.assertIn("transition-duration: 0.01ms !important;", self.dashboard_css)
        self.assertIn("scroll-behavior: auto !important;", self.dashboard_css)

    def test_keyboard_focus_indicators_cover_interactive_controls(self):
        self.assertIn(":focus-visible", self.dashboard_css)
        self.assertIn("outline: 2px solid #38bdf8;", self.dashboard_css)
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
        self.assertEqual(self.index_html.count('role="dialog" aria-modal="true"'), 6)

    def test_icon_only_dashboard_buttons_have_accessible_names(self):
        self.assertIn('aria-label="Search cryptocurrency"', self.index_html)
        self.assertIn('aria-label="Close backfill dialog"', self.index_html)
        self.assertIn('aria-label="Close deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML run dialog"', self.index_html)
        self.assertIn('aria-label="Close ML deployment dialog"', self.index_html)
        self.assertIn('aria-label="Close ML inspection dialog"', self.index_html)


if __name__ == "__main__":
    unittest.main()
