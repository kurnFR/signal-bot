import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchNewsSnapshot } from "./news";

const stats = {
  total_events_collected: 120, events_evaluated_by_ai: 90, high_confidence_signals: 12,
  avg_confidence: 78.5, trades_opened: 4, pending_signals: 2,
  performance: { closed_trades: 8, wins: 5, losses: 3, win_rate: 62.5, expectancy_r: 0.4, profit_factor: 1.6, total_r: 3.2, net_pnl: 120.5, max_drawdown_r: 1.1 },
  heartbeats: [{ collector_name: "news_poller", status: "running", detail: null, last_beat_time: "2026-10-26T10:00:00Z" }],
};
const signal = {
  id: 1, symbol: "BTCUSDT", market: "spot", timeframe: "1h", bias: "LONG",
  confidence: 82, reasoning: "positive", invalidation_condition: null, time_horizon: "4h",
  trade_timing: "immediate", acted_on: true, paper_position_id: 10, skip_reason: null,
  created_at: "2026-10-26T10:00:00Z", headline: "Macro update", url: "https://example.test/news",
  source: "test", category: "macro", country: "US", impact: "high", scheduled_at: null,
  actual_value: "3.1", forecast_value: 3, previous_value: null,
};
const event = {
  id: 2, source: "test", category: "macro", headline: "Macro update",
  url: "https://example.test/news", symbols: "BTCUSDT", country: "US", impact: "high",
  published_at: "2026-10-26T09:00:00Z", scheduled_at: null, actual_value: "3.1",
  forecast_value: 3, previous_value: null, processed_at: "2026-10-26T09:05:00Z",
  fetched_at: "2026-10-26T09:00:00Z",
};
function installFetch(overrides: Record<string, unknown> = {}) {
  const payloads: Record<string, unknown> = {
    "/api/news/stats": stats,
    "/api/news/signals?limit=50": { signals: [signal] },
    "/api/news/events?limit=30": { events: [event] },
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") throw new Error("News snapshot must use GET");
    return Promise.resolve(new Response(JSON.stringify(payloads[String(input)]), {
      status: 200, headers: { "content-type": "application/json" },
    }));
  }));
}
beforeEach(() => {
  window.localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("News AI response contracts", () => {
  it("accepts valid stats, heartbeat, signal, and event payloads", async () => {
    const result = await fetchNewsSnapshot();
    expect(result.stats.performance.net_pnl).toBe(120.5);
    expect(result.signals[0].confidence).toBe(82);
    expect(result.events[0].headline).toBe("Macro update");
  });

  it("accepts valid empty signal and event collections", async () => {
    installFetch({
      "/api/news/signals?limit=50": { signals: [] },
      "/api/news/events?limit=30": { events: [] },
    });
    const result = await fetchNewsSnapshot();
    expect(result.signals).toEqual([]);
    expect(result.events).toEqual([]);
  });

  it("rejects malformed performance metrics", async () => {
    installFetch({ "/api/news/stats": { ...stats, performance: { ...stats.performance, net_pnl: "120.5" } } });
    await expect(fetchNewsSnapshot()).rejects.toThrow(/stats returned an invalid response/i);
  });

  it("rejects malformed signal rows", async () => {
    installFetch({ "/api/news/signals?limit=50": { signals: [{ ...signal, acted_on: "true" }] } });
    await expect(fetchNewsSnapshot()).rejects.toThrow(/signals returned an invalid response/i);
  });

  it("rejects malformed event IDs and numeric values", async () => {
    installFetch({ "/api/news/events?limit=30": { events: [{ ...event, id: "2", actual_value: {} }] } });
    await expect(fetchNewsSnapshot()).rejects.toThrow(/events returned an invalid response/i);
  });
});
