import { afterEach, describe, expect, it, vi } from "vitest";
import { executeBacktest, fetchBacktestHistory, fetchBacktestRunDetails, fetchPromotionSnapshot, type PromotionPolicy, type BacktestExecutionInput } from "./backtestStudio";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const validPolicy: PromotionPolicy = {
  enabled: true,
  min_test_trades: 30,
  min_profit_factor: 1.2,
  min_expectancy_r: 0.05,
  max_drawdown_r: 8,
  min_rank_score: 0.1,
  require_holdout: true,
  send_telegram: true,
};

function mockSnapshot(policy: unknown, events: unknown = []) {
  const methods: Array<string | undefined> = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    methods.push(init?.method);
    const path = String(input);
    const payload = path.includes("promotion-policy") ? { policy } : { events };
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, methods };
}

describe("fetchPromotionSnapshot contract validation", () => {
  it("returns the complete verified policy and audit events", async () => {
    const events = [{ decision_hash: "abc", symbol: "BTCUSDT", market: "spot", timeframe: "1h", data_segment: "holdout", strategy_name: "trend_ema_v1", status: "promoted", reason: "qualified", paper_config_id: 4, telegram_result: null, created_at: "2026-10-28T10:00:00Z", updated_at: null }];
    const { fetchMock, methods } = mockSnapshot(validPolicy, events);

    await expect(fetchPromotionSnapshot()).resolves.toEqual({ policy: validPolicy, events });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(methods).toEqual(["GET", "GET"]);
  });


  it("rejects audit records with invalid status types before the UI renders them", async () => {
    mockSnapshot(validPolicy, [{
      decision_hash: "abc",
      symbol: "BTCUSDT",
      market: "spot",
      timeframe: "1h",
      data_segment: "holdout",
      strategy_name: "trend_ema_v1",
      status: { unexpected: true },
      reason: "qualified",
      paper_config_id: null,
      telegram_result: null,
      created_at: null,
      updated_at: null,
    }]);

    await expect(fetchPromotionSnapshot()).rejects.toThrow("Promotion status returned an invalid response");
  });

  it("rejects incomplete audit records instead of returning a falsely typed snapshot", async () => {
    mockSnapshot(validPolicy, [{ decision_hash: "abc", status: "reconciliation_required" }]);

    await expect(fetchPromotionSnapshot()).rejects.toThrow("Promotion status returned an invalid response");
  });

  it("rejects an enabled policy that omits the Telegram side-effect flag", async () => {
    const { send_telegram: _ignored, ...incompletePolicy } = validPolicy;
    mockSnapshot(incompletePolicy);

    await expect(fetchPromotionSnapshot()).rejects.toThrow("Promotion status returned an invalid response");
  });

  it("rejects policy thresholds with wrong types instead of treating policy as verified", async () => {
    mockSnapshot({ ...validPolicy, min_profit_factor: "1.2" });

    await expect(fetchPromotionSnapshot()).rejects.toThrow("Promotion status returned an invalid response");
  });

  it("rejects non-finite policy thresholds", async () => {
    mockSnapshot({ ...validPolicy, max_drawdown_r: null });

    await expect(fetchPromotionSnapshot()).rejects.toThrow("Promotion status returned an invalid response");
  });
});


const executionInput: BacktestExecutionInput = {
  symbol: "BTCUSDT",
  market: "spot",
  timeframe: "1h",
  strategy: "trend_ema_v1",
  data_segment: "holdout",
  initial_capital: 10000,
  risk_per_trade_pct: 1,
  save_to_db: true,
  params: {},
};

function mockExecutionResult(payload: unknown) {
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).toBe("POST");
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("executeBacktest response contract validation", () => {
  const validResult = {
    runId: 42,
    symbol: "BTCUSDT",
    market: "spot",
    timeframe: "1h",
    strategy: "trend_ema_v1",
    dataSegment: "holdout",
    totalCandles: 1000,
    metrics: { total_trades: 12, win_rate_pct: 58.3, expectancy_r: 0.2, profit_factor: 1.4, max_drawdown_r: 2.1 },
    trades: [],
  };

  it("accepts a well-formed persisted execution result", async () => {
    mockExecutionResult(validResult);
    await expect(executeBacktest(executionInput)).resolves.toEqual(validResult);
  });

  it("rejects a fractional run ID instead of treating it as a saved run", async () => {
    mockExecutionResult({ ...validResult, runId: 42.5 });
    await expect(executeBacktest(executionInput)).rejects.toMatchObject({ name: "BacktestExecutionError", message: "Backtest returned an invalid result payload" });
  });

  it("rejects incorrectly typed metrics instead of rendering invalid values", async () => {
    mockExecutionResult({ ...validResult, metrics: { ...validResult.metrics, profit_factor: "1.4" } });
    await expect(executeBacktest(executionInput)).rejects.toThrow("Backtest returned an invalid result payload");
  });

  it("rejects missing required execution metadata", async () => {
    const { totalCandles: _ignored, ...incompleteResult } = validResult;
    mockExecutionResult(incompleteResult);
    await expect(executeBacktest(executionInput)).rejects.toThrow("Backtest returned an invalid result payload");
  });
});


const validRun = { id: 42, symbol: "BTCUSDT", market: "spot", timeframe: "1h", strategy: "trend_ema_v1", dataSegment: "holdout", totalTrades: 2, winRatePct: 50, expectancyR: 0.25, profitFactor: 1.5, maxDrawdownR: 1.2, createdAt: "2026-10-26T10:00:00Z" };
const validTrade = { direction: "LONG", entryTime: "2026-10-26T09:00:00Z", entryTimeWib: "2026-10-26 16:00:00", entryPrice: 100, exitTime: "2026-10-26T10:00:00Z", exitTimeWib: "2026-10-26 17:00:00", exitPrice: 102, exitReason: "take_profit", stopLoss: 98, takeProfit: 102, netReturnPct: 1.8, rMultiple: 1, holdingBars: 1 };

function mockGet(payload: unknown) {
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET");
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("backtest history response contract validation", () => {
  it("accepts valid persisted run summaries and nullable metrics", async () => {
    mockGet({ runs: [validRun, { ...validRun, id: 43, winRatePct: null, expectancyR: null, profitFactor: null, maxDrawdownR: null }] });
    await expect(fetchBacktestHistory()).resolves.toEqual({ runs: [validRun, { ...validRun, id: 43, winRatePct: null, expectancyR: null, profitFactor: null, maxDrawdownR: null }] });
  });

  it("rejects malformed run summaries instead of passing invalid values to the UI", async () => {
    mockGet({ runs: [{ ...validRun, id: 42.5, totalTrades: "2" }] });
    await expect(fetchBacktestHistory()).rejects.toThrow("Backtest history returned an invalid response");
  });

  it("rejects a non-array history envelope", async () => {
    mockGet({ runs: { id: 42 } });
    await expect(fetchBacktestHistory()).rejects.toThrow("Backtest history returned an invalid response");
  });
});

describe("backtest run detail response contract validation", () => {
  it("accepts valid run metadata, parameters, and closed trades", async () => {
    const payload = { run: { ...validRun, params: { TREND_EMA_FAST: 20 } }, trades: [validTrade] };
    mockGet(payload);
    await expect(fetchBacktestRunDetails(42)).resolves.toEqual(payload);
  });

  it("rejects malformed trade numeric fields", async () => {
    mockGet({ run: { ...validRun, params: {} }, trades: [{ ...validTrade, entryPrice: "100" }] });
    await expect(fetchBacktestRunDetails(42)).rejects.toThrow("Backtest run details returned an invalid response");
  });

  it("rejects missing run metadata and invalid parameter containers", async () => {
    mockGet({ run: { id: 42, params: [] }, trades: [] });
    await expect(fetchBacktestRunDetails(42)).rejects.toThrow("Backtest run details returned an invalid response");
  });

  it("rejects invalid trade arrays", async () => {
    mockGet({ run: { ...validRun, params: null }, trades: { items: [] } });
    await expect(fetchBacktestRunDetails(42)).rejects.toThrow("Backtest run details returned an invalid response");
  });
});
