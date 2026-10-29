import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createCustomStrategy,
  fetchCustomStrategies,
  fetchStrategyIndicators,
  generateAIStrategyDraft,
  quickTestCustomStrategy,
  StrategyLibraryActionError,
  StrategyQuickTestError,
  type CreateCustomStrategyInput,
} from "./strategyLibrary";

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}
function mockFetch(payload: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload, status)));
}
const strategy = {
  name: "rsi_volume_v1",
  displayName: "RSI + Volume",
  description: "Mean reversion",
  rationale: "Confirm with volume",
  longConditions: [{ indicator: "rsi", operator: "<", value: 30 }],
  shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }],
  slAtrMult: 1.5,
  riskRewardRatio: 2.5,
  useTrailingStop: true,
};
const createInput: CreateCustomStrategyInput = {
  name: "rsi_volume_v1",
  displayName: "RSI + Volume",
  description: "Mean reversion",
  rationale: "Confirm with volume",
  longConditions: [{ indicator: "rsi", operator: "<", value: 30 }],
  shortConditions: [{ indicator: "rsi", operator: ">", value: 70 }],
  slAtrMult: 1.5,
  riskRewardRatio: 2.5,
  useTrailingStop: true,
  trailActivationR: 1,
  trailDistanceAtrMult: 1.5,
};

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("Strategy Library API response contracts", () => {
  it("accepts a valid saved strategy and indicator catalog", async () => {
    mockFetch({ strategies: [strategy] });
    await expect(fetchCustomStrategies()).resolves.toEqual({ strategies: [strategy] });
    mockFetch({ indicators: [{ name: "rsi", label: "RSI", default: 30 }], operators: ["<", ">"] });
    await expect(fetchStrategyIndicators()).resolves.toMatchObject({ operators: ["<", ">"] });
  });

  it("rejects malformed saved strategy rows", async () => {
    mockFetch({ strategies: [{ ...strategy, longConditions: "invalid" }] });
    await expect(fetchCustomStrategies()).rejects.toThrow(/invalid response/i);
  });

  it("rejects malformed indicator metadata", async () => {
    mockFetch({ indicators: [{ name: "rsi", default: Number.NaN }], operators: ["<"] });
    await expect(fetchStrategyIndicators()).rejects.toThrow(/invalid response/i);
  });

  it("rejects a quick-test result with invalid count fields", async () => {
    mockFetch({
      status: "completed", symbol: "BTCUSDT", market: "spot", timeframe: "1h",
      totalCandles: 12.5, totalTrades: 4, winRate: 0.5, winRatePct: 50,
      netR: 1, profitFactor: 1.2, maxDrawdownR: 0.5, expectancyR: 0.25,
    });
    await expect(quickTestCustomStrategy(strategy, { symbol: "BTCUSDT", market: "spot", timeframe: "1h" }))
      .rejects.toBeInstanceOf(StrategyQuickTestError);
  });

  it("rejects an AI draft whose nested strategy contract is malformed", async () => {
    mockFetch({ status: "ok", provider: "test", strategy: { ...strategy, shortConditions: "invalid" }, chatResponse: "Draft ready" });
    await expect(generateAIStrategyDraft("build an RSI strategy")).rejects.toBeInstanceOf(StrategyLibraryActionError);
  });

  it("rejects mutation responses with malformed optional fields", async () => {
    mockFetch({ status: 42, message: "Saved" });
    await expect(createCustomStrategy(createInput)).rejects.toBeInstanceOf(StrategyLibraryActionError);
  });
});
