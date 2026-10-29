import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchScreenerControl, fetchScreenerSnapshot, saveScreenerControl, ScreenerControlError } from "./screener";

describe("Screener control session handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.removeItem("token");
  });

  it("clears an expired token and announces session expiry when saving is unauthorized", async () => {
    window.localStorage.setItem("token", "expired-session-token");
    const listener = vi.fn();
    window.addEventListener("signal-bot:session-expired", listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not authenticated" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    ));

    await expect(saveScreenerControl(false, {})).rejects.toMatchObject({ status: 401 });
    expect(window.localStorage.getItem("token")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener("signal-bot:session-expired", listener);
  });
});

describe("Screener snapshot response contracts", () => {
  afterEach(() => vi.unstubAllGlobals());

  const status = {
    running: true, database_ok: true, enabled: true, timeframe: "1m",
    symbols_monitored: 240, last_beat_at: "2026-10-26T12:00:00Z",
    last_signal_at: "2026-10-26T12:01:00Z", last_universe_refresh: null,
  };
  const signal = {
    id: 7, symbol: "BTCUSDT", signal_type: "VOLUME_SPIKE", rvol: 8.2,
    volume: 1000, quote_volume: 100000, price: 64000, price_change_pct: 1.4,
    volume_velocity: 4.1, quality_score: 82, candle_time: null,
    telegram_sent: true, detected_at: "2026-10-26T12:01:00Z",
  };
  function installSnapshot(overrides: Record<string, unknown> = {}) {
    const payloads: Record<string, unknown> = {
      "/api/research/screener/status": status,
      "/api/research/screener/signals?limit=50": { signals: [signal] },
      "/api/research/screener/stats": { total_signals: 25, signals_last_24h: 4, by_type_last_24h: { VOLUME_SPIKE: 3, DIVERGENCE: 1 } },
      ...overrides,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const body = payloads[String(input)];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    }));
  }

  it("accepts backend-shaped status, signal rows, and stats", async () => {
    installSnapshot();
    const result = await fetchScreenerSnapshot();
    expect(result.signals[0].quote_volume).toBe(100000);
    expect(result.stats.signals_last_24h).toBe(4);
  });

  it("rejects malformed signal numeric fields", async () => {
    installSnapshot({ "/api/research/screener/signals?limit=50": { signals: [{ ...signal, quality_score: "82" }] } });
    await expect(fetchScreenerSnapshot()).rejects.toThrow(/signals returned an invalid response/i);
  });

  it("rejects malformed stats counts instead of rendering strings", async () => {
    installSnapshot({ "/api/research/screener/stats": { total_signals: 25, signals_last_24h: "4", by_type_last_24h: {} } });
    await expect(fetchScreenerSnapshot()).rejects.toThrow(/stats returned an invalid response/i);
  });
});

describe("Screener control response contracts", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects non-finite or non-numeric saved overrides", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      enabled: true, overrides: { rvol_multiplier: "8" }, updated_at: null,
      tunable_fields: ["rvol_multiplier"],
    }), { status: 200, headers: { "content-type": "application/json" } })));
    await expect(fetchScreenerControl()).rejects.toThrow(/control returned an invalid response/i);
  });

  it("rejects persisted override keys that are not declared tunable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      enabled: true, overrides: { unknown_override: 8 }, updated_at: null,
      tunable_fields: ["rvol_multiplier"],
    }), { status: 200, headers: { "content-type": "application/json" } })));
    await expect(fetchScreenerControl()).rejects.toThrow(/control returned an invalid response/i);
  });

  it("rejects a successful-looking save response without its note contract", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), {
      status: 200, headers: { "content-type": "application/json" },
    })));
    await expect(saveScreenerControl(true, {})).rejects.toBeInstanceOf(ScreenerControlError);
  });
});
