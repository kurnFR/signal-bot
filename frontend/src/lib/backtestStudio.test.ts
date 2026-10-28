import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchPromotionSnapshot, type PromotionPolicy } from "./backtestStudio";

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
