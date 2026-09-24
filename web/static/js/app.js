// Quantitative Trading Platform: App State, Auth, Paper Trading, Backtest & Data Logic
const API_BASE = "";

const App = {
    currentTab: "home",
    authToken: localStorage.getItem("token") || null,
    currentUser: null,
    systemStatus: null,
    coverageData: [],
    coverageSummary: {},
    strategiesList: [],
    selectedStrategy: null,
    activeJobId: null,
    jobPollTimer: null,
    equityChartInstance: null,
    tvChart: null,
    candleSeries: null,
    usersList: [],
    paperPositions: [],
    paperTrades: [],
    paperConfigs: [],
    paperMetrics: {},

    async init() {
        this.bindEvents();
        this.startClock();
        
        // 1. Verify Authentication
        await this.checkAuth();

        // 2. Fetch Initial System & Platform Data
        await this.fetchSystemStatus();
        await this.fetchCoverage();
        await this.fetchStrategies();
        
        // 3. Switch to default view
        this.switchTab("home");
        
        // Auto refresh telemetry & paper positions every 15s
        setInterval(() => {
            if (this.authToken) {
                this.fetchSystemStatus();
                if (this.currentTab === "paper") this.fetchPaperData();
            }
        }, 15000);
    },

    bindEvents() {
        document.querySelectorAll(".nav-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const tab = btn.dataset.tab;
                this.switchTab(tab);
            });
        });
    },

    getAuthHeaders() {
        const headers = { "Content-Type": "application/json" };
        if (this.authToken) {
            headers["Authorization"] = `Bearer ${this.authToken}`;
        }
        return headers;
    },

    // ----------------------------------------------------
    // TOAST NOTIFICATIONS
    // ----------------------------------------------------
    showToast(message, type = "info") {
        let container = document.getElementById("toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            container.style.cssText = [
                "position:fixed", "top:1rem", "right:1rem", "z-index:9999",
                "display:flex", "flex-direction:column", "gap:0.5rem",
                "max-width:22rem", "pointer-events:none",
            ].join(";");
            document.body.appendChild(container);
        }

        const palette = {
            success: { border: "#10b981", icon: "check-circle", iconColor: "#34d399" },
            error: { border: "#ef4444", icon: "alert-circle", iconColor: "#f87171" },
            warning: { border: "#f59e0b", icon: "alert-triangle", iconColor: "#fbbf24" },
            info: { border: "#3b82f6", icon: "info", iconColor: "#60a5fa" },
        };
        const style = palette[type] || palette.info;

        const toast = document.createElement("div");
        toast.style.cssText = [
            "pointer-events:auto", "background:#111827", `border-left:3px solid ${style.border}`,
            "border-top:1px solid #1f293d", "border-right:1px solid #1f293d", "border-bottom:1px solid #1f293d",
            "border-radius:0.5rem", "padding:0.75rem 1rem", "display:flex", "align-items:flex-start", "gap:0.6rem",
            "box-shadow:0 10px 25px -5px rgba(0,0,0,0.4)", "font-size:0.8rem", "color:#e2e8f0",
            "opacity:0", "transform:translateX(1rem)", "transition:opacity 0.2s ease, transform 0.2s ease",
        ].join(";");
        toast.innerHTML = `
            <i data-lucide="${style.icon}" style="width:16px;height:16px;flex-shrink:0;margin-top:1px;color:${style.iconColor}"></i>
            <span style="flex:1;line-height:1.35;word-break:break-word;">${message}</span>
            <button style="background:none;border:none;color:#64748b;cursor:pointer;line-height:1;font-size:0.9rem;padding:0 0 0 0.25rem;" aria-label="Dismiss">&times;</button>
        `;
        container.appendChild(toast);
        if (window.lucide) window.lucide.createIcons();

        requestAnimationFrame(() => {
            toast.style.opacity = "1";
            toast.style.transform = "translateX(0)";
        });

        const remove = () => {
            toast.style.opacity = "0";
            toast.style.transform = "translateX(1rem)";
            setTimeout(() => toast.remove(), 200);
        };
        toast.querySelector("button").addEventListener("click", remove);
        setTimeout(remove, 4500);
    },

    // ----------------------------------------------------
    // NEWS AI OVERLAY SIGNALS
    // ----------------------------------------------------
    _escapeHtml(str) {
        if (str === null || str === undefined) return "";
        const div = document.createElement("div");
        div.textContent = String(str);
        return div.innerHTML;
    },

    async fetchNewsData() {
        const grid = document.getElementById("news-heartbeats-grid");
        const tbody = document.getElementById("news-signals-table-body");
        const feed = document.getElementById("news-events-feed");
        try {
            const [statsRes, signalsRes, eventsRes] = await Promise.all([
                fetch(`${API_BASE}/api/news/stats`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/news/signals?limit=50`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/news/events?limit=30`, { headers: this.getAuthHeaders() }),
            ]);
            if (!statsRes.ok || !signalsRes.ok || !eventsRes.ok) {
                const failed = [statsRes, signalsRes, eventsRes].find(r => !r.ok);
                let detail = `HTTP ${failed.status}`;
                try { detail = (await failed.json()).detail || detail; } catch (_) {}
                throw new Error(detail);
            }
            const stats = await statsRes.json();
            const { signals } = await signalsRes.json();
            const { events } = await eventsRes.json();

            this._renderNewsKpis(stats);
            this._renderNewsHeartbeats(stats.heartbeats || []);
            this._renderNewsSignalsTable(signals);
            this._renderNewsFeed(events);
        } catch (e) {
            this.showToast(`Failed to load News AI data: ${e.message || e}`, "error");
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-rose-400 text-xs">Failed to load -- ${this._escapeHtml(e.message || e)}</td></tr>`;
            if (feed) feed.innerHTML = "";
            if (grid) grid.innerHTML = "";
        } finally {
            if (window.lucide) window.lucide.createIcons();
        }
    },

    _renderNewsKpis(stats) {
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
        set("news-kpi-events", stats.total_events_collected ?? 0);
        set("news-kpi-processed", stats.events_evaluated_by_ai ?? 0);
        set("news-kpi-signals", stats.high_confidence_signals ?? 0);
        set("news-kpi-avgconf", stats.avg_confidence != null ? `${stats.avg_confidence}%` : "--");
        set("news-kpi-trades", stats.trades_opened ?? 0);
        set("news-kpi-pending", stats.pending_signals ?? 0);

        const perf = stats.performance || {};
        set("news-perf-closed", perf.closed_trades ?? 0);
        set("news-perf-winrate", perf.win_rate != null ? `${perf.win_rate}%` : "--");
        set("news-perf-expectancy", perf.expectancy_r != null ? `${Number(perf.expectancy_r).toFixed(3)}R` : "--");
        set("news-perf-pf", perf.profit_factor != null ? Number(perf.profit_factor).toFixed(3) : "--");
        set("news-perf-r", `${Number(perf.total_r || 0).toFixed(2)}R`);
        set("news-perf-dd", `${Number(perf.max_drawdown_r || 0).toFixed(2)}R`);
    },

    _renderNewsHeartbeats(heartbeats) {
        const grid = document.getElementById("news-heartbeats-grid");
        if (!grid) return;
        const expected = [
            { name: "news_poller", label: "News Collector (CryptoPanic)" },
            { name: "econ_calendar_poller", label: "Economic Calendar (Finnhub)" },
            { name: "news_strategy_engine", label: "AI Reasoning (Claude)" },
            { name: "news_execution", label: "Execution & Telegram" },
        ];
        const byName = Object.fromEntries(heartbeats.map(h => [h.collector_name, h]));
        const STALE_MS = 20 * 60 * 1000; // no beat in 20 min -> treat as not running

        grid.innerHTML = expected.map(exp => {
            const hb = byName[exp.name];
            let dotColor = "bg-slate-600", label = "Not running", sub = "No heartbeat recorded yet";
            if (hb) {
                const age = Date.now() - new Date(hb.last_beat_time + "Z").getTime();
                if (hb.status === "error") {
                    dotColor = "bg-rose-400 animate-pulse"; label = "Error";
                    sub = this._escapeHtml(hb.detail || "See process logs");
                } else if (age > STALE_MS) {
                    dotColor = "bg-slate-600"; label = "Not running";
                    sub = `Last seen ${this._timeAgo(hb.last_beat_time)}`;
                } else {
                    dotColor = "bg-emerald-400"; label = "Running";
                    sub = this._escapeHtml(hb.detail || `Last beat ${this._timeAgo(hb.last_beat_time)}`);
                }
            }
            return `
                <div class="p-3 rounded-lg bg-slate-950/60 border border-slate-800">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full ${dotColor} flex-shrink-0"></span>
                        <span class="text-xs font-semibold text-slate-200">${exp.label}</span>
                    </div>
                    <div class="text-[11px] text-slate-500 mt-1 pl-4">${label} &middot; ${sub}</div>
                </div>`;
        }).join("");
    },

    _timeAgo(isoString) {
        const diffMs = Date.now() - new Date(isoString + "Z").getTime();
        const mins = Math.floor(diffMs / 60000);
        if (mins < 1) return "just now";
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return `${Math.floor(hrs / 24)}d ago`;
    },

    _renderNewsSignalsTable(signals) {
        const tbody = document.getElementById("news-signals-table-body");
        if (!tbody) return;
        if (!signals || signals.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-slate-500 text-xs">No signals yet -- the AI hasn't seen anything worth a high-confidence call. Check Pipeline Health above to confirm the collectors are running.</td></tr>`;
            return;
        }
        tbody.innerHTML = signals.map(s => {
            const biasColor = s.bias === "long" ? "text-emerald-400 bg-emerald-950 border-emerald-800"
                : s.bias === "short" ? "text-rose-400 bg-rose-950 border-rose-800"
                : "text-slate-400 bg-slate-800 border-slate-700";
            let outcome;
            if (s.paper_position_id) {
                outcome = `<span class="px-2 py-0.5 text-[10px] font-bold rounded bg-emerald-950 text-emerald-300 border border-emerald-800">OPENED #${s.paper_position_id}</span>`;
            } else if (s.acted_on) {
                outcome = `<span class="px-2 py-0.5 text-[10px] font-bold rounded bg-slate-800 text-slate-400 border border-slate-700" title="${this._escapeHtml(s.skip_reason || '')}">SKIPPED</span>
                    <div class="text-[10px] text-slate-500 mt-0.5">${this._escapeHtml(s.skip_reason || '')}</div>`;
            } else {
                outcome = `<span class="px-2 py-0.5 text-[10px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">PENDING</span>`;
            }
            const confColor = s.confidence >= 85 ? "text-emerald-400" : s.confidence >= 75 ? "text-amber-400" : "text-slate-300";
            return `
                <tr>
                    <td class="whitespace-nowrap text-slate-400">${this._formatDateTime(s.created_at)}</td>
                    <td class="font-semibold text-slate-100">${this._escapeHtml(s.symbol)}<div class="text-[10px] text-slate-500">${this._escapeHtml(s.market)}/${this._escapeHtml(s.timeframe)}</div></td>
                    <td><span class="px-2 py-0.5 text-[10px] font-bold rounded border ${biasColor}">${this._escapeHtml((s.bias || "").toUpperCase())}</span></td>
                    <td class="font-mono font-bold ${confColor}">${s.confidence}%</td>
                    <td class="max-w-[220px] text-slate-300">${s.url ? `<a href="${this._escapeHtml(s.url)}" target="_blank" rel="noopener" class="hover:text-sky-400 underline decoration-dotted">${this._escapeHtml(s.headline || "(no linked event)")}</a>` : this._escapeHtml(s.headline || "(no linked event)")}</td>
                    <td class="max-w-[260px] text-slate-400">${this._escapeHtml(s.reasoning || "--")}</td>
                    <td class="text-slate-400">${this._escapeHtml(s.time_horizon || "--")}</td>
                    <td>${outcome}</td>
                </tr>`;
        }).join("");
    },

    _renderNewsFeed(events) {
        const feed = document.getElementById("news-events-feed");
        if (!feed) return;
        if (!events || events.length === 0) {
            feed.innerHTML = `<div class="text-center py-8 text-slate-500 text-xs">No news collected yet.</div>`;
            return;
        }
        feed.innerHTML = events.map(e => {
            const isCalendar = e.category === "macro_calendar";
            const icon = isCalendar ? "calendar-clock" : "newspaper";
            const badgeColor = isCalendar ? "text-purple-300 bg-purple-950 border-purple-800" : "text-sky-300 bg-sky-950 border-sky-800";
            const badgeText = isCalendar ? (e.impact || "macro").toUpperCase() : (e.source || "news").toUpperCase();
            const when = e.published_at || e.scheduled_at || e.fetched_at;
            let metaLine = "";
            if (isCalendar) {
                metaLine = `Forecast: ${this._escapeHtml(e.forecast_value ?? "--")} &middot; Actual: ${this._escapeHtml(e.actual_value ?? "pending")} &middot; Previous: ${this._escapeHtml(e.previous_value ?? "--")}`;
            } else if (e.symbols) {
                metaLine = `Tagged: ${this._escapeHtml(e.symbols)}`;
            }
            const headline = e.url
                ? `<a href="${this._escapeHtml(e.url)}" target="_blank" rel="noopener" class="hover:text-sky-400">${this._escapeHtml(e.headline)}</a>`
                : this._escapeHtml(e.headline);
            return `
                <div class="flex items-start gap-3 p-2.5 rounded-lg hover:bg-slate-800/40 transition-all">
                    <i data-lucide="${icon}" class="w-3.5 h-3.5 text-slate-500 flex-shrink-0 mt-0.5"></i>
                    <div class="min-w-0 flex-1">
                        <div class="flex items-center gap-2">
                            <span class="px-1.5 py-0.2 text-[9px] font-bold rounded border ${badgeColor}">${badgeText}</span>
                            <span class="text-[11px] text-slate-500">${this._formatDateTime(when)}</span>
                            ${!e.processed_at ? `<span class="text-[9px] text-amber-500">not yet evaluated</span>` : ""}
                        </div>
                        <div class="text-xs text-slate-200 mt-0.5">${headline}</div>
                        ${metaLine ? `<div class="text-[10px] text-slate-500 mt-0.5">${metaLine}</div>` : ""}
                    </div>
                </div>`;
        }).join("");
    },

    _formatDateTime(isoString) {
        if (!isoString) return "--";
        try {
            const d = new Date(isoString.includes("Z") || isoString.includes("+") ? isoString : isoString + "Z");
            return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
        } catch (_) {
            return isoString;
        }
    },

    // ----------------------------------------------------
    // RESEARCH BOTS: retailbot2 + smart-money screener
    // ----------------------------------------------------
    async fetchResearchData() {
        try {
            const [posRes, statsRes, screenerSigRes, screenerStatsRes] = await Promise.all([
                fetch(`${API_BASE}/api/research/retailbot2/positions?limit=100`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/research/retailbot2/stats`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/research/screener/signals?limit=50`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/research/screener/stats`, { headers: this.getAuthHeaders() }),
            ]);
            const results = [posRes, statsRes, screenerSigRes, screenerStatsRes];
            const failed = results.find(r => !r.ok);
            if (failed) {
                let detail = `HTTP ${failed.status}`;
                try { detail = (await failed.json()).detail || detail; } catch (_) {}
                throw new Error(detail);
            }
            const { positions } = await posRes.json();
            const rb2Stats = await statsRes.json();
            const { signals } = await screenerSigRes.json();
            const screenerStats = await screenerStatsRes.json();

            this._renderRb2Kpis(rb2Stats);
            this._renderRb2Positions(positions);
            this._renderRb2StrategyTable(rb2Stats.by_strategy || []);
            this._renderScreenerKpis(screenerStats);
            this._renderScreenerSignals(signals);
        } catch (e) {
            this.showToast(`Failed to load Research Bots data: ${e.message || e}`, "error");
        } finally {
            if (window.lucide) window.lucide.createIcons();
        }
        // Separate try/catch: a bot that's never been started yet (control
        // table doesn't exist) shouldn't take down the rest of the panel
        // above, which can still show positions/stats/signals fine.
        this._fetchBotControl("retailbot2");
        this._fetchBotControl("screener");
    },

    async _fetchBotControl(botName) {
        try {
            const res = await fetch(`${API_BASE}/api/research/${botName}/control`, { headers: this.getAuthHeaders() });
            if (!res.ok) {
                let detail = `HTTP ${res.status}`;
                try { detail = (await res.json()).detail || detail; } catch (_) {}
                throw new Error(detail);
            }
            const control = await res.json();
            const prefix = botName === "retailbot2" ? "rb2-ctl-" : "screener-ctl-";
            const enabledEl = document.getElementById(`${prefix}enabled`);
            if (enabledEl) enabledEl.checked = !!control.enabled;
            for (const [key, value] of Object.entries(control.overrides || {})) {
                const el = document.getElementById(`${prefix}${key}`);
                if (!el) continue;
                if (el.type === "checkbox") el.checked = !!value;
                else el.value = value;
            }
            const updatedEl = document.getElementById(`${prefix}updated`);
            if (updatedEl && control.updated_at) {
                updatedEl.innerText = `Last changed: ${this._formatDateTime(control.updated_at)}`;
            }
        } catch (e) {
            // Quiet by design -- this fires automatically on every panel
            // load, and "bot never started yet" is an expected state, not
            // an error worth interrupting the user with a toast for. The
            // control card's inputs just stay at their placeholder defaults.
            console.warn(`${botName} control not available yet:`, e.message || e);
        }
    },

    async saveBotControl(botName) {
        const prefix = botName === "retailbot2" ? "rb2-ctl-" : "screener-ctl-";
        const fields = botName === "retailbot2"
            ? ["atr_sl_multiplier", "rr_ratio", "risk_per_trade", "max_open_trades_per_mode",
               "max_trades_per_symbol", "min_trade_interval_hours", "rsi_oversold", "rsi_overbought",
               "rsi_zone_width", "sr_touch_threshold", "sr_min_touches", "bb_std", "bb_proximity_pct",
               "pattern_tolerance", "pattern_min_bars_apart", "spike_atr_multiplier"]
            : ["rvol_multiplier", "divergence_max_price_change", "velocity_threshold",
               "max_alerts_per_hour", "max_alerts_per_symbol_per_hour", "alert_cooldown_sec",
               "enable_divergence_detection", "enable_velocity_detection"];

        const enabledEl = document.getElementById(`${prefix}enabled`);
        const overrides = {};
        for (const key of fields) {
            const el = document.getElementById(`${prefix}${key}`);
            if (!el) continue;
            if (el.type === "checkbox") {
                overrides[key] = el.checked;
            } else if (el.value !== "") {
                const num = Number(el.value);
                if (!Number.isNaN(num)) overrides[key] = num;
            }
            // A blank field is intentionally left out of the payload rather
            // than sent as 0/null -- the bot only applies keys present in
            // the overrides dict, so blank = "don't change this one" here,
            // not "reset to zero".
        }

        try {
            const res = await fetch(`${API_BASE}/api/research/${botName}/control`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({ enabled: enabledEl ? enabledEl.checked : true, overrides }),
            });
            if (!res.ok) {
                let detail = `HTTP ${res.status}`;
                try { detail = (await res.json()).detail || detail; } catch (_) {}
                throw new Error(detail);
            }
            const data = await res.json();
            this.showToast(`${botName === "retailbot2" ? "Retail Bot" : "Screener"} settings saved. ${data.note || ""}`, "success");
        } catch (e) {
            this.showToast(`Failed to save ${botName} settings: ${e.message || e}`, "error");
        }
    },

    _renderRb2Kpis(stats) {
        const byMode = stats.by_mode || {};
        const shadow = byMode.shadow || {};
        const inverse = byMode.inverse || {};
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
        const pnlClass = (v) => v > 0 ? "text-emerald-400" : v < 0 ? "text-rose-400" : "text-slate-100";
        const pnlEl = (id, v) => { const el = document.getElementById(id); if (el) { el.innerText = `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`; el.className = el.className.replace(/text-(emerald|rose|slate)-\d+/, "") + " " + pnlClass(v); } };

        pnlEl("rb2-kpi-shadow-pnl", shadow.realized_pnl || 0);
        pnlEl("rb2-kpi-inverse-pnl", inverse.realized_pnl || 0);
        set("rb2-kpi-shadow-open", shadow.open_count ?? 0);
        set("rb2-kpi-inverse-open", inverse.open_count ?? 0);
        set("rb2-kpi-shadow-wr", shadow.win_rate != null ? `${shadow.win_rate}%` : "--");
        set("rb2-kpi-inverse-wr", inverse.win_rate != null ? `${inverse.win_rate}%` : "--");
        set("rb2-kpi-shadow-closed", shadow.closed_count ?? 0);
        set("rb2-kpi-inverse-closed", inverse.closed_count ?? 0);
    },

    _renderRb2Positions(positions) {
        const tbody = document.getElementById("rb2-positions-table-body");
        if (!tbody) return;
        if (!positions || positions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-slate-500 text-xs">No open positions. Confirm paper/retailbot2.py is running.</td></tr>`;
            return;
        }
        tbody.innerHTML = positions.map(p => {
            const sideColor = p.side === "LONG" ? "text-emerald-400 bg-emerald-950 border-emerald-800" : "text-rose-400 bg-rose-950 border-rose-800";
            const modeColor = p.mode === "inverse" ? "text-purple-300 bg-purple-950 border-purple-800" : "text-sky-300 bg-sky-950 border-sky-800";
            return `
                <tr>
                    <td class="font-semibold text-slate-100">${this._escapeHtml(p.symbol)}</td>
                    <td><span class="px-2 py-0.5 text-[10px] font-bold rounded border ${sideColor}">${this._escapeHtml(p.side)}</span></td>
                    <td><span class="px-2 py-0.5 text-[10px] font-bold rounded border ${modeColor}">${this._escapeHtml(p.mode)}</span></td>
                    <td class="font-mono text-slate-300">$${Number(p.entry_price).toFixed(4)}</td>
                    <td class="font-mono text-rose-400">$${Number(p.stop_price).toFixed(4)}</td>
                    <td class="font-mono text-emerald-400">$${Number(p.target_price).toFixed(4)}</td>
                    <td class="text-slate-400">${this._escapeHtml(p.strategy)}</td>
                    <td class="text-slate-400">${p.confluence_score ?? 1}/7</td>
                    <td class="whitespace-nowrap text-slate-500">${this._formatDateTime(p.entry_time)}</td>
                </tr>`;
        }).join("");
    },

    _renderRb2StrategyTable(rows) {
        const tbody = document.getElementById("rb2-strategy-table-body");
        if (!tbody) return;
        if (!rows || rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-slate-500 text-xs">No closed trades yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = rows.map(r => {
            const total = r.total_trades || 0;
            const wr = total > 0 ? ((r.wins || 0) / total * 100).toFixed(1) : "--";
            const pnl = Number(r.total_pnl || 0);
            const pnlColor = pnl > 0 ? "text-emerald-400" : pnl < 0 ? "text-rose-400" : "text-slate-300";
            const modeColor = r.mode === "inverse" ? "text-purple-300 bg-purple-950 border-purple-800" : "text-sky-300 bg-sky-950 border-sky-800";
            return `
                <tr>
                    <td class="font-semibold text-slate-100">${this._escapeHtml(r.strategy)}</td>
                    <td><span class="px-2 py-0.5 text-[10px] font-bold rounded border ${modeColor}">${this._escapeHtml(r.mode)}</span></td>
                    <td class="text-slate-300">${total}</td>
                    <td class="text-emerald-400">${r.wins || 0}</td>
                    <td class="text-rose-400">${r.losses || 0}</td>
                    <td class="text-slate-300">${wr}${wr !== "--" ? "%" : ""}</td>
                    <td class="font-mono ${pnlColor}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</td>
                </tr>`;
        }).join("");
    },

    _renderScreenerKpis(stats) {
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
        set("screener-kpi-24h", stats.signals_last_24h ?? 0);
        set("screener-kpi-total", stats.total_signals ?? 0);
        const breakdownEl = document.getElementById("screener-kpi-breakdown");
        if (breakdownEl) {
            const byType = stats.by_type_last_24h || {};
            const entries = Object.entries(byType);
            breakdownEl.innerHTML = entries.length
                ? entries.map(([type, n]) => `<div>${this._escapeHtml(type)}: <span class="font-mono text-slate-100">${n}</span></div>`).join("")
                : `<span class="text-slate-500">No signals in 24h</span>`;
        }
    },

    _renderScreenerSignals(signals) {
        const tbody = document.getElementById("screener-signals-table-body");
        if (!tbody) return;
        if (!signals || signals.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-slate-500 text-xs">No signals yet. Confirm Screen/screening.py is running.</td></tr>`;
            return;
        }
        const typeColors = {
            RVOL_SPIKE: "text-amber-300 bg-amber-950 border-amber-800",
            DIVERGENCE_ACCUMULATION: "text-emerald-300 bg-emerald-950 border-emerald-800",
            DIVERGENCE_DISTRIBUTION: "text-rose-300 bg-rose-950 border-rose-800",
            VELOCITY_SURGE: "text-sky-300 bg-sky-950 border-sky-800",
        };
        tbody.innerHTML = signals.map(s => {
            const color = typeColors[s.signal_type] || "text-slate-300 bg-slate-800 border-slate-700";
            const changeColor = s.price_change_pct > 0 ? "text-emerald-400" : s.price_change_pct < 0 ? "text-rose-400" : "text-slate-400";
            return `
                <tr>
                    <td class="whitespace-nowrap text-slate-500">${this._formatDateTime(s.detected_at)}</td>
                    <td class="font-semibold text-slate-100">${this._escapeHtml(s.symbol)}</td>
                    <td><span class="px-2 py-0.5 text-[10px] font-bold rounded border ${color}">${this._escapeHtml(s.signal_type)}</span></td>
                    <td class="font-mono text-slate-300">${Number(s.rvol).toFixed(2)}x</td>
                    <td class="font-mono text-slate-300">$${Number(s.price).toFixed(4)}</td>
                    <td class="font-mono ${changeColor}">${s.price_change_pct >= 0 ? "+" : ""}${Number(s.price_change_pct).toFixed(3)}%</td>
                    <td class="font-mono text-slate-300">${Number(s.volume_velocity).toFixed(2)}x</td>
                    <td>${s.telegram_sent ? `<i data-lucide="check" class="w-3.5 h-3.5 text-emerald-400"></i>` : `<i data-lucide="x" class="w-3.5 h-3.5 text-slate-600"></i>`}</td>
                </tr>`;
        }).join("");
    },

    // ----------------------------------------------------
    // AUTHENTICATION & SESSIONS
    // ----------------------------------------------------
    async checkAuth() {
        if (!this.authToken) {
            this.showLoginModal();
            return;
        }
        try {
            const res = await fetch(`${API_BASE}/api/auth/me`, {
                headers: this.getAuthHeaders()
            });
            if (res.ok) {
                this.currentUser = await res.json();
                this.updateUserUI();
                this.hideLoginModal();
            } else {
                this.authToken = null;
                localStorage.removeItem("token");
                this.showLoginModal();
            }
        } catch (e) {
            console.error("Auth check failed:", e);
            this.showLoginModal();
        }
    },

    showLoginModal() {
        const modal = document.getElementById("login-modal");
        if (modal) {
            modal.classList.remove("hidden");
            modal.classList.add("flex");
        }
    },

    hideLoginModal() {
        const modal = document.getElementById("login-modal");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },

    async handleLogin() {
        const userEl = document.getElementById("login-username");
        const passEl = document.getElementById("login-password");
        const errEl = document.getElementById("login-error-msg");

        const username = userEl.value.trim();
        const password = passEl.value;

        if (!username || !password) {
            if (errEl) {
                errEl.innerText = "Please enter username and password.";
                errEl.classList.remove("hidden");
            }
            return;
        }

        try {
            const res = await fetch(`${API_BASE}/api/auth/login`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password })
            });

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || "Authentication failed");
            }

            this.authToken = data.token;
            this.currentUser = data.user;
            localStorage.setItem("token", data.token);

            if (errEl) errEl.classList.add("hidden");
            this.hideLoginModal();
            this.updateUserUI();
            this.showToast(`Welcome back, ${this.currentUser.username}!`, "success");

            // Refresh user & settings
            this.fetchUsers();
        } catch (err) {
            if (errEl) {
                errEl.innerText = err.message;
                errEl.classList.remove("hidden");
            }
        }
    },

    handleLogout() {
        this.authToken = null;
        this.currentUser = null;
        localStorage.removeItem("token");
        this.updateUserUI();
        this.showLoginModal();
        this.showToast("Logged out successfully.", "info");
    },

    updateUserUI() {
        const userBadge = document.getElementById("top-user-badge");
        const userRole = document.getElementById("top-user-role");
        const userContainer = document.getElementById("top-user-container");

        if (this.currentUser && userContainer) {
            userContainer.classList.remove("hidden");
            if (userBadge) userBadge.innerText = this.currentUser.username;
            if (userRole) userRole.innerText = this.currentUser.role.toUpperCase();
        } else if (userContainer) {
            userContainer.classList.add("hidden");
        }
    },

    async handleChangeMyPassword() {
        const oldP = document.getElementById("chg-old-pwd").value;
        const newP = document.getElementById("chg-new-pwd").value;
        const confP = document.getElementById("chg-conf-pwd").value;

        if (newP !== confP) {
            alert("New passwords do not match!");
            return;
        }

        try {
            const res = await fetch(`${API_BASE}/api/auth/change-password`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({ old_password: oldP, new_password: newP })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Password change failed");

            this.showToast("Password updated successfully!", "success");
            document.getElementById("chg-old-pwd").value = "";
            document.getElementById("chg-new-pwd").value = "";
            document.getElementById("chg-conf-pwd").value = "";
        } catch (e) {
            alert("Error: " + e.message);
        }
    },

    // ----------------------------------------------------
    // USER MANAGEMENT (ADMIN)
    // ----------------------------------------------------
    async fetchUsers() {
        if (!this.currentUser || this.currentUser.role !== "admin") return;
        try {
            const res = await fetch(`${API_BASE}/api/users`, {
                headers: this.getAuthHeaders()
            });
            if (res.ok) {
                const data = await res.json();
                this.usersList = data.users || [];
                this.renderUsersTable();
            }
        } catch (e) {
            console.error("Failed to fetch users:", e);
        }
    },

    renderUsersTable() {
        const tbody = document.getElementById("users-table-body");
        if (!tbody) return;

        tbody.innerHTML = this.usersList.map(u => `
            <tr>
                <td class="font-bold text-slate-100 font-mono">#${u.id}</td>
                <td class="font-mono text-sky-400 font-semibold">${u.username}</td>
                <td class="text-xs text-slate-400">${u.email || "-"}</td>
                <td>
                    <span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold ${u.role === "admin" ? "bg-purple-950 text-purple-300 border border-purple-800" : "bg-slate-800 text-slate-300"}">
                        ${u.role}
                    </span>
                </td>
                <td>
                    <span class="px-2 py-0.5 rounded text-[10px] font-semibold ${u.is_active ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-rose-950 text-rose-400 border border-rose-800"}">
                        ${u.is_active ? "Active" : "Disabled"}
                    </span>
                </td>
                <td class="text-xs text-slate-500 font-mono">${(u.created_at || "").slice(0, 10)}</td>
                <td class="text-right space-x-2">
                    <button onclick="App.openResetPasswordModal(${u.id}, '${u.username}')" class="px-2 py-1 text-xs rounded bg-sky-600/20 hover:bg-sky-600 text-sky-300 hover:text-white border border-sky-500/30">
                        Reset Pass
                    </button>
                    ${u.id !== this.currentUser.id ? `
                        <button onclick="App.toggleUserStatus(${u.id}, ${!u.is_active})" class="px-2 py-1 text-xs rounded bg-amber-600/20 hover:bg-amber-600 text-amber-300 hover:text-white border border-amber-500/30">
                            ${u.is_active ? "Disable" : "Enable"}
                        </button>
                    ` : ""}
                </td>
            </tr>
        `).join("");
    },

    async handleCreateUser() {
        const username = document.getElementById("new-user-username").value.trim();
        const email = document.getElementById("new-user-email").value.trim();
        const password = document.getElementById("new-user-password").value;
        const role = document.getElementById("new-user-role").value;

        try {
            const res = await fetch(`${API_BASE}/api/users`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({ username, email, password, role })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "User creation failed");

            this.showToast(`User '${username}' created successfully!`, "success");
            document.getElementById("new-user-username").value = "";
            document.getElementById("new-user-email").value = "";
            document.getElementById("new-user-password").value = "";
            await this.fetchUsers();
        } catch (e) {
            alert("Error: " + e.message);
        }
    },

    openResetPasswordModal(userId, username) {
        const newPass = prompt(`Enter new password for user '${username}' (min 6 characters):`);
        if (!newPass) return;
        this.submitResetPassword(userId, newPass);
    },

    async submitResetPassword(userId, newPassword) {
        try {
            const res = await fetch(`${API_BASE}/api/users/${userId}/reset-password`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({ new_password: newPassword })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Reset failed");
            this.showToast(data.message, "success");
        } catch (e) {
            alert("Error: " + e.message);
        }
    },

    async toggleUserStatus(userId, newActive) {
        try {
            const res = await fetch(`${API_BASE}/api/users/${userId}`, {
                method: "PUT",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({ is_active: newActive })
            });
            if (res.ok) {
                this.showToast("User status updated", "info");
                await this.fetchUsers();
            }
        } catch (e) {
            alert("Error: " + e);
        }
    },

    // ----------------------------------------------------
    // PHASE B: PAPER TRADING
    // ----------------------------------------------------
    async fetchPaperData() {
        try {
            const [posRes, tradesRes, cfgsRes, metricsRes] = await Promise.all([
                fetch(`${API_BASE}/api/paper/positions`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/paper/trades`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/paper/configs`, { headers: this.getAuthHeaders() }),
                fetch(`${API_BASE}/api/paper/metrics`, { headers: this.getAuthHeaders() }),
            ]);

            this.paperPositions = (await posRes.json()).positions || [];
            this.paperTrades = (await tradesRes.json()).trades || [];
            this.paperConfigs = (await cfgsRes.json()).configs || [];
            this.paperMetrics = (await metricsRes.json()).metrics || {};

            this.renderPaperView();
        } catch (e) {
            console.error("Failed to fetch paper data:", e);
        }
    },

    renderPaperView() {
        // 1. Render Metrics Scorecard
        const m = this.paperMetrics || {};
        const elOpen = document.getElementById("paper-kpi-open");
        const elTotal = document.getElementById("paper-kpi-total");
        const elWin = document.getElementById("paper-kpi-winrate");
        const elCumR = document.getElementById("paper-kpi-cumr");

        if (elOpen) elOpen.innerText = this.paperPositions.length;
        if (elTotal) elTotal.innerText = m.totalTrades || 0;
        if (elWin) elWin.innerText = m.winRatePct != null ? `${m.winRatePct}%` : "0%";
        if (elCumR) {
            const isPos = (m.cumulativeR || 0) >= 0;
            elCumR.innerText = `${isPos ? "+" : ""}${(m.cumulativeR || 0).toFixed(2)}R`;
            elCumR.className = `text-2xl font-bold font-mono ${isPos ? "text-emerald-400" : "text-rose-400"}`;
        }

        // 2. Render Active Positions Table
        const posBody = document.getElementById("paper-positions-body");
        if (posBody) {
            if (this.paperPositions.length === 0) {
                posBody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-slate-500 text-xs">No active paper positions open right now. Monitoring live market signals...</td></tr>`;
            } else {
                posBody.innerHTML = this.paperPositions.map(p => {
                    const isProfitable = (p.unrealized_r || 0) >= 0;
                    return `
                        <tr>
                            <td class="font-bold font-mono text-slate-100">${p.symbol}</td>
                            <td><span class="px-1.5 py-0.5 text-xs rounded uppercase ${p.market === "futures" ? "bg-purple-950 text-purple-400 border border-purple-800" : "bg-slate-800 text-slate-300"}">${p.market}</span> ${p.timeframe}</td>
                            <td>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${p.direction === "LONG" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-rose-950 text-rose-400 border border-rose-800"}">
                                    ${p.direction}
                                </span>
                            </td>
                            <td class="font-mono text-xs text-sky-400">${p.strategy_name}</td>
                            <td class="font-mono text-xs text-slate-200">$${p.entry_price.toLocaleString()}</td>
                            <td class="font-mono text-xs font-bold text-slate-100">$${p.current_price.toLocaleString()}</td>
                            <td class="font-mono text-xs ${isProfitable ? "text-emerald-400" : "text-rose-400"} font-semibold">
                                ${(p.unrealized_pnl_pct * 100).toFixed(2)}% (${p.unrealized_r > 0 ? "+" : ""}${p.unrealized_r.toFixed(2)}R)
                            </td>
                            <td class="text-xs text-slate-400 font-mono">${p.holding_bars} bars</td>
                            <td class="text-right">
                                <button onclick="App.manualClosePaperPosition(${p.id})" class="px-2.5 py-1 text-xs rounded bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/30 font-medium transition-colors">
                                    Close Position
                                </button>
                            </td>
                        </tr>
                    `;
                }).join("");
            }
        }

        // 3. Render Strategy Activation Manager
        const cfgContainer = document.getElementById("paper-configs-container");
        if (cfgContainer) {
            cfgContainer.innerHTML = this.paperConfigs.map(c => `
                <div class="p-4 rounded-xl bg-slate-900 border ${c.is_active ? "border-emerald-800/60" : "border-slate-800"} flex items-center justify-between">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-slate-100 font-mono">${c.symbol}</span>
                            <span class="text-xs text-slate-400 uppercase">${c.market} • ${c.timeframe}</span>
                            ${c.is_active ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">ACTIVE LIVE</span>` : `<span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-500">PAUSED</span>`}
                        </div>
                        <div class="text-xs font-mono text-sky-400 mt-1">${c.strategy_name}</div>
                        <div class="text-[10px] text-slate-500 mt-0.5">$${Number(c.allocated_capital).toLocaleString()} capital &middot; ${c.risk_per_trade_pct}% risk/trade</div>
                    </div>
                    <div>
                        <button onclick="App.togglePaperStrategy('${c.symbol}', '${c.market}', '${c.timeframe}', '${c.strategy_name}', ${!c.is_active}, ${c.allocated_capital}, ${c.risk_per_trade_pct})" 
                            class="px-3 py-1.5 rounded text-xs font-semibold ${c.is_active ? "bg-amber-600/20 hover:bg-amber-600 text-amber-300 hover:text-white border border-amber-500/30" : "bg-emerald-600/20 hover:bg-emerald-600 text-emerald-300 hover:text-white border border-emerald-500/30"}">
                            ${c.is_active ? "Pause" : "Activate"}
                        </button>
                    </div>
                </div>
            `).join("");
        }

        // 4. Render Closed Paper Trades Ledger
        const tradesBody = document.getElementById("paper-trades-body");
        if (tradesBody) {
            if (this.paperTrades.length === 0) {
                tradesBody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-500 text-xs">No closed paper trades recorded yet.</td></tr>`;
            } else {
                tradesBody.innerHTML = this.paperTrades.map(t => {
                    const isWin = t.r_multiple > 0;
                    return `
                        <tr>
                            <td class="font-bold font-mono text-slate-200">${t.symbol}</td>
                            <td class="text-xs font-mono text-sky-400">${t.strategy_name}</td>
                            <td>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${t.direction === "LONG" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-rose-950 text-rose-400 border border-rose-800"}">
                                    ${t.direction}
                                </span>
                            </td>
                            <td class="font-mono text-xs text-slate-300">${t.entry_time_wib}</td>
                            <td class="font-mono text-xs text-slate-300">${t.exit_time_wib}</td>
                            <td>
                                <span class="px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold ${t.exit_reason === "TP" ? "bg-emerald-950 text-emerald-400" : t.exit_reason === "SL" ? "bg-rose-950 text-rose-400" : "bg-amber-950 text-amber-400"}">
                                    ${t.exit_reason}
                                </span>
                            </td>
                            <td class="font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"} font-semibold">
                                ${t.net_return_pct}% (${t.r_multiple > 0 ? "+" : ""}${t.r_multiple}R)
                            </td>
                            <td class="font-mono text-xs text-slate-400">${t.holding_bars} bars</td>
                        </tr>
                    `;
                }).join("");
            }
        }
    },

    async togglePaperStrategy(symbol, market, timeframe, strategy, newActive, allocatedCapital, riskPerTradePct) {
        try {
            const res = await fetch(`${API_BASE}/api/paper/configs`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol, market, timeframe,
                    strategy_name: strategy,
                    is_active: newActive,
                    // Must send the existing capital/risk% back -- the
                    // backend's ON DUPLICATE KEY UPDATE always applies
                    // whatever is in this request, and Pydantic's request
                    // model defaults (5000.0 / 1.0) would otherwise silently
                    // overwrite a deployment's real configured values on
                    // every single pause/activate click.
                    allocated_capital: allocatedCapital,
                    risk_per_trade_pct: riskPerTradePct,
                })
            });
            if (res.ok) {
                this.showToast(`Paper trading for ${symbol} ${strategy} ${newActive ? "activated" : "paused"}`, "info");
                await this.fetchPaperData();
            }
        } catch (e) {
            alert("Error toggling paper config: " + e);
        }
    },

    async manualClosePaperPosition(posId) {
        if (!confirm("Are you sure you want to manually exit this open paper position at market price?")) return;
        try {
            const res = await fetch(`${API_BASE}/api/paper/positions/${posId}/close`, {
                method: "POST",
                headers: this.getAuthHeaders()
            });
            if (res.ok) {
                this.showToast(`Paper position #${posId} closed!`, "success");
                await this.fetchPaperData();
            }
        } catch (e) {
            alert("Error closing position: " + e);
        }
    },

    async syncPaperMarketTick() {
        const btn = document.getElementById("btn-sync-paper");
        if (btn) btn.innerHTML = `<div class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Syncing...`;
        try {
            const res = await fetch(`${API_BASE}/api/paper/sync`, {
                method: "POST",
                headers: this.getAuthHeaders()
            });
            if (!res.ok) {
                // Surface the real backend error instead of letting a
                // non-JSON error body (e.g. a plain-text 500) blow up on
                // res.json() with a confusing "Unexpected token" message.
                let detail = `HTTP ${res.status}`;
                try {
                    const errBody = await res.json();
                    detail = errBody.detail || JSON.stringify(errBody);
                } catch (_) {
                    detail = await res.text();
                }
                throw new Error(detail);
            }
            const data = await res.json();
            const r = data.results || {};
            this.showToast(`Market sync evaluated ${r.evaluated_configs} active strategies: ${r.new_positions_opened} new entries, ${r.positions_closed} exits.`, "success");
            await this.fetchPaperData();
        } catch (e) {
            this.showToast(`Sync error: ${e.message || e}`, "error");
        } finally {
            if (btn) btn.innerHTML = `<i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i> Evaluate Market Tick`;
            if (window.lucide) window.lucide.createIcons();
        }
    },

    // ----------------------------------------------------
    // MASTER DATA, BACKTEST STUDIO, GENERAL NAVIGATION
    // ----------------------------------------------------
    startClock() {
        const clockEl = document.getElementById("wib-clock");
        const update = () => {
            const now = new Date();
            const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
            const wib = new Date(utc + (3600000 * 7));
            const hours = String(wib.getHours()).padStart(2, "0");
            const mins = String(wib.getMinutes()).padStart(2, "0");
            const secs = String(wib.getSeconds()).padStart(2, "0");
            if (clockEl) clockEl.innerText = `${hours}:${mins}:${secs} WIB`;
        };
        update();
        setInterval(update, 1000);
    },

    switchTab(tabId) {
        this.currentTab = tabId;
        document.querySelectorAll(".nav-btn").forEach(b => {
            b.classList.toggle("active", b.dataset.tab === tabId);
        });
        document.querySelectorAll(".view-section").forEach(sec => {
            sec.classList.toggle("hidden", sec.id !== `view-${tabId}`);
        });

        const titleEl = document.getElementById("view-header-title");
        const descEl = document.getElementById("view-header-desc");

        if (tabId === "home") {
            if (titleEl) titleEl.innerText = "Executive Trading Overview";
            if (descEl) descEl.innerText = "System health, database metrics, and backtest leaderboard";
            this.renderHome();
        } else if (tabId === "master-data") {
            if (titleEl) titleEl.innerText = "Master Data & Backfill Manager";
            if (descEl) descEl.innerText = "Search any Binance asset, backfill historical klines, and compute features";
            this.renderMasterData();
        } else if (tabId === "backtest") {
            if (titleEl) titleEl.innerText = "Backtest Studio & Visual Simulator";
            if (descEl) descEl.innerText = "Simulate 24+ strategies against historical data with strict no-lookahead execution";
            this.renderBacktestStudio();
        } else if (tabId === "paper") {
            if (titleEl) titleEl.innerText = "Phase B: Live Paper Trading";
            if (descEl) descEl.innerText = "Simulate forward order execution against real market data without risk";
            this.fetchPaperData();
        } else if (tabId === "ml-studio") {
            if (titleEl) titleEl.innerText = "Machine Learning Forecast & Tournament Studio";
            if (descEl) descEl.innerText = "Multi-model tournament validation, probability calibration, and AI signal gating";
            this.fetchMLData();
        } else if (tabId === "strategies") {
            if (titleEl) titleEl.innerText = "Strategy Library & Rule Builder";
            if (descEl) descEl.innerText = "Explore strategy methodologies, benchmark performance, and create custom strategies";
            this.renderStrategyLibrary();
        } else if (tabId === "news-ai") {
            if (titleEl) titleEl.innerText = "News AI Overlay Signals";
            if (descEl) descEl.innerText = "What the AI has read, how confident it was, and what it did about it";
            this.fetchNewsData();
        } else if (tabId === "research") {
            if (titleEl) titleEl.innerText = "Research Bots";
            if (descEl) descEl.innerText = "Retail Death Trap Bot v2 (shadow/inverse confluence) and the Smart Money Volume Screener";
            this.fetchResearchData();
        } else if (tabId === "settings") {
            if (titleEl) titleEl.innerText = "System Settings & User Administration";
            if (descEl) descEl.innerText = "User accounts, password resets, MariaDB connection, and execution parameters";
            this.renderSettings();
            this.fetchUsers();
        }

        if (window.lucide) window.lucide.createIcons();
    },

    async fetchSystemStatus() {
        try {
            const res = await fetch(`${API_BASE}/api/settings/status`, { headers: this.getAuthHeaders() });
            this.systemStatus = await res.json();
            this.updateStatusBar();
        } catch (e) {
            console.error("Failed to fetch status:", e);
        }
    },

    updateStatusBar() {
        const dbBadge = document.getElementById("status-db-badge");
        if (dbBadge && this.systemStatus) {
            const isOk = this.systemStatus.database.status === "ok";
            dbBadge.className = `px-2 py-0.5 text-xs rounded font-medium flex items-center gap-1.5 ${isOk ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-red-950 text-red-400 border border-red-800"}`;
            dbBadge.innerHTML = `<span class="w-2 h-2 rounded-full ${isOk ? "bg-emerald-400 animate-pulse" : "bg-red-400"}"></span> MariaDB: ${isOk ? "Connected" : "Error"}`;
        }
    },

    async fetchCoverage() {
        try {
            const res = await fetch(`${API_BASE}/api/master-data/coverage`, { headers: this.getAuthHeaders() });
            const data = await res.json();
            this.coverageData = data.coverage || [];
            this.coverageSummary = data.summary || {};
            this.updateSymbolSelectors();
        } catch (e) {
            console.error("Failed to fetch coverage:", e);
        }
    },

    async fetchStrategies() {
        try {
            const res = await fetch(`${API_BASE}/api/backtest/strategies`, { headers: this.getAuthHeaders() });
            const data = await res.json();
            this.strategiesList = data.strategies || [];
            this.updateStrategySelectors();
        } catch (e) {
            console.error("Failed to fetch strategies:", e);
        }
    },

    renderHome() {
        const totalCandles = this.coverageSummary.totalCandles || 0;
        const totalDatasets = this.coverageSummary.coverageEntries || 0;
        const distinctSymbols = this.coverageSummary.distinctSymbols || 0;

        const kpiCandles = document.getElementById("home-kpi-candles");
        const kpiDatasets = document.getElementById("home-kpi-datasets");
        const kpiSymbols = document.getElementById("home-kpi-symbols");

        if (kpiCandles) kpiCandles.innerText = totalCandles.toLocaleString();
        if (kpiDatasets) kpiDatasets.innerText = totalDatasets;
        if (kpiSymbols) kpiSymbols.innerText = distinctSymbols;

        const hbContainer = document.getElementById("home-heartbeats-list");
        if (hbContainer && this.systemStatus) {
            const beats = this.systemStatus.heartbeats || [];
            if (beats.length === 0) {
                hbContainer.innerHTML = `<div class="text-xs text-slate-500 py-3">No live collectors active. Run <code>python3 run_collectors.py</code> to stream real-time data.</div>`;
            } else {
                hbContainer.innerHTML = beats.map(b => `
                    <div class="flex items-center justify-between p-2.5 rounded bg-slate-800/40 border border-slate-750">
                        <div class="flex items-center gap-2">
                            <span class="w-2 h-2 rounded-full ${b.status === "ok" ? "bg-emerald-400" : "bg-amber-400"}"></span>
                            <span class="font-mono text-xs text-slate-300">${b.collector}</span>
                        </div>
                        <div class="text-xs text-slate-400">${b.lastBeat || "Never"}</div>
                    </div>
                `).join("");
            }
        }

        this.fetchRecentBacktests();
    },

    async fetchRecentBacktests() {
        try {
            const res = await fetch(`${API_BASE}/api/backtest/history?limit=10`, { headers: this.getAuthHeaders() });
            const data = await res.json();
            const listEl = document.getElementById("home-recent-backtests");
            if (!listEl) return;

            if (!data.runs || data.runs.length === 0) {
                listEl.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500 text-xs">No backtests run yet. Go to Backtest Studio to run your first simulation!</td></tr>`;
                return;
            }

            listEl.innerHTML = data.runs.map(r => {
                const isPos = (r.expectancyR || 0) > 0;
                return `
                    <tr class="cursor-pointer hover:bg-slate-800/50 transition-colors">
                        <td class="font-mono font-medium text-slate-200">${r.symbol}</td>
                        <td class="text-xs text-slate-400"><span class="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700">${r.market}</span> ${r.timeframe}</td>
                        <td class="text-xs font-mono text-sky-400 max-w-[140px] truncate" title="${r.strategy}">${r.strategy}</td>
                        <td class="text-xs text-slate-300 font-mono">${r.totalTrades}</td>
                        <td class="text-xs font-mono">${r.winRatePct != null ? `${r.winRatePct}%` : "-"}</td>
                        <td class="text-xs font-mono font-semibold ${isPos ? "text-emerald-400" : "text-rose-400"}">
                            ${r.expectancyR != null ? `${r.expectancyR > 0 ? "+" : ""}${r.expectancyR}R` : "-"}
                        </td>
                        <td class="text-xs text-slate-400 font-mono">${(r.createdAt || "").slice(0, 16)}</td>
                    </tr>
                `;
            }).join("");
        } catch (e) {
            console.error("Failed to fetch backtest history:", e);
        }
    },

    renderMasterData() {
        this.renderCoverageTable();
        this.fetchPopularCoins();
        this.pollJobs();
    },

    async fetchPopularCoins() {
        const container = document.getElementById("popular-coins-list");
        if (!container) return;
        try {
            const res = await fetch(`${API_BASE}/api/crypto/popular?market=spot`, { headers: this.getAuthHeaders() });
            const data = await res.json();
            container.innerHTML = (data.items || []).map(coin => `
                <div class="p-3 rounded-lg bg-slate-900 border border-slate-800 hover:border-sky-500/50 transition-all flex flex-col justify-between">
                    <div class="flex items-center justify-between">
                        <span class="font-bold text-slate-200">${coin.symbol}</span>
                        <span class="text-xs font-mono ${coin.priceChangePercent >= 0 ? "text-emerald-400" : "text-rose-400"}">
                            ${coin.priceChangePercent >= 0 ? "+" : ""}${coin.priceChangePercent.toFixed(2)}%
                        </span>
                    </div>
                    <div class="my-1.5 flex items-baseline justify-between">
                        <span class="text-sm font-mono text-slate-300">$${coin.lastPrice.toLocaleString()}</span>
                        <span class="text-[11px] text-slate-500 font-mono">Vol: $${(coin.quoteVolume / 1e6).toFixed(1)}M</span>
                    </div>
                    <button onclick="App.openBackfillModal('${coin.symbol}', '${coin.market}')" class="mt-2 w-full text-xs font-medium py-1.5 rounded bg-slate-800 hover:bg-sky-600 text-sky-300 hover:text-white transition-colors flex items-center justify-center gap-1">
                        <i data-lucide="download" class="w-3.5 h-3.5"></i> Backfill
                    </button>
                </div>
            `).join("");
            if (window.lucide) window.lucide.createIcons();
        } catch (e) {
            console.error("Error popular coins:", e);
        }
    },

    async handleSearchCrypto() {
        const input = document.getElementById("search-crypto-input");
        const marketSelect = document.getElementById("search-market-select");
        const query = (input ? input.value : "").trim();
        const market = marketSelect ? marketSelect.value : "all";

        const resultsContainer = document.getElementById("crypto-search-results");
        if (!resultsContainer) return;

        resultsContainer.innerHTML = `<div class="text-center py-6 text-slate-400 text-xs flex items-center justify-center gap-2"><div class="w-4 h-4 border-2 border-sky-400 border-t-transparent rounded-full animate-spin"></div> Querying Binance live API...</div>`;

        try {
            const res = await fetch(`${API_BASE}/api/crypto/search?q=${encodeURIComponent(query)}&market=${market}&limit=20`, { headers: this.getAuthHeaders() });
            const data = await res.json();
            const items = data.results || [];

            if (items.length === 0) {
                resultsContainer.innerHTML = `<div class="text-center py-6 text-slate-500 text-xs">No matching USDT pairs found on Binance.</div>`;
                return;
            }

            resultsContainer.innerHTML = `
                <table class="w-full text-left custom-table">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Market</th>
                            <th>Last Price</th>
                            <th>24h Change</th>
                            <th>24h Volume (USDT)</th>
                            <th class="text-right">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${items.map(c => `
                            <tr>
                                <td class="font-bold text-slate-100 font-mono">${c.symbol}</td>
                                <td><span class="px-2 py-0.5 rounded text-[11px] uppercase font-semibold ${c.market === "futures" ? "bg-purple-950 text-purple-300 border border-purple-800" : "bg-sky-950 text-sky-300 border border-sky-800"}">${c.market}</span></td>
                                <td class="font-mono text-slate-200">$${c.lastPrice.toLocaleString()}</td>
                                <td class="font-mono text-xs ${c.priceChangePercent >= 0 ? "text-emerald-400" : "text-rose-400"}">
                                    ${c.priceChangePercent >= 0 ? "+" : ""}${c.priceChangePercent.toFixed(2)}%
                                </td>
                                <td class="font-mono text-xs text-slate-400">$${(c.quoteVolume).toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
                                <td class="text-right">
                                    <button onclick="App.openBackfillModal('${c.symbol}', '${c.market}')" class="px-3 py-1 rounded bg-sky-600/20 hover:bg-sky-600 text-sky-300 hover:text-white border border-sky-500/30 text-xs font-semibold transition-all">
                                        ⚡ Backfill
                                    </button>
                                </td>
                            </tr>
                        `).join("")}
                    </tbody>
                </table>
            `;
        } catch (e) {
            resultsContainer.innerHTML = `<div class="text-center py-6 text-rose-400 text-xs">Error querying Binance: ${e}</div>`;
        }
    },

    renderCoverageTable() {
        const tbody = document.getElementById("coverage-table-body");
        if (!tbody) return;

        if (this.coverageData.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500 text-xs">No historical data found in database. Search a symbol above to start backfilling!</td></tr>`;
            return;
        }

        tbody.innerHTML = this.coverageData.map(c => `
            <tr>
                <td class="font-bold font-mono text-slate-200">${c.symbol}</td>
                <td><span class="px-1.5 py-0.5 text-xs rounded uppercase ${c.market === "futures" ? "bg-purple-950 text-purple-400 border border-purple-800" : "bg-slate-800 text-slate-300"}">${c.market}</span></td>
                <td><span class="font-mono font-semibold text-xs text-amber-400">${c.timeframe}</span></td>
                <td class="font-mono text-slate-300 text-xs">${c.candleCount.toLocaleString()}</td>
                <td>
                    <span class="px-2 py-0.5 text-[11px] rounded font-medium ${c.hasFeatures ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-amber-950 text-amber-400 border border-amber-800"}">
                        ${c.hasFeatures ? `✓ Computed (${c.featureCount.toLocaleString()})` : "Missing"}
                    </span>
                </td>
                <td class="text-xs text-slate-400 font-mono">${(c.startDate || "").slice(0, 10)} → ${(c.endDate || "").slice(0, 10)}</td>
                <td class="text-right space-x-1.5">
                    ${!c.hasFeatures ? `
                        <button onclick="App.triggerComputeFeatures('${c.symbol}', '${c.market}', '${c.timeframe}')" class="px-2.5 py-1 text-xs rounded bg-amber-600/20 hover:bg-amber-600 text-amber-300 hover:text-white border border-amber-500/30">
                            Build Features
                        </button>
                    ` : ""}
                    <button onclick="App.launchBacktestFromCoverage('${c.symbol}', '${c.market}', '${c.timeframe}')" class="px-2.5 py-1 text-xs rounded bg-sky-600/20 hover:bg-sky-600 text-sky-300 hover:text-white border border-sky-500/30 font-medium">
                        Backtest →
                    </button>
                </td>
            </tr>
        `).join("");
    },

    openBackfillModal(symbol, market) {
        const modal = document.getElementById("backfill-modal");
        if (!modal) return;
        document.getElementById("modal-backfill-symbol").value = symbol || "BTCUSDT";
        document.getElementById("modal-backfill-market").value = market || "spot";
        modal.classList.remove("hidden");
        modal.classList.add("flex");
    },

    closeBackfillModal() {
        const modal = document.getElementById("backfill-modal");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },

    async submitBackfill() {
        const symbol = document.getElementById("modal-backfill-symbol").value.trim().toUpperCase();
        const market = document.getElementById("modal-backfill-market").value;
        const timeframe = document.getElementById("modal-backfill-timeframe").value;
        const years = parseFloat(document.getElementById("modal-backfill-years").value);
        const autoFeats = document.getElementById("modal-backfill-autofeats").checked;

        try {
            const res = await fetch(`${API_BASE}/api/master-data/backfill`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol, market, timeframe, years,
                    auto_compute_features: autoFeats
                })
            });
            const job = await res.json();
            this.closeBackfillModal();
            this.activeJobId = job.id;
            this.pollJobs();
            this.showToast(`Backfill job launched for ${symbol} ${timeframe}!`, "info");
        } catch (e) {
            alert("Failed to trigger backfill: " + e);
        }
    },

    async triggerComputeFeatures(symbol, market, timeframe) {
        try {
            const res = await fetch(`${API_BASE}/api/master-data/build-features`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({symbol, market, timeframe})
            });
            const job = await res.json();
            this.activeJobId = job.id;
            this.pollJobs();
            this.showToast(`Feature computation job started for ${symbol}...`, "info");
        } catch (e) {
            alert("Error: " + e);
        }
    },

    pollJobs() {
        if (this.jobPollTimer) clearInterval(this.jobPollTimer);
        const update = async () => {
            try {
                const res = await fetch(`${API_BASE}/api/master-data/jobs`, { headers: this.getAuthHeaders() });
                const data = await res.json();
                const jobs = data.jobs || [];
                const container = document.getElementById("active-jobs-container");
                if (!container) return;

                if (jobs.length === 0) {
                    container.innerHTML = `<div class="text-xs text-slate-500 py-2">No active or recent background jobs.</div>`;
                    return;
                }

                container.innerHTML = jobs.slice(0, 5).map(j => {
                    const isDone = j.status === "completed";
                    const isFailed = j.status === "failed";
                    return `
                        <div class="p-3 rounded bg-slate-900 border border-slate-800 space-y-2">
                            <div class="flex items-center justify-between text-xs">
                                <span class="font-bold text-slate-200 font-mono">${j.symbol || ""} ${j.timeframe || ""} (${j.type})</span>
                                <span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold ${isDone ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : isFailed ? "bg-rose-950 text-rose-400" : "bg-sky-950 text-sky-400 animate-pulse"}">
                                    ${j.status}
                                </span>
                            </div>
                            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                                <div class="h-full bg-sky-500 transition-all duration-300" style="width: ${j.progress}%"></div>
                            </div>
                            <div class="text-[11px] text-slate-400 truncate">${j.message || ""}</div>
                        </div>
                    `;
                }).join("");

                if (jobs.some(j => j.id === this.activeJobId && j.status === "completed")) {
                    this.activeJobId = null;
                    await this.fetchCoverage();
                    this.renderCoverageTable();
                }
            } catch (e) {
                console.error("Job poll error:", e);
            }
        };

        update();
        this.jobPollTimer = setInterval(update, 2000);
    },

    // ----------------------------------------------------
    // BACKTEST STUDIO
    // ----------------------------------------------------
    renderBacktestStudio() {
        // Called every time the Backtest Studio tab is opened (see
        // switchTab()). Refreshes both dropdowns from the latest fetched
        // data -- coverage/strategies may have changed (e.g. a new backfill)
        // since app init or the last visit to this tab. Was previously
        // called but never defined, throwing on every visit to this tab
        // (same bug class as the missing showToast() above).
        this.updateSymbolSelectors();
        this.updateStrategySelectors();
        if (this.strategiesList.length > 0) {
            this.onStrategyChanged();
        }
    },

    updateSymbolSelectors() {
        const symbolSelect = document.getElementById("bt-symbol-select");
        if (!symbolSelect) return;

        const uniqueSymbols = [...new Set(this.coverageData.map(c => c.symbol))];
        if (uniqueSymbols.length === 0) {
            symbolSelect.innerHTML = `<option value="BTCUSDT">BTCUSDT</option>`;
            return;
        }

        const currentVal = symbolSelect.value;
        symbolSelect.innerHTML = uniqueSymbols.map(s => `
            <option value="${s}" ${s === currentVal ? "selected" : ""}>${s}</option>
        `).join("");
    },

    updateStrategySelectors() {
        const stratSelect = document.getElementById("bt-strategy-select");
        if (!stratSelect) return;

        const currentVal = stratSelect.value || "confluence_ensemble_v1";
        stratSelect.innerHTML = this.strategiesList.map(st => `
            <option value="${st.id}" ${st.id === currentVal ? "selected" : ""}>
                ${st.validated ? "⭐ " : ""}${st.displayName}
            </option>
        `).join("");

        this.onStrategyChanged();
    },

    onStrategyChanged() {
        const stratSelect = document.getElementById("bt-strategy-select");
        if (!stratSelect) return;
        const selectedId = stratSelect.value;
        const strat = this.strategiesList.find(s => s.id === selectedId);
        if (!strat) return;

        this.selectedStrategy = strat;

        const infoBanner = document.getElementById("bt-strategy-info");
        if (infoBanner) {
            infoBanner.innerHTML = `
                <div class="p-3 rounded bg-slate-900/80 border border-slate-800 flex items-start gap-3">
                    <div class="p-2 rounded bg-sky-950/60 border border-sky-800/50 text-sky-400">
                        <i data-lucide="${strat.validated ? "award" : "compass"}" class="w-5 h-5"></i>
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-sm text-slate-100">${strat.displayName}</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase bg-slate-800 text-slate-300">${strat.category}</span>
                            ${strat.validated ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">HOLDOUT VALIDATED</span>` : ""}
                        </div>
                        <p class="text-xs text-slate-400 mt-1">${strat.description}</p>
                    </div>
                </div>
            `;
            if (window.lucide) window.lucide.createIcons();
        }

        const paramsForm = document.getElementById("bt-dynamic-params-container");
        if (!paramsForm) return;

        const allParams = [...(strat.params || []), ...(strat.sharedParams || [])];
        if (allParams.length === 0) {
            paramsForm.innerHTML = `<div class="text-xs text-slate-500">No adjustable parameters for this strategy.</div>`;
            return;
        }

        paramsForm.innerHTML = `
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                ${allParams.map(p => {
                    const id = `param-input-${p.key}`;
                    if (p.type === "bool") {
                        return `
                            <div class="flex items-center justify-between p-2.5 rounded bg-slate-900 border border-slate-800">
                                <label for="${id}" class="text-xs text-slate-300 font-medium">${p.label}</label>
                                <input type="checkbox" id="${id}" data-key="${p.key}" ${p.default ? "checked" : ""} class="w-4 h-4 accent-sky-500 rounded bg-slate-800 border-slate-700">
                            </div>
                        `;
                    } else if (p.type === "string") {
                        return `
                            <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                                <label for="${id}" class="block text-xs text-slate-400 font-medium mb-1">${p.label}</label>
                                <input type="text" id="${id}" data-key="${p.key}" value="${p.default}" class="w-full text-xs bg-slate-800 text-slate-200 rounded px-2.5 py-1.5 border border-slate-750 font-mono">
                            </div>
                        `;
                    } else {
                        return `
                            <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                                <div class="flex justify-between items-center mb-1">
                                    <label for="${id}" class="text-xs text-slate-400 font-medium">${p.label}</label>
                                    <span id="val-${id}" class="text-xs font-mono text-sky-400 font-semibold">${p.default}</span>
                                </div>
                                <input type="range" id="${id}" data-key="${p.key}" min="${p.min != null ? p.min : 0}" max="${p.max != null ? p.max : 100}" step="${p.step || 1}" value="${p.default}" 
                                    oninput="document.getElementById('val-${id}').innerText = this.value"
                                    class="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer">
                            </div>
                        `;
                    }
                }).join("")}
            </div>
        `;
    },

    collectBacktestParams() {
        const overrides = {};
        const inputs = document.querySelectorAll("#bt-dynamic-params-container [data-key]");
        inputs.forEach(inp => {
            const key = inp.dataset.key;
            if (inp.type === "checkbox") {
                overrides[key] = inp.checked;
            } else if (inp.type === "range" || inp.type === "number") {
                overrides[key] = parseFloat(inp.value);
            } else {
                overrides[key] = inp.value;
            }
        });
        return overrides;
    },

    launchBacktestFromCoverage(symbol, market, timeframe) {
        this.switchTab("backtest");
        const symEl = document.getElementById("bt-symbol-select");
        const mktEl = document.getElementById("bt-market-select");
        const tfEl = document.getElementById("bt-timeframe-select");
        if (symEl) symEl.value = symbol;
        if (mktEl) mktEl.value = market;
        if (tfEl) tfEl.value = timeframe;
    },

    async runBacktest() {
        const symbol = document.getElementById("bt-symbol-select").value;
        const market = document.getElementById("bt-market-select").value;
        const timeframe = document.getElementById("bt-timeframe-select").value;
        const strategy = document.getElementById("bt-strategy-select").value;
        const segment = document.getElementById("bt-segment-select").value;
        const capital = parseFloat(document.getElementById("bt-capital-input").value) || 5000;
        const riskPct = parseFloat(document.getElementById("bt-risk-input").value) || 1.0;

        const customParams = this.collectBacktestParams();

        const btn = document.getElementById("btn-run-backtest");
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<div class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Running simulation...`;

        try {
            const res = await fetch(`${API_BASE}/api/backtest/run`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol, market, timeframe, strategy,
                    data_segment: segment,
                    initial_capital: capital,
                    risk_per_trade_pct: riskPct,
                    save_to_db: true,
                    params: customParams
                })
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Backtest failed");
            }

            const data = await res.json();
            this.renderBacktestResults(data);
            this.showToast(`Simulation completed! ${data.trades.length} trades generated.`, "success");
        } catch (e) {
            alert("Backtest error: " + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    },

    renderBacktestResults(data) {
        document.getElementById("backtest-results-container").classList.remove("hidden");

        const m = data.metrics || {};
        const eq = data.equity || {};

        document.getElementById("res-total-trades").innerText = m.total_trades || 0;
        document.getElementById("res-win-rate").innerText = m.win_rate_pct != null ? `${m.win_rate_pct}%` : "-";
        
        const expEl = document.getElementById("res-expectancy");
        const isExpPos = (m.expectancy_r || 0) > 0;
        expEl.innerText = m.expectancy_r != null ? `${m.expectancy_r > 0 ? "+" : ""}${m.expectancy_r}R` : "-";
        expEl.className = `text-2xl font-bold font-mono ${isExpPos ? "text-emerald-400" : "text-rose-400"}`;

        document.getElementById("res-profit-factor").innerText = m.profit_factor != null ? m.profit_factor : "-";
        document.getElementById("res-max-dd").innerText = m.max_drawdown_r != null ? `${m.max_drawdown_r}R` : "-";

        const retEl = document.getElementById("res-total-return");
        const isRetPos = (eq.total_return_pct || 0) >= 0;
        retEl.innerText = eq.total_return_pct != null ? `${eq.total_return_pct >= 0 ? "+" : ""}${eq.total_return_pct}%` : "-";
        retEl.className = `text-2xl font-bold font-mono ${isRetPos ? "text-emerald-400" : "text-rose-400"}`;

        document.getElementById("res-final-equity").innerText = `$${(eq.final_equity || eq.initial_capital || 5000).toLocaleString()}`;

        this.renderCandleChart(data.candles || [], data.trades || []);
        this.renderEquityCurve(eq.curve || []);
        this.renderTradeTable(data.trades || []);

        document.getElementById("backtest-results-container").scrollIntoView({ behavior: "smooth" });
    },

    renderCandleChart(candles, trades) {
        const container = document.getElementById("candlestick-chart-box");
        if (!container) return;
        container.innerHTML = "";

        if (!window.LightweightCharts) {
            container.innerHTML = `<div class="p-6 text-center text-slate-500 text-xs">LightweightCharts library not loaded.</div>`;
            return;
        }

        this.tvChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 380,
            layout: {
                background: { color: "#0d131f" },
                textColor: "#94a3b8",
            },
            grid: {
                vertLines: { color: "#172033" },
                horzLines: { color: "#172033" },
            },
            timeScale: {
                timeVisible: true,
                borderColor: "#1f293d",
            },
            rightPriceScale: {
                borderColor: "#1f293d",
            }
        });

        this.candleSeries = this.tvChart.addCandlestickSeries({
            upColor: "#10b981",
            downColor: "#ef4444",
            borderVisible: false,
            wickUpColor: "#10b981",
            wickDownColor: "#ef4444",
        });

        this.candleSeries.setData(candles);

        const markers = [];
        trades.forEach(t => {
            if (t.entryTime) {
                const entrySec = Math.floor(t.entryTime / 1000);
                markers.push({
                    time: entrySec,
                    position: t.direction === "LONG" ? "belowBar" : "aboveBar",
                    color: t.direction === "LONG" ? "#10b981" : "#ef4444",
                    shape: t.direction === "LONG" ? "arrowUp" : "arrowDown",
                    text: `${t.direction} @ ${t.entryPrice}`,
                });
            }
            if (t.exitTime) {
                const exitSec = Math.floor(t.exitTime / 1000);
                const isWin = (t.rMultiple || 0) > 0;
                markers.push({
                    time: exitSec,
                    position: t.direction === "LONG" ? "aboveBar" : "belowBar",
                    color: isWin ? "#10b981" : "#f43f5e",
                    shape: "circle",
                    text: `${t.exitReason} (${t.rMultiple > 0 ? "+" : ""}${t.rMultiple}R)`,
                });
            }
        });

        markers.sort((a, b) => a.time - b.time);
        this.candleSeries.setMarkers(markers);
        this.tvChart.timeScale().fitContent();

        window.addEventListener("resize", () => {
            if (this.tvChart && container) {
                this.tvChart.applyOptions({ width: container.clientWidth });
            }
        });
    },

    renderEquityCurve(curve) {
        const ctx = document.getElementById("equity-chart-canvas");
        if (!ctx) return;

        if (this.equityChartInstance) {
            this.equityChartInstance.destroy();
        }

        const labels = curve.map(c => `#${c.trade_num}`);
        const equityData = curve.map(c => c.equity);

        this.equityChartInstance = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    label: "Portfolio Equity ($ USD)",
                    data: equityData,
                    borderColor: "#38bdf8",
                    backgroundColor: "rgba(56, 189, 248, 0.08)",
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: curve.length > 80 ? 0 : 3,
                    pointHoverRadius: 5,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: "index",
                        intersect: false,
                        callbacks: {
                            label: (ctx) => `Equity: $${ctx.parsed.y.toLocaleString()}`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: "#172033" },
                        ticks: { color: "#64748b", font: { size: 10 } }
                    },
                    y: {
                        grid: { color: "#172033" },
                        ticks: {
                            color: "#64748b",
                            font: { size: 10 },
                            callback: (v) => `$${v.toLocaleString()}`
                        }
                    }
                }
            }
        });
    },

    renderTradeTable(trades) {
        const tbody = document.getElementById("bt-trade-table-body");
        if (!tbody) return;

        if (trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-slate-500 text-xs">No trades generated by strategy conditions.</td></tr>`;
            return;
        }

        tbody.innerHTML = trades.map((t, idx) => {
            const isWin = (t.rMultiple || 0) > 0;
            return `
                <tr>
                    <td class="font-mono text-xs text-slate-400">#${idx + 1}</td>
                    <td>
                        <span class="px-2 py-0.5 rounded text-[10px] font-bold ${t.direction === "LONG" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-rose-950 text-rose-400 border border-rose-800"}">
                            ${t.direction}
                        </span>
                    </td>
                    <td class="font-mono text-xs text-slate-300">${t.entryTimeWib || "-"}</td>
                    <td class="font-mono text-xs text-slate-200">$${t.entryPrice.toLocaleString()}</td>
                    <td class="font-mono text-xs text-slate-300">${t.exitTimeWib || "-"}</td>
                    <td class="font-mono text-xs text-slate-200">$${t.exitPrice.toLocaleString()}</td>
                    <td>
                        <span class="px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold ${t.exitReason === "TP" ? "bg-emerald-950 text-emerald-400" : t.exitReason === "SL" ? "bg-rose-950 text-rose-400" : "bg-amber-950 text-amber-400"}">
                            ${t.exitReason}
                        </span>
                    </td>
                    <td class="font-mono text-xs font-semibold ${isWin ? "text-emerald-400" : "text-rose-400"}">
                        ${t.rMultiple != null ? `${t.rMultiple > 0 ? "+" : ""}${t.rMultiple}R` : "-"}
                    </td>
                    <td class="font-mono text-xs text-slate-400">${t.holdingBars} bars</td>
                </tr>
            `;
        }).join("");
    },

    renderStrategyLibrary() {
        const container = document.getElementById("strategies-grid-container");
        if (!container) return;

        container.innerHTML = this.strategiesList.map(st => `
            <div class="p-5 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition-all flex flex-col justify-between space-y-4">
                <div>
                    <div class="flex items-center justify-between">
                        <span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase bg-slate-800 text-slate-300">${st.category}</span>
                        ${st.validated ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">HOLDOUT VALIDATED</span>` : ""}
                    </div>
                    <h3 class="text-base font-bold text-slate-100 mt-2">${st.displayName}</h3>
                    <p class="text-xs text-slate-400 mt-1 leading-relaxed">${st.description}</p>
                </div>
                <div class="pt-3 border-t border-slate-800 flex items-center justify-between">
                    <span class="font-mono text-[11px] text-slate-500">${(st.params || []).length} adjustable params</span>
                    <button onclick="App.selectStrategyForBacktest('${st.id}')" class="px-3 py-1 text-xs rounded bg-sky-600/20 hover:bg-sky-600 text-sky-300 hover:text-white border border-sky-500/30 font-medium transition-colors">
                        Simulate →
                    </button>
                </div>
            </div>
        `).join("");
    },

    selectStrategyForBacktest(stratId) {
        this.switchTab("backtest");
        const el = document.getElementById("bt-strategy-select");
        if (el) {
            el.value = stratId;
            this.onStrategyChanged();
        }
    },

    async submitCustomStrategy() {
        const name = document.getElementById("cust-strat-name").value.trim();
        const disp = document.getElementById("cust-strat-disp").value.trim();
        const desc = document.getElementById("cust-strat-desc").value.trim();

        const rsiLong = parseFloat(document.getElementById("cust-rsi-long").value) || 30;
        const rsiShort = parseFloat(document.getElementById("cust-rsi-short").value) || 70;
        const slAtr = parseFloat(document.getElementById("cust-sl-atr").value) || 1.5;
        const rr = parseFloat(document.getElementById("cust-rr-ratio").value) || 2.5;

        try {
            const res = await fetch(`${API_BASE}/api/custom-strategy/create`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    name, displayName: disp, description: desc,
                    longConditions: [
                        { indicator: "rsi", operator: "<", value: rsiLong }
                    ],
                    shortConditions: [
                        { indicator: "rsi", operator: ">", value: rsiShort }
                    ],
                    slAtrMult: slAtr,
                    riskRewardRatio: rr,
                    useTrailingStop: false
                })
            });
            const data = await res.json();
            await this.fetchStrategies();
            this.showToast(`Custom Strategy "${disp || name}" created and registered!`, "success");
            this.switchTab("backtest");
        } catch (e) {
            alert("Error creating custom strategy: " + e);
        }
    },

    renderSettings() {
        if (!this.systemStatus) return;
        const db = this.systemStatus.database || {};
        const bin = this.systemStatus.binance || {};

        document.getElementById("settings-db-host").innerText = `${db.host}:${db.port}`;
        document.getElementById("settings-db-name").innerText = db.database;
        document.getElementById("settings-db-user").innerText = db.user;
        document.getElementById("settings-bin-spot").innerText = bin.spotApi;
        document.getElementById("settings-bin-futures").innerText = bin.futuresApi;
    },

    // ==========================================
    // BATTLE ROYALE & TOP 3 & AI INSIGHT
    // ==========================================
    async runBattleRoyale() {
        const symbol = document.getElementById("bt-symbol-select").value;
        const market = document.getElementById("bt-market-select").value;
        const timeframe = document.getElementById("bt-timeframe-select").value;
        const segment = document.getElementById("bt-segment-select").value;
        const capital = parseFloat(document.getElementById("bt-capital-input").value) || 5000;
        const riskPct = parseFloat(document.getElementById("bt-risk-input").value) || 1.0;

        const btn = document.getElementById("btn-run-battle-royale");
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<div class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Evaluating 24 models...`;

        try {
            const res = await fetch(`${API_BASE}/api/backtest/run-all`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol, market, timeframe,
                    data_segment: segment,
                    initial_capital: capital,
                    risk_per_trade_pct: riskPct
                })
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Battle Royale execution failed");
            }

            const data = await res.json();
            this.lastBattleRoyaleData = data;
            this.renderBattleRoyaleResults(data);
            this.showToast(`Battle Royale Complete! ${data.top3.length} profitable models discovered.`, "success");
        } catch (e) {
            alert("Battle Royale Error: " + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    },

    renderBattleRoyaleResults(data) {
        const container = document.getElementById("battle-royale-container");
        if (!container) return;
        container.classList.remove("hidden");

        const badgeEl = document.getElementById("br-summary-badge");
        if (badgeEl) {
            badgeEl.innerText = `${data.profitableCount} / ${data.totalEvaluated} Profitable Models | ${data.symbol} (${data.timeframe.toUpperCase()})`;
        }

        // Render Top 3 Cards
        const topCardsContainer = document.getElementById("br-top3-cards");
        if (topCardsContainer) {
            topCardsContainer.innerHTML = "";
            const ranks = [
                { title: "1st Place Winner", badge: "🥇 Rank #1", border: "border-amber-500/60 bg-gradient-to-b from-amber-950/30 to-slate-900", accent: "text-amber-400" },
                { title: "2nd Place Runner-Up", badge: "🥈 Rank #2", border: "border-slate-400/50 bg-gradient-to-b from-slate-800/40 to-slate-900", accent: "text-slate-200" },
                { title: "3rd Place Contender", badge: "🥉 Rank #3", border: "border-orange-600/50 bg-gradient-to-b from-orange-950/25 to-slate-900", accent: "text-orange-400" },
            ];

            if (!data.top3 || data.top3.length === 0) {
                topCardsContainer.innerHTML = `
                    <div class="col-span-3 p-8 text-center bg-slate-900/60 border border-slate-800 rounded-xl">
                        <i data-lucide="alert-triangle" class="w-8 h-8 text-amber-400 mx-auto mb-2"></i>
                        <h4 class="text-sm font-bold text-slate-200">No Models Met Minimum Profitability Criteria</h4>
                        <p class="text-xs text-slate-400 mt-1 max-w-md mx-auto">
                            None of the 24 strategy models yielded net positive expectancy (R > 0) with at least 10 trades on ${data.symbol} (${data.timeframe}). Try a higher timeframe (4h, 1d) or backfill more data.
                        </p>
                    </div>
                `;
            } else {
                data.top3.forEach((strat, idx) => {
                    const cfg = ranks[idx] || ranks[2];
                    const m = strat.metrics || {};
                    const isExpPos = (m.expectancy_r || 0) > 0;

                    const card = document.createElement("div");
                    card.className = `p-5 rounded-xl border ${cfg.border} flex flex-col justify-between space-y-4 shadow-xl relative`;
                    card.innerHTML = `
                        <div class="flex items-center justify-between">
                            <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-slate-800/90 ${cfg.accent} border border-slate-700">
                                ${cfg.badge}
                            </span>
                            <span class="text-[11px] font-mono text-slate-400">Score: <b class="text-slate-100">${strat.rankScore}</b></span>
                        </div>

                        <div>
                            <h4 class="text-sm font-bold text-slate-100 leading-snug">${strat.displayName || strat.strategy}</h4>
                            <span class="text-[11px] text-slate-400 uppercase tracking-wider">${strat.category || 'Quantitative'}</span>
                        </div>

                        <div class="grid grid-cols-2 gap-2 text-xs bg-slate-950/50 p-3 rounded-lg border border-slate-800/60 font-mono">
                            <div>
                                <span class="text-[10px] text-slate-500 uppercase">Expectancy</span>
                                <div class="font-bold ${isExpPos ? 'text-emerald-400' : 'text-rose-400'}">${m.expectancy_r > 0 ? '+' : ''}${m.expectancy_r}R</div>
                            </div>
                            <div>
                                <span class="text-[10px] text-slate-500 uppercase">Profit Factor</span>
                                <div class="font-bold text-slate-100">${m.profit_factor}</div>
                            </div>
                            <div>
                                <span class="text-[10px] text-slate-500 uppercase">Win Rate</span>
                                <div class="font-bold text-slate-100">${m.win_rate_pct}%</div>
                            </div>
                            <div>
                                <span class="text-[10px] text-slate-500 uppercase">Total Trades</span>
                                <div class="font-bold text-slate-100">${m.total_trades}</div>
                            </div>
                        </div>

                        <div class="space-y-2 pt-1">
                            <button onclick="App.openDeployModal('${strat.strategy}', '${strat.displayName}', ${idx + 1}, ${strat.rankScore})" class="w-full py-2 px-3 rounded-lg bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-white font-bold text-xs shadow-md shadow-emerald-500/20 flex items-center justify-center gap-1.5 transition-all">
                                <i data-lucide="zap" class="w-3.5 h-3.5"></i> Deploy to Paper & Telegram
                            </button>
                            <button onclick="App.inspectStrategyInBacktest('${strat.strategy}')" class="w-full py-1.5 px-3 rounded-lg bg-slate-800 hover:bg-slate-750 text-slate-300 font-semibold text-xs flex items-center justify-center gap-1.5 border border-slate-700 transition-colors">
                                <i data-lucide="line-chart" class="w-3.5 h-3.5"></i> View Candlestick Chart
                            </button>
                        </div>
                    `;
                    topCardsContainer.appendChild(card);
                });
            }
        }

        // Render AI Quantitative Insight Box
        const aiBox = document.getElementById("br-ai-insight-box");
        const ai = data.aiInsight;
        if (aiBox && ai) {
            const isStrong = ai.verdict === "STRONG_DEPLOY";
            const badgeBg = isStrong ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" : "bg-amber-500/20 text-amber-300 border-amber-500/40";

            aiBox.innerHTML = `
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-sky-500 to-blue-600 flex items-center justify-center text-white shadow-lg shadow-sky-500/25">
                            <i data-lucide="brain-circuit" class="w-5 h-5"></i>
                        </div>
                        <div>
                            <div class="flex items-center gap-2">
                                <h3 class="text-sm font-bold text-slate-100">AI Quantitative Strategy Diagnosis</h3>
                                <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border uppercase tracking-wider ${badgeBg}">
                                    ${ai.verdictLabel}
                                </span>
                            </div>
                            <p class="text-xs text-slate-400 mt-0.5">Automated algorithmic analysis and regime compatibility engine</p>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        <span class="text-xs text-slate-400 flex items-center gap-1.5">
                            <i data-lucide="send" class="w-3.5 h-3.5 text-sky-400"></i> Telegram Dispatch: 
                            <b class="text-emerald-400">@SignBTBot (1487656060)</b>
                        </span>
                    </div>
                </div>

                <div class="p-4 rounded-xl bg-slate-850/80 border border-slate-800 text-xs text-slate-200 leading-relaxed font-medium">
                    ${ai.headline}
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <!-- Column 1: Market Regime -->
                    <div class="p-4 rounded-xl bg-slate-850 border border-slate-800 space-y-2">
                        <span class="text-[11px] font-bold uppercase tracking-wider text-sky-400 flex items-center gap-1.5">
                            <i data-lucide="compass" class="w-3.5 h-3.5"></i> Market Regime
                        </span>
                        <div class="text-xs font-bold text-slate-100">${ai.regime?.state || 'Neutral'}</div>
                        <div class="text-[11px] text-slate-400">${ai.regime?.volatility || 'Normal Volatility'}</div>
                        <p class="text-[11px] text-slate-500 pt-1 leading-normal">${ai.regime?.fitNote || ''}</p>
                    </div>

                    <!-- Column 2: Statistical Reliability -->
                    <div class="p-4 rounded-xl bg-slate-850 border border-slate-800 space-y-2">
                        <span class="text-[11px] font-bold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                            <i data-lucide="shield-check" class="w-3.5 h-3.5"></i> Statistical Confidence
                        </span>
                        <div class="text-xs font-bold text-slate-100">${ai.topStrategy?.sampleConfidence || 'Moderate'}</div>
                        <p class="text-[11px] text-slate-400 pt-1 leading-normal">${ai.topStrategy?.sampleNote || ''}</p>
                    </div>

                    <!-- Column 3: Sizing & Risk -->
                    <div class="p-4 rounded-xl bg-slate-850 border border-slate-800 space-y-2">
                        <span class="text-[11px] font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                            <i data-lucide="percent" class="w-3.5 h-3.5"></i> Risk Sizing Prescription
                        </span>
                        <div class="text-xs font-bold text-slate-100 font-mono">Recommended: ${ai.riskProfile?.recommendedRiskPct || 1.0}% / trade</div>
                        <div class="text-[11px] text-slate-400 font-mono">Half-Kelly Max: ${ai.riskProfile?.halfKellyPct || 1.0}%</div>
                        <p class="text-[11px] text-slate-500 pt-1 leading-normal">
                            Risk of 4 Consecutive Losses: <b class="text-slate-300 font-mono">${ai.riskProfile?.prob4Losses || 0}%</b>
                        </p>
                    </div>
                </div>

                <!-- Prescriptions List -->
                <div class="space-y-2 pt-1">
                    <h5 class="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                        <i data-lucide="check-circle-2" class="w-3.5 h-3.5 text-emerald-400"></i> AI Actionable Prescriptions
                    </h5>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                        ${(ai.prescriptions || []).map(p => `
                            <div class="p-3 rounded-lg bg-slate-850/60 border border-slate-800/80 text-xs text-slate-300 flex items-start gap-2">
                                <span class="text-emerald-400 font-bold mt-0.5">➔</span>
                                <div>${p}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        // Render All Evaluated Table
        const allTbody = document.getElementById("br-all-table-body");
        if (allTbody && data.allResults) {
            allTbody.innerHTML = "";
            data.allResults.forEach((r, idx) => {
                const m = r.metrics || {};
                const isProf = r.isProfitable;
                const isTop3 = idx < (data.top3 || []).length && isProf;

                const tr = document.createElement("tr");
                tr.className = isTop3 ? "bg-amber-500/5 hover:bg-amber-500/10" : "hover:bg-slate-800/40";
                tr.innerHTML = `
                    <td class="font-mono font-bold ${isTop3 ? 'text-amber-400' : 'text-slate-400'}">
                        ${isTop3 ? `🥇 #${idx + 1}` : `#${idx + 1}`}
                    </td>
                    <td>
                        <div class="font-bold text-slate-200 text-xs">${r.displayName || r.strategy}</div>
                        <div class="text-[10px] text-slate-500 font-mono">${r.strategy}</div>
                    </td>
                    <td class="text-xs text-slate-400">${r.category || 'Other'}</td>
                    <td class="font-mono text-xs text-slate-200">${m.total_trades || 0}</td>
                    <td class="font-mono text-xs text-slate-200">${m.win_rate_pct != null ? `${m.win_rate_pct}%` : '-'}</td>
                    <td class="font-mono text-xs font-bold ${ (m.expectancy_r || 0) > 0 ? 'text-emerald-400' : 'text-rose-400' }">
                        ${m.expectancy_r != null ? `${m.expectancy_r > 0 ? '+' : ''}${m.expectancy_r}R` : '-'}
                    </td>
                    <td class="font-mono text-xs text-slate-200">${m.profit_factor != null ? m.profit_factor : '-'}</td>
                    <td class="font-mono text-xs text-rose-400">${m.max_drawdown_r != null ? `${m.max_drawdown_r}R` : '-'}</td>
                    <td class="font-mono text-xs text-slate-300 font-bold">${r.rankScore > 0 ? r.rankScore : '-'}</td>
                    <td class="text-xs">
                        ${isProf 
                            ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">PROFITABLE</span>` 
                            : `<span class="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700">${r.rankNote || 'Unprofitable'}</span>`}
                    </td>
                    <td class="text-right">
                        <button onclick="App.openDeployModal('${r.strategy}', '${r.displayName}', ${idx + 1}, ${r.rankScore})" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-emerald-600 hover:text-white text-slate-300 font-semibold text-[11px] border border-slate-700 transition-colors">
                            Deploy
                        </button>
                    </td>
                `;
                allTbody.appendChild(tr);
            });
        }

        if (window.lucide) window.lucide.createIcons();
        container.scrollIntoView({ behavior: "smooth" });
    },

    inspectStrategyInBacktest(stratName) {
        const stratSelect = document.getElementById("bt-strategy-select");
        if (stratSelect) {
            stratSelect.value = stratName;
            this.onStrategyChanged();
            this.runBacktest();
        }
    },

    // ==========================================
    // DEPLOY STRATEGY TO PAPER & TELEGRAM MODAL
    // ==========================================
    openDeployModal(stratName, dispName, rank = null, score = null) {
        this.currentDeployTarget = {
            strategy: stratName,
            displayName: dispName || stratName,
            rank: rank,
            score: score
        };

        const modal = document.getElementById("deploy-modal");
        if (!modal) return;

        const sym = document.getElementById("bt-symbol-select")?.value || "BTCUSDT";
        const tf = document.getElementById("bt-timeframe-select")?.value || "1d";
        const mkt = document.getElementById("bt-market-select")?.value || "spot";

        document.getElementById("deploy-modal-strat-name").innerText = dispName || stratName;
        document.getElementById("deploy-modal-pair").innerText = `${sym} (${mkt.toUpperCase()} / ${tf})`;
        document.getElementById("deploy-modal-rank").innerText = rank ? `Rank #${rank} (Score: ${score || '-'})` : "Custom Model";

        modal.classList.remove("hidden");
        modal.classList.add("flex");
        if (window.lucide) window.lucide.createIcons();
    },

    closeDeployModal() {
        const modal = document.getElementById("deploy-modal");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },

    async confirmDeploy() {
        if (!this.currentDeployTarget) return;

        const sym = document.getElementById("bt-symbol-select")?.value || "BTCUSDT";
        const mkt = document.getElementById("bt-market-select")?.value || "spot";
        const tf = document.getElementById("bt-timeframe-select")?.value || "1d";
        const capital = parseFloat(document.getElementById("deploy-modal-capital").value) || 5000;
        const riskPct = parseFloat(document.getElementById("deploy-modal-risk").value) || 1.0;
        const sendTg = document.getElementById("deploy-modal-telegram").checked;

        const btn = document.getElementById("btn-confirm-deploy");
        const origText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<div class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Deploying...`;

        try {
            const res = await fetch(`${API_BASE}/api/paper/deploy`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol: sym,
                    market: mkt,
                    timeframe: tf,
                    strategy_name: this.currentDeployTarget.strategy,
                    allocated_capital: capital,
                    risk_per_trade_pct: riskPct,
                    send_telegram: sendTg,
                    rank: this.currentDeployTarget.rank,
                    rank_score: this.currentDeployTarget.score
                })
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Deployment failed");
            }

            const data = await res.json();
            this.closeDeployModal();
            this.showToast(data.message || "Strategy deployed to Live Paper Trading!", "success");

            // Refresh paper trading data
            await this.fetchPaperData();

            // Ask user if they want to switch to the Paper tab
            if (confirm("🚀 Strategy successfully deployed to Live Paper Trading!\nTelegram alert dispatched to @SignBTBot.\n\nWould you like to switch to the Paper Trading tab now?")) {
                this.switchTab("paper");
            }
        } catch (e) {
            alert("Deployment error: " + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
    },

    async sendTelegramTestPing() {
        const btn = document.getElementById("btn-tg-test");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<div class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></div>`;
        }

        try {
            const res = await fetch(`${API_BASE}/api/telegram/test`, {
                method: "POST",
                headers: this.getAuthHeaders()
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(`Ping delivered to @${data.bot?.username || 'bot'} (Chat ID: ${data.chat_id})!`, "success");
            } else {
                alert(`Telegram Ping Error: ${data.error || 'Failed to dispatch'}`);
            }
        } catch (e) {
            alert("Telegram Test Error: " + e.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i data-lucide="send" class="w-3 h-3"></i> Ping Bot`;
                if (window.lucide) window.lucide.createIcons();
            }
        }
    },

    // ========================================================================
    // ML STUDIO & CIRCUIT BREAKER METHODS
    // ========================================================================
    async fetchMLData() {
        try {
            // 1. Fetch circuit breakers
            const cbRes = await fetch(`${API_BASE}/api/paper/circuit-breakers`, {
                headers: this.getAuthHeaders()
            });
            if (cbRes.ok) {
                const cb = await cbRes.json();
                const badge = document.getElementById("ml-breaker-badge");
                const details = document.getElementById("ml-breaker-details");
                if (badge) {
                    if (cb.status === "NORMAL") {
                        badge.className = "mt-1.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold bg-emerald-950 text-emerald-400 border border-emerald-800";
                        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400"></span> NOMINAL`;
                    } else {
                        badge.className = "mt-1.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold bg-rose-950 text-rose-400 border border-rose-800";
                        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-rose-400 animate-pulse"></span> TRIPPED`;
                    }
                }
                if (details) {
                    details.innerText = `Active: ${cb.active_positions}/${cb.max_concurrent_positions} • Daily PnL: $${cb.today_realized_pnl_usd.toFixed(2)}`;
                }
            }

            // 2. Fetch models count
            const modelsRes = await fetch(`${API_BASE}/api/ml/models`, {
                headers: this.getAuthHeaders()
            });
            if (modelsRes.ok) {
                const md = await modelsRes.json();
                const modelsStat = document.getElementById("ml-stat-models");
                if (modelsStat) modelsStat.innerText = md.models ? md.models.length : 0;
            }

            // 3. Fetch experiments list
            const expRes = await fetch(`${API_BASE}/api/ml/experiments`, {
                headers: this.getAuthHeaders()
            });
            if (expRes.ok) {
                const expData = await expRes.json();
                const experiments = expData.experiments || [];
                const statExp = document.getElementById("ml-stat-experiments");
                if (statExp) statExp.innerText = experiments.length;

                const tbody = document.getElementById("ml-experiments-table-body");
                if (tbody) {
                    if (experiments.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-slate-500 text-xs">No ML tournaments found. Launch a new tournament using the button above.</td></tr>`;
                    } else {
                        tbody.innerHTML = experiments.map(exp => {
                            const rocColor = exp.roc_auc !== null ? (exp.roc_auc >= 0.55 ? "text-emerald-400 font-bold" : (exp.roc_auc >= 0.5 ? "text-sky-400" : "text-amber-400")) : "text-slate-500";
                            const rocText = exp.roc_auc !== null ? exp.roc_auc.toFixed(3) : "N/A";
                            const rColor = exp.ml_test_r >= 0 ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
                            const baseColor = exp.baseline_test_r >= 0 ? "text-slate-300" : "text-slate-400";

                            return `
                                <tr class="border-b border-slate-800/60 hover:bg-slate-800/30 transition-colors">
                                    <td class="py-3 px-3">
                                        <div class="font-mono text-xs font-bold text-slate-200">${exp.symbol} <span class="text-[10px] text-sky-400 uppercase font-sans">(${exp.market})</span></div>
                                        <div class="text-[10px] text-slate-400 font-mono">${exp.timeframe} • ${exp.experiment_id.split("-").slice(-2).join("-")}</div>
                                    </td>
                                    <td class="py-3 px-3 font-mono text-xs text-sky-300">${exp.strategy}</td>
                                    <td class="py-3 px-3">
                                        <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-purple-950/70 text-purple-300 border border-purple-800/50">
                                            ${exp.winner_model}
                                        </span>
                                    </td>
                                    <td class="py-3 px-3 font-mono text-xs font-bold text-amber-400">${(exp.threshold * 100).toFixed(0)}%</td>
                                    <td class="py-3 px-3 font-mono text-xs ${rocColor}">${rocText}</td>
                                    <td class="py-3 px-3 font-mono text-xs ${baseColor}">${exp.baseline_test_r >= 0 ? "+" : ""}${exp.baseline_test_r.toFixed(2)}R</td>
                                    <td class="py-3 px-3 font-mono text-xs ${rColor}">${exp.ml_test_r >= 0 ? "+" : ""}${exp.ml_test_r.toFixed(2)}R</td>
                                    <td class="py-3 px-3 font-mono text-xs text-slate-300">${exp.ml_test_win_rate ? exp.ml_test_win_rate.toFixed(1) + "%" : "N/A"} <span class="text-[10px] text-slate-500">(${exp.ml_test_trades}t)</span></td>
                                    <td class="py-3 px-3">
                                        <div class="flex items-center gap-2">
                                            <button onclick="App.inspectMLExperiment('${exp.experiment_id}')" class="px-2.5 py-1 rounded text-[11px] font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors">
                                                Inspect
                                            </button>
                                            <button onclick="App.openDeployMLModal('${exp.experiment_id}', '${exp.winner_model}', ${exp.threshold})" class="px-2.5 py-1 rounded text-[11px] font-bold bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white shadow-sm transition-all flex items-center gap-1">
                                                <i data-lucide="zap" class="w-3 h-3"></i> Deploy
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            `;
                        }).join("");
                        if (window.lucide) window.lucide.createIcons();
                    }
                }
            }
        } catch (e) {
            console.error("fetchMLData error:", e);
        }
    },

    async tripCircuitBreaker() {
        if (!confirm("⚠️ Are you sure you want to trigger the EMERGENCY STOP circuit breaker?\n\nThis will immediately halt all new paper position openings across all strategies.")) {
            return;
        }
        try {
            const res = await fetch(`${API_BASE}/api/paper/circuit-breakers/emergency-stop`, {
                method: "POST",
                headers: this.getAuthHeaders()
            });
            const data = await res.json();
            this.showToast("Emergency kill switch activated!", "warning");
            await this.fetchMLData();
        } catch (e) {
            alert("Error tripping circuit breaker: " + e.message);
        }
    },

    async resetCircuitBreaker() {
        try {
            const res = await fetch(`${API_BASE}/api/paper/circuit-breakers/reset`, {
                method: "POST",
                headers: this.getAuthHeaders()
            });
            const data = await res.json();
            this.showToast("Circuit breaker reset: new trade entries re-enabled", "success");
            await this.fetchMLData();
        } catch (e) {
            alert("Error resetting circuit breaker: " + e.message);
        }
    },

    openRunMLModal() {
        const modal = document.getElementById("modal-run-ml");
        if (modal) {
            modal.classList.remove("hidden");
            modal.classList.add("flex");
        }
    },

    closeRunMLModal() {
        const modal = document.getElementById("modal-run-ml");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },

    async submitMLRun() {
        const btn = document.getElementById("btn-submit-ml-run");
        const origHtml = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<div class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Training Tournament...`;
        }

        const symbol = document.getElementById("ml-run-symbol").value.trim().toUpperCase();
        const market = document.getElementById("ml-run-market").value;
        const timeframe = document.getElementById("ml-run-timeframe").value;
        const strategy_name = document.getElementById("ml-run-strategy").value;
        const model_type = document.getElementById("ml-run-model").value;
        const target_type = document.getElementById("ml-run-target").value;
        const min_validation_trades = parseInt(document.getElementById("ml-run-min-trades").value) || 5;

        try {
            const res = await fetch(`${API_BASE}/api/ml/run-experiment`, {
                method: "POST",
                headers: {
                    ...this.getAuthHeaders(),
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    symbol, market, timeframe, strategy_name,
                    model_type, target_type, min_validation_trades
                })
            });
            const data = await res.json();
            if (res.ok && data.status === "success") {
                this.closeRunMLModal();
                this.showToast(`Tournament completed! Winner: ${data.result?.winner?.model_type || 'Selected'}`, "success");
                await this.fetchMLData();
            } else {
                alert(`ML Run Failed: ${data.detail || JSON.stringify(data)}`);
            }
        } catch (e) {
            alert(`ML Run Network Error: ${e.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origHtml;
                if (window.lucide) window.lucide.createIcons();
            }
        }
    },

    openDeployMLModal(modelId, modelType, threshold) {
        document.getElementById("deploy-ml-model-id").value = modelId;
        document.getElementById("deploy-ml-label-id").innerText = modelId;
        document.getElementById("deploy-ml-label-model").innerText = modelType;
        document.getElementById("deploy-ml-label-threshold").innerText = `${(threshold * 100).toFixed(1)}%`;

        const modal = document.getElementById("modal-deploy-ml");
        if (modal) {
            modal.classList.remove("hidden");
            modal.classList.add("flex");
        }
    },

    closeDeployMLModal() {
        const modal = document.getElementById("modal-deploy-ml");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },

    async confirmDeployML() {
        const btn = document.getElementById("btn-confirm-deploy-ml");
        const origText = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<div class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Deploying...`;
        }

        const model_id = document.getElementById("deploy-ml-model-id").value;
        const allocated_capital = parseFloat(document.getElementById("deploy-ml-capital").value) || 5000.0;
        const risk_per_trade_pct = parseFloat(document.getElementById("deploy-ml-risk").value) || 1.0;
        const send_telegram = document.getElementById("deploy-ml-telegram").checked;

        try {
            const res = await fetch(`${API_BASE}/api/ml/deploy-to-paper`, {
                method: "POST",
                headers: {
                    ...this.getAuthHeaders(),
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    model_id, allocated_capital, risk_per_trade_pct, send_telegram
                })
            });
            const data = await res.json();
            if (res.ok && data.status === "success") {
                this.closeDeployMLModal();
                this.showToast(data.message, "success");
                await this.fetchPaperData();
                if (confirm("🚀 ML Model successfully deployed to Live Paper Trading!\nSignals will now be gated with AI confidence.\n\nSwitch to the Paper Trading tab to view active configs?")) {
                    this.switchTab("paper");
                }
            } else {
                alert(`Deploy Failed: ${data.detail || JSON.stringify(data)}`);
            }
        } catch (e) {
            alert(`Deploy Error: ${e.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origText;
            }
        }
    },

    async inspectMLExperiment(experimentId) {
        try {
            const res = await fetch(`${API_BASE}/api/ml/experiments/${experimentId}`, {
                headers: this.getAuthHeaders()
            });
            if (!res.ok) {
                alert("Could not load experiment details.");
                return;
            }
            const data = await res.json();
            const exp = data.experiment || {};
            const winner = data.winner || {};
            const baseTest = exp.baseline_test_metrics || {};
            const mlTest = exp.ml_test_metrics || {};
            const diag = exp.probability_diagnostics || {};

            document.getElementById("inspect-ml-title").innerText = data.experiment_id;
            const container = document.getElementById("inspect-ml-content");
            container.innerHTML = `
                <div class="grid grid-cols-2 gap-3 p-3 rounded-lg bg-slate-800/80 border border-slate-700">
                    <div><span class="text-slate-400">Winner Model:</span> <b class="text-purple-400">${winner.model_type || 'N/A'}</b></div>
                    <div><span class="text-slate-400">Selected Threshold:</span> <b class="text-amber-400 font-mono">${(exp.threshold * 100).toFixed(1)}%</b></div>
                    <div><span class="text-slate-400">ROC-AUC:</span> <b class="text-sky-400 font-mono">${diag.roc_auc !== null && diag.roc_auc !== undefined ? diag.roc_auc.toFixed(4) : 'N/A'}</b></div>
                    <div><span class="text-slate-400">Brier Score:</span> <b class="text-slate-200 font-mono">${diag.brier_score !== null && diag.brier_score !== undefined ? diag.brier_score.toFixed(4) : 'N/A'}</b></div>
                    <div><span class="text-slate-400">Dataset Rows:</span> <b class="text-slate-200 font-mono">${data.dataset_rows || 0}</b></div>
                    <div><span class="text-slate-400">Evaluation Mode:</span> <b class="text-emerald-400">No-Lookahead Chronological</b></div>
                </div>

                <div class="p-3 rounded-lg bg-slate-850 border border-slate-800 space-y-2">
                    <h4 class="font-bold text-slate-200 text-xs">Locked OOS Test Performance (Baseline vs ML Filter)</h4>
                    <table class="w-full text-left custom-table text-xs">
                        <thead>
                            <tr>
                                <th>Metric</th>
                                <th>Rule-Based Baseline</th>
                                <th>ML Filter (${(exp.threshold * 100).toFixed(0)}% Gate)</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>Net Outcome R</td>
                                <td class="font-mono">${baseTest.total_outcome_r !== undefined ? baseTest.total_outcome_r.toFixed(2) + "R" : "0.00R"}</td>
                                <td class="font-mono font-bold ${mlTest.total_outcome_r >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${mlTest.total_outcome_r !== undefined ? (mlTest.total_outcome_r >= 0 ? "+" : "") + mlTest.total_outcome_r.toFixed(2) + "R" : "0.00R"}</td>
                            </tr>
                            <tr>
                                <td>Win Rate</td>
                                <td class="font-mono">${baseTest.win_rate_pct !== undefined ? baseTest.win_rate_pct.toFixed(1) + "%" : "N/A"}</td>
                                <td class="font-mono font-bold text-sky-400">${mlTest.win_rate_pct !== undefined ? mlTest.win_rate_pct.toFixed(1) + "%" : "N/A"}</td>
                            </tr>
                            <tr>
                                <td>Profit Factor</td>
                                <td class="font-mono">${baseTest.profit_factor !== undefined ? baseTest.profit_factor.toFixed(2) : "N/A"}</td>
                                <td class="font-mono font-bold text-amber-400">${mlTest.profit_factor !== undefined ? mlTest.profit_factor.toFixed(2) : "N/A"}</td>
                            </tr>
                            <tr>
                                <td>Trade Count</td>
                                <td class="font-mono">${baseTest.trade_count || 0}</td>
                                <td class="font-mono">${mlTest.trade_count || 0}</td>
                            </tr>
                            <tr>
                                <td>Max Drawdown R</td>
                                <td class="font-mono text-rose-400">-${baseTest.max_drawdown_r !== undefined ? baseTest.max_drawdown_r.toFixed(2) + "R" : "0.00R"}</td>
                                <td class="font-mono text-rose-400">-${mlTest.max_drawdown_r !== undefined ? mlTest.max_drawdown_r.toFixed(2) + "R" : "0.00R"}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <div class="p-3 rounded-lg bg-slate-850 border border-slate-800 space-y-1.5">
                    <h4 class="font-bold text-slate-200 text-xs">Features Used</h4>
                    <div class="flex flex-wrap gap-1.5">
                        ${(winner.feature_columns || []).map(f => `<span class="px-2 py-0.5 rounded bg-slate-800 text-[10px] font-mono text-slate-300 border border-slate-700">${f}</span>`).join("")}
                    </div>
                </div>
            `;

            const modal = document.getElementById("modal-inspect-ml");
            if (modal) {
                modal.classList.remove("hidden");
                modal.classList.add("flex");
            }
        } catch (e) {
            alert("Error inspecting experiment: " + e.message);
        }
    },

    closeInspectMLModal() {
        const modal = document.getElementById("modal-inspect-ml");
        if (modal) {
            modal.classList.add("hidden");
            modal.classList.remove("flex");
        }
    },
};

window.App = App;
document.addEventListener("DOMContentLoaded", () => App.init());

