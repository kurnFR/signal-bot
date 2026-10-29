import { beforeEach, describe, expect, it, vi } from "vitest";

const { getJson } = vi.hoisted(() => ({ getJson: vi.fn() }));
vi.mock("./api", () => ({ getJson }));

import { fetchPaperTradingSnapshot } from "./paperTrading";

const metrics = {
  totalTrades: 0, wins: 0, losses: 0, winRatePct: 0, cumulativeR: 0,
  expectancyR: 0, netPnl: 0, byStrategy: [],
};
const breaker = {
  status: "NORMAL", emergency_stop_active: false, emergency_stop_reason: null,
  active_positions: 0, max_concurrent_positions: 5, today_realized_pnl_usd: 0,
  max_daily_loss_usd: 500, open_notional_usd: 0, max_total_notional_usd: 25000,
  can_open_new_position: true, message: "Entries allowed",
};
const validResponses: Record<string, unknown> = {
  "/api/paper/positions": { positions: [] },
  "/api/paper/configs": { configs: [] },
  "/api/paper/orders?limit=20": { orders: [] },
  "/api/paper/trades?limit=20": { trades: [] },
  "/api/paper/metrics": { metrics },
  "/api/paper/circuit-breakers": breaker,
};

beforeEach(() => {
  getJson.mockReset();
  getJson.mockImplementation((path: string) => Promise.resolve(validResponses[path]));
});

describe("fetchPaperTradingSnapshot response contracts", () => {
  it("accepts verified empty datasets and a valid risk guard", async () => {
    const result = await fetchPaperTradingSnapshot();
    expect(result.positions).toEqual([]);
    expect(result.metrics.totalTrades).toBe(0);
    expect(result.breaker.can_open_new_position).toBe(true);
  });

  it("rejects malformed risk status rather than treating it as verified", async () => {
    getJson.mockImplementation((path: string) => Promise.resolve(
      path === "/api/paper/circuit-breakers"
        ? { ...breaker, can_open_new_position: "true" }
        : validResponses[path],
    ));
    await expect(fetchPaperTradingSnapshot()).rejects.toThrow(/risk status is not verified/);
  });

  it("rejects non-finite performance metrics", async () => {
    getJson.mockImplementation((path: string) => Promise.resolve(
      path === "/api/paper/metrics"
        ? { metrics: { ...metrics, netPnl: Number.NaN } }
        : validResponses[path],
    ));
    await expect(fetchPaperTradingSnapshot()).rejects.toThrow(/invalid response/);
  });

  it("rejects a missing dataset envelope", async () => {
    getJson.mockImplementation((path: string) => Promise.resolve(
      path === "/api/paper/positions" ? { items: [] } : validResponses[path],
    ));
    await expect(fetchPaperTradingSnapshot()).rejects.toThrow(/invalid response/);
  });

  it("rejects malformed position, config, order, and trade rows", async () => {
    const cases: Array<[string, unknown]> = [
      ["/api/paper/positions", { positions: [{ id: "1", symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s", direction: "LONG", entry_price: 10, current_price: 11, stop_loss: null, take_profit: null, quantity: 1, unrealized_pnl_pct: 1, unrealized_r: 1, allocated_capital: 100, status: "OPEN", last_update_time: null }] }],
      ["/api/paper/configs", { configs: [{ id: 1, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s", is_active: "yes", allocated_capital: 100, risk_per_trade_pct: 1, telegram_enabled: false, report_enabled: true, created_at: "now" }] }],
      ["/api/paper/orders?limit=20", { orders: [{ id: 1, symbol: "BTCUSDT", market: "spot", timeframe: "1h", side: "BUY", order_type: "MARKET", status: "PENDING", price: Number.POSITIVE_INFINITY, created_at: "now", filled_at: null, state_reason: null }] }],
      ["/api/paper/trades?limit=20", { trades: [{ id: 1, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s", direction: "LONG", entry_price: 10, exit_price: 11, net_pnl: Number.NaN, net_return_pct: 1, r_multiple: 1, exit_reason: "target", entry_time_wib: "now", exit_time_wib: "later" }] }],
    ];
    for (const [badPath, badPayload] of cases) {
      getJson.mockImplementation((path: string) => Promise.resolve(path === badPath ? badPayload : validResponses[path]));
      await expect(fetchPaperTradingSnapshot()).rejects.toThrow(/invalid response/);
    }
  });

});
