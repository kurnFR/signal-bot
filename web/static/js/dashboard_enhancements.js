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
        rows.forEach((row, index) => {
            if (row.dataset.originalIndex === undefined) row.dataset.originalIndex = String(index);
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
            th.setAttribute("role", "button");
            th.setAttribute("tabindex", "0");
            th.setAttribute("aria-label", `Sort by ${label}`);
            th.addEventListener("click", () => {
                const current = table.dataset.sortColumn === String(index) ? Number(table.dataset.sortDirection || 0) : 0;
                const direction = current === 1 ? -1 : 1;
                table.dataset.sortColumn = String(index);
                table.dataset.sortDirection = String(direction);
                Array.from(thead.rows[0].cells).forEach((cell, i) => setSortIndicator(cell, i === index ? direction : 0));
                sortTable(table, index, direction);
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
            bar.setAttribute("aria-label", "Horizontal table scroll");
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
                new ResizeObserver(syncWidth).observe(viewport);
                new ResizeObserver(syncWidth).observe(table);
            }
            syncWidth();
        }
    }

    function enhanceAllTables() {
        document.querySelectorAll(SORTABLE_TABLE_SELECTOR).forEach(enhanceTable);
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
                this.showToast(`Telegram OK: @${escapeHtml(bot.username || "bot")} verified${target}. Test message sent.`, "success");
            } catch (error) {
                this.showToast(`Telegram Ping failed: ${escapeHtml(error.message || error)}. Check bot token, chat ID, and that the bot can message the target chat.`, "error");
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
        const observer = new MutationObserver(() => {
            enhanceAllTables();
            installTelegramPing();
        });
        observer.observe(document.body, { childList: true, subtree: true });
        window.addEventListener("resize", enhanceAllTables);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
