/* Dashboard UX enhancements: sortable tables, sticky headers, persistent horizontal scrolling,
 * and robust Telegram ping diagnostics. This file intentionally stays separate from trading logic.
 */
(function () {
    "use strict";

    const SORTABLE_TABLE_SELECTOR = ".custom-table";
    const SORTABLE_EXCLUDES = new Set(["Action", "Actions", "Action(s)", "Status / Audit Note"]);

    function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    }

    function rawCellValue(cell) {
        if (!cell) return "";
        return (cell.dataset.sortValue || cell.textContent || "").replace(/\s+/g, " ").trim();
    }

    function typedValue(value) {
        const text = String(value || "").trim();
        if (!text) return { type: "empty", value: "" };

        const numeric = text.replace(/[$€£¥,]/g, "").replace(/%/g, "").replace(/R$/i, "").trim();
        if (/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?$/i.test(numeric)) {
            return { type: "number", value: Number(numeric) };
        }

        const dateMs = Date.parse(text);
        if (!Number.isNaN(dateMs) && /[-/:T]/.test(text)) {
            return { type: "date", value: dateMs };
        }
        return { type: "text", value: text.toLocaleLowerCase() };
    }

    function compareValues(a, b, direction) {
        const av = typedValue(a);
        const bv = typedValue(b);
        if (av.type === "empty" && bv.type !== "empty") return 1;
        if (bv.type === "empty" && av.type !== "empty") return -1;
        if (av.type === bv.type) {
            if (av.value < bv.value) return -1 * direction;
            if (av.value > bv.value) return 1 * direction;
            return 0;
        }
        // Keep mixed-type columns deterministic; empty values are already last.
        return String(av.value).localeCompare(String(bv.value), undefined, { numeric: true, sensitivity: "base" }) * direction;
    }

    function setSortIndicator(th, direction) {
        const old = th.querySelector(".table-sort-indicator");
        if (old) old.remove();
        if (!direction) return;
        const indicator = document.createElement("span");
        indicator.className = "table-sort-indicator";
        indicator.setAttribute("aria-hidden", "true");
        indicator.textContent = direction === 1 ? " ↑" : " ↓";
        th.appendChild(indicator);
    }

    function sortTable(table, columnIndex, direction) {
        const tbody = table.tBodies[0];
        if (!tbody) return;
        const rows = Array.from(tbody.rows);
        if (rows.length < 2) return;
        // Assign newly inserted rows indices after all existing rows to preserve stable tie order.
        let nextOriginalIndex = rows.reduce((max, row) => {
            const value = Number(row.dataset.originalIndex);
            return Number.isFinite(value) ? Math.max(max, value) : max;
        }, -1) + 1;
        rows.forEach(row => {
            if (row.dataset.originalIndex === undefined) {
                row.dataset.originalIndex = String(nextOriginalIndex++);
            }
        });
        rows.sort((ra, rb) => {
            const result = compareValues(rawCellValue(ra.cells[columnIndex]), rawCellValue(rb.cells[columnIndex]), direction);
            return result || Number(ra.dataset.originalIndex || 0) - Number(rb.dataset.originalIndex || 0);
        });
        rows.forEach(row => tbody.appendChild(row));
    }

    function tbodyRows(table) {
        return table.tBodies[0] ? Array.from(table.tBodies[0].rows) : [];
    }

    function makeTableSortable(table) {
        if (table.dataset.sortableReady === "1") return;
        const thead = table.tHead;
        if (!thead || !thead.rows[0]) return;

        table.dataset.sortableReady = "1";
        Array.from(tbodyRows(table)).forEach((row, index) => { row.dataset.originalIndex = String(index); });

        Array.from(thead.rows[0].cells).forEach((th, index) => {
            const label = (th.textContent || "").replace(/[↑↓]/g, "").trim();
            if (!label || SORTABLE_EXCLUDES.has(label) || th.querySelector("button")) return;
            th.classList.add("table-sortable-header");
            // Keep the native column-header role; tabindex + keyboard handling provide activation.
            th.setAttribute("tabindex", "0");
            th.setAttribute("aria-label", `${label}, sortable`);
            th.removeAttribute("aria-sort");
            th.addEventListener("click", () => {
                const current = table.dataset.sortColumn === String(index) ? Number(table.dataset.sortDirection || 0) : 0;
                const direction = current === 1 ? -1 : 1;
                table.dataset.sortColumn = String(index);
                table.dataset.sortDirection = String(direction);
                Array.from(thead.rows[0].cells).forEach((cell, i) => {
                    setSortIndicator(cell, i === index ? direction : 0);
                    if (i === index) {
                        cell.setAttribute("aria-sort", direction === 1 ? "ascending" : "descending");
                        cell.setAttribute("aria-label", `${label}, sorted ${direction === 1 ? "ascending" : "descending"}`);
                    } else {
                        cell.removeAttribute("aria-sort");
                        if (cell.classList.contains("table-sortable-header")) {
                            const cellLabel = (cell.textContent || "").replace(/[↑↓]/g, "").trim();
                            cell.setAttribute("aria-label", `${cellLabel}, sortable`);
                        }
                    }
                });
                sortTable(table, index, direction);
                let statusRegion = table.parentElement.querySelector(".dashboard-sort-status");
                if (!statusRegion) {
                    statusRegion = document.createElement("div");
                    statusRegion.className = "dashboard-sort-status";
                    statusRegion.setAttribute("role", "status");
                    statusRegion.setAttribute("aria-live", "polite");
                    statusRegion.setAttribute("aria-atomic", "true");
                    table.parentElement.appendChild(statusRegion);
                }
                statusRegion.textContent = `Sorted by ${label} ${direction === 1 ? "ascending" : "descending"}.`;
            });
            th.addEventListener("keydown", event => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    th.click();
                }
            });
        });
    }

    function enhanceTable(table) {
        if (!table || table.dataset.dashboardEnhanced === "1") return;
        const parent = table.parentElement;
        if (!parent) return;

        table.dataset.dashboardEnhanced = "1";
        makeTableSortable(table);

        const head = table.tHead;
        if (head) head.classList.add("dashboard-sticky-head");

        const viewport = parent;
        viewport.classList.add("dashboard-table-scroll");

        if (!viewport.querySelector(":scope > .dashboard-horizontal-scroll")) {
            const bar = document.createElement("div");
            bar.className = "dashboard-horizontal-scroll";
            bar.setAttribute("role", "region");
            bar.setAttribute("tabindex", "0");
            const tableLabel = table.getAttribute("aria-label")
                || (table.caption && table.caption.textContent.trim())
                || table.dataset.label
                || "table";
            bar.setAttribute("aria-label", "Horizontal scroll for " + tableLabel);
            const spacer = document.createElement("div");
            spacer.className = "dashboard-horizontal-spacer";
            bar.appendChild(spacer);
            viewport.appendChild(bar);

            const syncWidth = () => {
                spacer.style.width = `${table.scrollWidth}px`;
                bar.style.display = table.scrollWidth > viewport.clientWidth ? "block" : "none";
                if (Math.abs(bar.scrollLeft - viewport.scrollLeft) > 1) bar.scrollLeft = viewport.scrollLeft;
            };
            let syncing = false;
            bar.addEventListener("scroll", () => {
                if (syncing) return;
                syncing = true;
                viewport.scrollLeft = bar.scrollLeft;
                requestAnimationFrame(() => { syncing = false; });
            });
            viewport.addEventListener("scroll", () => {
                if (syncing) return;
                syncing = true;
                bar.scrollLeft = viewport.scrollLeft;
                requestAnimationFrame(() => { syncing = false; });
            });
            if (window.ResizeObserver) {
                // One observer per table is sufficient; retain it so removed tables
                // can release their observed elements instead of leaking observers.
                const resizeObserver = new ResizeObserver(syncWidth);
                resizeObserver.observe(viewport);
                resizeObserver.observe(table);
                table._dashboardResizeObserver = resizeObserver;
            }
            syncWidth();
        }
    }

    function enhanceAllTables() {
        document.querySelectorAll(SORTABLE_TABLE_SELECTOR).forEach(enhanceTable);
    }

    // Coalesce DOM-driven scrollbar measurements so a burst of text/row mutations
    // performs at most one layout pass per animation frame.
    const pendingScrollbarTables = new Set();
    let scrollbarSyncFrame = null;

    function scheduleHorizontalScrollbarSync(tables) {
        tables.forEach(table => pendingScrollbarTables.add(table));
        if (scrollbarSyncFrame !== null) return;
        scrollbarSyncFrame = window.requestAnimationFrame(() => {
            scrollbarSyncFrame = null;
            const changedTables = Array.from(pendingScrollbarTables);
            pendingScrollbarTables.clear();
            syncHorizontalScrollbars(changedTables);
        });
    }

    function syncHorizontalScrollbars(tables) {
        const bars = tables
            ? Array.from(tables, table => table && table.parentElement
                ? table.parentElement.querySelector(":scope > .dashboard-horizontal-scroll")
                : null).filter(Boolean)
            : Array.from(document.querySelectorAll(".dashboard-horizontal-scroll"));
        bars.forEach(bar => {
            const viewport = bar.parentElement;
            const table = viewport && viewport.querySelector(":scope > table");
            const spacer = bar.firstElementChild;
            if (!viewport || !table || !spacer) return;
            spacer.style.width = `${table.scrollWidth}px`;
            bar.style.display = table.scrollWidth > viewport.clientWidth ? "block" : "none";
            if (Math.abs(bar.scrollLeft - viewport.scrollLeft) > 1) {
                bar.scrollLeft = viewport.scrollLeft;
            }
        });
    }

    function installTelegramPing() {
        if (!window.App || window.App._enhancedTelegramPingInstalled) return;
        window.App._enhancedTelegramPingInstalled = true;

        window.App.sendTelegramTestPing = async function () {
            const btn = document.getElementById("btn-tg-test");
            const original = btn ? btn.innerHTML : "";
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<div class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Testing...`;
            }

            try {
                const response = await fetch("/api/telegram/test", {
                    method: "POST",
                    headers: this.getAuthHeaders(),
                    body: JSON.stringify({})
                });
                let data = {};
                try { data = await response.json(); } catch (_) {}

                if (!response.ok) {
                    const detail = data.detail || data.error || `HTTP ${response.status}`;
                    throw new Error(detail);
                }
                if (!data.success) throw new Error(data.error || "Telegram test failed");

                const bot = data.bot || {};
                const target = data.chat_id ? ` → chat ${data.chat_id}` : "";
                this.showToast(`Telegram OK: @${bot.username || "bot"} verified${target}. Test message sent.`, "success");
            } catch (error) {
                this.showToast(`Telegram Ping failed: ${error.message || error}. Check bot token, chat ID, and that the bot can message the target chat.`, "error");
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = original || `<i data-lucide="send" class="w-3 h-3"></i> Ping Bot`;
                    if (window.lucide) window.lucide.createIcons();
                }
            }
        };
    }

    function init() {
        enhanceAllTables();
        installTelegramPing();
        const observer = new MutationObserver(mutations => {
            // Collect candidates first so nested/overlapping inserted subtrees do
            // not cause the same table to be inspected repeatedly in one batch.
            const tablesToEnhance = new Set();
            const tablesToSync = new Set();
            for (const mutation of mutations) {
                const target = mutation.target.nodeType === Node.ELEMENT_NODE
                    ? mutation.target
                    : mutation.target.parentElement;
                const changedTable = target && target.closest(SORTABLE_TABLE_SELECTOR);
                if (changedTable && changedTable.dataset.dashboardEnhanced === "1") {
                    tablesToSync.add(changedTable);
                }
                for (const node of mutation.removedNodes) {
                    if (node.nodeType !== Node.ELEMENT_NODE) continue;
                    const removedTables = [];
                    if (node.matches(SORTABLE_TABLE_SELECTOR)) removedTables.push(node);
                    node.querySelectorAll(SORTABLE_TABLE_SELECTOR).forEach(table => removedTables.push(table));
                    removedTables.forEach(table => {
                        // A table may be moved rather than removed; only release observers
                        // after the DOM mutation batch has left it disconnected.
                        if (table.isConnected || !table._dashboardResizeObserver) return;
                        table._dashboardResizeObserver.disconnect();
                        delete table._dashboardResizeObserver;
                    });
                }
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== Node.ELEMENT_NODE) continue;
                    if (node.matches(SORTABLE_TABLE_SELECTOR)) tablesToEnhance.add(node);
                    node.querySelectorAll(SORTABLE_TABLE_SELECTOR).forEach(table => tablesToEnhance.add(table));
                }
            }
            tablesToEnhance.forEach(enhanceTable);
            // Older browsers without ResizeObserver still need their scrollbar
            // width refreshed when API-driven table content changes.
            if (tablesToSync.size) scheduleHorizontalScrollbarSync(tablesToSync);
            // App may be initialized after this enhancement script loads.
            installTelegramPing();
        });
        observer.observe(document.body, { childList: true, characterData: true, subtree: true });
        // Coalesce resize bursts into one layout pass per animation frame.
        // Window resize can fire many times while a user drags the browser edge.
        let resizeFrame = null;
        window.addEventListener("resize", () => {
            if (resizeFrame !== null) return;
            resizeFrame = window.requestAnimationFrame(() => {
                resizeFrame = null;
                enhanceAllTables();
                // ResizeObserver is not available in some older browsers; refresh
                // existing scrollbars explicitly as a fallback after viewport changes.
                syncHorizontalScrollbars();
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();


/* Theme preference: presentation-only enhancement; does not touch trading/API logic. */
(function () {
    "use strict";

    const STORAGE_KEY = "signal-bot-theme";
    const root = document.documentElement;

    function systemPreference() {
        try {
            return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
                ? "light"
                : "dark";
        } catch (_) {
            return "dark";
        }
    }

    function readPreference() {
        try {
            const saved = window.localStorage.getItem(STORAGE_KEY);
            return saved === "light" || saved === "dark" ? saved : systemPreference();
        } catch (_) {
            return systemPreference();
        }
    }

    function applyTheme(theme) {
        const normalized = theme === "light" ? "light" : "dark";
        root.dataset.theme = normalized;
        root.classList.toggle("dark", normalized === "dark");

        const button = document.getElementById("dashboard-theme-toggle");
        if (button) {
            const nextTheme = normalized === "dark" ? "light" : "dark";
            button.setAttribute("aria-label", "Switch to " + nextTheme + " theme");
            button.setAttribute("title", "Switch to " + nextTheme + " theme");
            button.setAttribute("aria-pressed", String(normalized === "light"));
            const icon = button.querySelector("[data-lucide]");
            if (icon) icon.setAttribute("data-lucide", normalized === "dark" ? "sun" : "moon");
            const label = button.querySelector(".dashboard-theme-label");
            if (label) label.textContent = normalized === "dark" ? "Light" : "Dark";
            if (window.lucide && typeof window.lucide.createIcons === "function") {
                window.lucide.createIcons();
            }
        }
    }

    function mountToggle() {
        if (document.getElementById("dashboard-theme-toggle")) return;
        const brandContainer = document.querySelector("aside > div:first-child");
        const brandRow = brandContainer && brandContainer.querySelector(":scope > div:first-child");
        if (!brandContainer || !brandRow) return;

        const button = document.createElement("button");
        button.id = "dashboard-theme-toggle";
        button.type = "button";
        button.className = "dashboard-theme-toggle";
        button.innerHTML = '<i data-lucide="sun" aria-hidden="true"></i><span class="dashboard-theme-label">Light</span>';
        button.addEventListener("click", function () {
            const next = root.dataset.theme === "dark" ? "light" : "dark";
            try { window.localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
            applyTheme(next);
        });
        // Keep the toggle on its own row so it cannot squeeze or overflow the logo/title row.
        button.style.margin = "0.75rem 1.25rem 0";
        button.style.width = "calc(100% - 2.5rem)";
        button.style.justifyContent = "center";
        brandRow.insertAdjacentElement("afterend", button);
        applyTheme(root.dataset.theme || readPreference());
    }

    function initTheme() {
        applyTheme(readPreference());
        mountToggle();

        // Watch only sidebar internals for toggle removal; unrelated dashboard mutations
        // should not trigger this observer. A lightweight body observer handles sidebar replacement.
        let observedSidebar = null;
        let sidebarObserver = null;
        const observeCurrentSidebar = () => {
            const sidebar = document.querySelector("body > aside");
            if (sidebar !== observedSidebar) {
                if (sidebarObserver) sidebarObserver.disconnect();
                observedSidebar = sidebar;
                sidebarObserver = null;
                if (sidebar) {
                    sidebarObserver = new MutationObserver(() => {
                        if (!document.getElementById("dashboard-theme-toggle")) mountToggle();
                    });
                    sidebarObserver.observe(sidebar, { childList: true, subtree: true });
                }
            }
            if (!document.getElementById("dashboard-theme-toggle")) mountToggle();
        };
        observeCurrentSidebar();
        if (document.body) {
            const sidebarRootObserver = new MutationObserver(observeCurrentSidebar);
            sidebarRootObserver.observe(document.body, { childList: true });
        }

        // Follow OS theme changes only while the user has not chosen a theme.
        let userOverride = false;
        let mediaQuery;
        try {
            mediaQuery = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
        } catch (_) {
            mediaQuery = null;
        }
        if (mediaQuery) {
            const onSystemThemeChange = () => {
                if (userOverride) return;
                try {
                    const saved = window.localStorage.getItem(STORAGE_KEY);
                    if (saved === "light" || saved === "dark") return;
                } catch (_) {
                    // If storage is unavailable, an in-session toggle still takes precedence.
                }
                applyTheme(systemPreference());
            };
            if (typeof mediaQuery.addEventListener === "function") {
                mediaQuery.addEventListener("change", onSystemThemeChange);
            } else if (typeof mediaQuery.addListener === "function") {
                mediaQuery.addListener(onSystemThemeChange);
            }
            // Delegate so a newly mounted toggle still records an in-session choice.
            document.addEventListener("click", event => {
                if (event.target && event.target.closest("#dashboard-theme-toggle")) {
                    userOverride = true;
                }
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initTheme, { once: true });
    } else {
        initTheme();
    }
})();
