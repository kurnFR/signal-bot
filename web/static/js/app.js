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
                    </div>
                    <div>
                        <button onclick="App.togglePaperStrategy('${c.symbol}', '${c.market}', '${c.timeframe}', '${c.strategy_name}', ${!c.is_active})" 
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

    async togglePaperStrategy(symbol, market, timeframe, strategy, newActive) {
        try {
            const res = await fetch(`${API_BASE}/api/paper/configs`, {
                method: "POST",
                headers: this.getAuthHeaders(),
                body: JSON.stringify({
                    symbol, market, timeframe,
                    strategy_name: strategy,
                    is_active: newActive
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
            const data = await res.json();
            const r = data.results || {};
            this.showToast(`Market sync evaluated ${r.evaluated_configs} active strategies: ${r.new_positions_opened} new entries, ${r.positions_closed} exits.`, "success");
            await this.fetchPaperData();
        } catch (e) {
            alert("Sync error: " + e);
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
        } else if (tabId === "strategies") {
            if (titleEl) titleEl.innerText = "Strategy Library & Rule Builder";
            if (descEl) descEl.innerText = "Explore strategy methodologies, benchmark performance, and create custom strategies";
            this.renderStrategyLibrary();
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

    showToast(message, type = "info") {
        const toast = document.createElement("div");
        toast.className = `fixed bottom-5 right-5 z-50 px-4 py-3 rounded-lg shadow-xl border text-xs font-semibold flex items-center gap-2.5 transition-all duration-300 transform translate-y-2 opacity-0 ${type === "success" ? "bg-emerald-950 text-emerald-300 border-emerald-800" : "bg-slate-900 text-sky-300 border-slate-750"}`;
        toast.innerHTML = `<span class="w-2 h-2 rounded-full ${type === "success" ? "bg-emerald-400" : "bg-sky-400"}"></span> ${message}`;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.classList.remove("translate-y-2", "opacity-0");
        }, 10);

        setTimeout(() => {
            toast.classList.add("translate-y-2", "opacity-0");
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }
};

window.App = App;
document.addEventListener("DOMContentLoaded", () => App.init());
