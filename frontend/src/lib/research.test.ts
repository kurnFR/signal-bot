import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchResearchSnapshot } from "./research";

const responses: Record<string, unknown> = {
  "/api/research/retailbot2/positions?limit=100": {
    positions: [{ id: 1, symbol: "BTCUSDT", side: "LONG", mode: "inverse", entry_price: 100, stop_price: 95, target_price: 110, atr: 2, rr: 2, units: 0.1, strategy: "BB_REV_INV", strategies_json: null, confluence_score: 4, reason: "test", entry_time: "2026-10-26T10:00:00Z" }],
  },
  "/api/research/retailbot2/trades?limit=50": {
    trades: [{ id: 2, symbol: "ETHUSDT", side: "LONG", mode: "shadow", entry_price: 50, stop_price: 48, target_price: 55, units: 1, strategy: "BB_REV", confluence_score: 3, status: "CLOSED", exit_price: 55, exit_reason: "target", net_pnl: 5, pnl_percent: 10, entry_time: "2026-10-26T10:00:00Z", exit_time: "2026-10-26T11:00:00Z" }],
  },
  "/api/research/retailbot2/stats": {
    by_strategy: [{ strategy: "BB_REV_INV", mode: "inverse", total_trades: 12, wins: 7, losses: 5, total_pnl: 125.5, last_updated: "2026-10-26T12:00:00Z" }],
    by_mode: { inverse: { realized_pnl: 125.5, closed_count: 12, wins: 7, win_rate: 58.3, open_count: 1 }, shadow: { realized_pnl: -25, closed_count: 4, wins: 1, win_rate: 25, open_count: 0 } },
  },
  "/api/research/retailbot2/strategies": {
    strategies: [{ strategy_id: "BB_REV_INV", mode: "inverse", evaluation_enabled: true, paper_enabled: true, telegram_enabled: false, report_enabled: true, auto_promote_enabled: false, historical_only: false, updated_at: null }],
  },
};
function installFetch(overrides: Record<string, unknown> = {}) {
  const payloads = { ...responses, ...overrides };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") throw new Error("Research snapshot must use GET");
    const path = String(input);
    return Promise.resolve(new Response(JSON.stringify(payloads[path]), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  }));
}
beforeEach(() => {
  window.localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("Research response contracts", () => {
  it("accepts backend-shaped populated research data", async () => {
    const result = await fetchResearchSnapshot();
    expect(result.positions[0].entry_time).toContain("2026-10-26");
    expect(result.trades[0].net_pnl).toBe(5);
    expect(result.stats.by_mode.inverse.win_rate).toBe(58.3);
    expect(result.strategies[0].auto_promote_enabled).toBe(false);
  });

  it("rejects positions with stale or missing serializer fields", async () => {
    installFetch({
      "/api/research/retailbot2/positions?limit=100": { positions: [{ id: 1, symbol: "BTCUSDT", mode: "inverse", entry_price: 100, current_price: 110, unrealized_pnl: 10, opened_at: "2026-10-26T10:00:00Z" }] },
    });
    await expect(fetchResearchSnapshot()).rejects.toThrow(/positions returned an invalid response/i);
  });

  it("rejects trades with malformed PnL fields", async () => {
    installFetch({
      "/api/research/retailbot2/trades?limit=50": { trades: [{ id: 2, symbol: "ETHUSDT", side: "LONG", mode: "shadow", entry_price: 50, stop_price: 48, target_price: 55, units: 1, strategy: "BB_REV", confluence_score: 3, status: "CLOSED", exit_price: 55, exit_reason: "target", net_pnl: "5", pnl_percent: 10, entry_time: "2026-10-26T10:00:00Z", exit_time: "2026-10-26T11:00:00Z" }] },
    });
    await expect(fetchResearchSnapshot()).rejects.toThrow(/trades returned an invalid response/i);
  });

  it("rejects malformed lifecycle flags instead of coercing them", async () => {
    installFetch({
      "/api/research/retailbot2/strategies": { strategies: [{ strategy_id: "BB_REV_INV", mode: "inverse", evaluation_enabled: "true", paper_enabled: true, telegram_enabled: false, report_enabled: true, auto_promote_enabled: false }] },
    });
    await expect(fetchResearchSnapshot()).rejects.toThrow(/strategies returned an invalid response/i);
  });
});
