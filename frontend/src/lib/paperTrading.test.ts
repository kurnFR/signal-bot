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


  it("accepts populated rows matching the backend serializers", async () => {
    const populated: Record<string, unknown> = {
      "/api/paper/positions": { positions: [{
        id: 3, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s",
        direction: "LONG", entry_time: "2026-10-29T02:00:00", entry_time_wib: "2026-10-29 09:00:00",
        entry_price: 100, current_price: 101, stop_loss: 95, take_profit: 110,
        current_stop: 96, trailing_active: false, trail_activation_r: 1,
        trail_distance_atr_mult: 2, initial_risk: 5, allocated_capital: 1000,
        risk_per_trade_pct: 1, risk_amount: 10, stop_distance: 5, quantity: 2,
        notional_value: 202, entry_fee: 0.1, unrealized_pnl_pct: 1,
        unrealized_r: 0.2, holding_bars: 1, max_hold_bars: 20,
        status: "OPEN", last_update_time: "2026-10-29T02:01:00",
      }] },
      "/api/paper/configs": { configs: [{
        id: 4, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s",
        is_active: true, allocated_capital: 1000, risk_per_trade_pct: 1,
        ml_model_id: null, telegram_enabled: false, report_enabled: true, created_at: "2026-10-29T02:00:00",
      }] },
      "/api/paper/orders?limit=20": { orders: [{
        id: 5, symbol: "BTCUSDT", market: "spot", timeframe: "1h", side: "BUY",
        order_type: "MARKET", status: "PENDING", quantity: 2, price: null,
        created_at: "2026-10-29T02:00:00", filled_at: null, state_reason: null,
      }] },
      "/api/paper/trades?limit=20": { trades: [{
        id: 6, position_id: 3, symbol: "BTCUSDT", market: "spot", timeframe: "1h",
        strategy_name: "s", direction: "LONG", entry_time: "2026-10-29T02:00:00",
        entry_time_wib: "2026-10-29 09:00:00", entry_price: 100,
        exit_time: "2026-10-29T03:00:00", exit_time_wib: "2026-10-29 10:00:00",
        exit_price: 101, exit_reason: "target", stop_loss: 95, take_profit: 110,
        quantity: 2, notional_value: 202, gross_pnl: 2, entry_fee: 0.1,
        exit_fee: 0.1, funding_pnl: 0, net_pnl: 1.8, allocated_capital: 1000,
        risk_per_trade_pct: 1, risk_amount: 10, net_return_pct: 0.18,
        r_multiple: 0.2, holding_bars: 1, created_at: "2026-10-29T03:00:00",
      }] },
    };
    getJson.mockImplementation((path: string) => Promise.resolve(populated[path] ?? validResponses[path]));
    const result = await fetchPaperTradingSnapshot();
    expect(result.positions).toHaveLength(1);
    expect(result.configs).toHaveLength(1);
    expect(result.orders).toHaveLength(1);
    expect(result.trades).toHaveLength(1);
  });

});
