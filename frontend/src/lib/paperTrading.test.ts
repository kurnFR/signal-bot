import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getJson, assertApiPath } = vi.hoisted(() => ({
  getJson: vi.fn(),
  assertApiPath: vi.fn((path: string) => {
    if (!path.startsWith("/api/")) throw new TypeError("bad path");
  }),
}));
vi.mock("./api", () => ({ getJson, assertApiPath }));

import {
  cancelPaperOrder,
  closePaperPosition,
  deployPaperStrategy,
  fetchPaperTradingSnapshot,
  PaperTradingActionError,
  resetPaperCircuitBreaker,
  tripPaperCircuitBreaker,
} from "./paperTrading";

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
      ["/api/paper/orders?limit=20", { orders: [{ id: 1, config_id: 2, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s", direction: "LONG", signal_open_time: 1793239200, entry_time: 1793239200, requested_entry_price: Number.POSITIVE_INFINITY, stop_loss: 95, take_profit: 110, atr_at_signal: 2, ml_model_id: null, ml_probability: null, status: "PENDING", state_reason: null, fill_price: null, position_id: null, created_at: "now", updated_at: "now", filled_at: null, terminal_at: null }] }],
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
        direction: "LONG", entry_time: 1793239200000, entry_time_wib: "2026-10-29 09:00:00",
        entry_price: 100, current_price: 101, stop_loss: 95, take_profit: 110,
        current_stop: 96, trailing_active: false, trail_activation_r: 1,
        trail_distance_atr_mult: 2, initial_risk: 5, allocated_capital: 1000,
        risk_per_trade_pct: 1, risk_amount: 10, stop_distance: 5, quantity: 2,
        notional_value: 202, entry_fee: 0.1, unrealized_pnl_pct: 1,
        unrealized_r: 0.2, holding_bars: 1, max_hold_bars: 20,
        status: "OPEN", last_update_time: 1793239260000,
      }] },
      "/api/paper/configs": { configs: [{
        id: 4, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s",
        is_active: true, allocated_capital: 1000, risk_per_trade_pct: 1,
        ml_model_id: null, telegram_enabled: false, report_enabled: true, created_at: "2026-10-29T02:00:00",
      }] },
      "/api/paper/orders?limit=20": { orders: [{
        id: 5, config_id: 4, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy_name: "s",
        direction: "LONG", signal_open_time: 1793239200, entry_time: 1793239200,
        requested_entry_price: 100, stop_loss: 95, take_profit: 110, atr_at_signal: 2,
        ml_model_id: null, ml_probability: null, status: "PENDING", state_reason: null,
        fill_price: null, position_id: null, created_at: "2026-10-29T02:00:00", filled_at: null,
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

describe("Paper Trading mutation contracts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  describe("cancelPaperOrder", () => {
    it("successfully cancels an order with authentication token", async () => {
      window.localStorage.setItem("token", "trader-token");
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "cancelled", order_id: 42 }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await cancelPaperOrder(42);
      expect(res).toEqual({ status: "cancelled", order_id: 42 });
      expect(fetchMock).toHaveBeenCalledWith("/api/paper/orders/42/cancel", expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer trader-token" }),
      }));
    });

    it("rejects non-positive order IDs without network call", async () => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      await expect(cancelPaperOrder(0)).rejects.toThrow(PaperTradingActionError);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it("handles 409 transition conflict", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ detail: "Order is already filled" }),
        { status: 409, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(cancelPaperOrder(42)).rejects.toThrow("Order is already filled");
    });
  });

  describe("closePaperPosition", () => {
    it("successfully closes a position", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ status: "closed", position_id: 10 }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await closePaperPosition(10);
      expect(res).toEqual({ status: "closed", position_id: 10 });
      expect(fetchMock).toHaveBeenCalledWith("/api/paper/positions/10/close", expect.objectContaining({
        method: "POST",
      }));
    });

    it("handles 404 for missing or already closed position", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ detail: "Position not found" }),
        { status: 404, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(closePaperPosition(999)).rejects.toThrow("Position not found");
    });
  });

  describe("deployPaperStrategy", () => {
    it("submits valid deploy payload and returns server result", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          message: "Strategy deployed",
          config: { id: 1 },
          syncResult: { evaluated_configs: 1 },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await deployPaperStrategy({
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "confluence_ensemble_v1",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      });

      expect(res.status).toBe("success");
      expect(res.message).toBe("Strategy deployed");
      expect(fetchMock).toHaveBeenCalledWith("/api/paper/deploy", expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          symbol: "BTCUSDT",
          market: "spot",
          timeframe: "1h",
          strategy_name: "confluence_ensemble_v1",
          allocated_capital: 5000,
          risk_per_trade_pct: 1.0,
          send_telegram: false,
        }),
      }));
    });

    it("rejects invalid capital or risk values", async () => {
      await expect(deployPaperStrategy({
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "confluence_ensemble_v1",
        allocated_capital: 50, // < 100
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      })).rejects.toThrow(/Allocated capital/);
    });

    it("maps telegramAlert and telegram consistently in response", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          message: "Strategy deployed",
          config: { id: 10 },
          telegramAlert: { success: true, message_id: 123 },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await deployPaperStrategy({
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "confluence_ensemble_v1",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: true,
      });

      expect(res.status).toBe("success");
      expect(res.telegramAlert).toEqual({ success: true, message_id: 123 });
      expect(res.telegram).toEqual({ success: true, message_id: 123 });
    });
  });

  describe("circuit breaker trip & reset", () => {
    it("trips emergency stop", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          message: "Emergency stop tripped",
          breaker: { ...breaker, status: "TRIPPED", emergency_stop_active: true, can_open_new_position: false },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await tripPaperCircuitBreaker("High volatility");
      expect(res.status).toBe("success");
      expect(res.breaker.emergency_stop_active).toBe(true);
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/paper/circuit-breakers/emergency-stop"), expect.objectContaining({
        method: "POST",
      }));
    });

    it("resets circuit breaker", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          message: "Circuit breaker reset",
          breaker: { ...breaker, status: "NORMAL", emergency_stop_active: false, can_open_new_position: true },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await resetPaperCircuitBreaker();
      expect(res.status).toBe("success");
      expect(res.breaker.can_open_new_position).toBe(true);
      expect(fetchMock).toHaveBeenCalledWith("/api/paper/circuit-breakers/reset", expect.objectContaining({
        method: "POST",
      }));
    });
  });
});
