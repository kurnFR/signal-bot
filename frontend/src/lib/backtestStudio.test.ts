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
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const payload = path.includes("promotion-policy") ? { policy } : { events };
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchPromotionSnapshot contract validation", () => {
  it("returns the complete verified policy and audit events", async () => {
    const events = [{ decision_hash: "abc", status: "promoted" }];
    const fetchMock = mockSnapshot(validPolicy, events);

    await expect(fetchPromotionSnapshot()).resolves.toEqual({ policy: validPolicy, events });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
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
