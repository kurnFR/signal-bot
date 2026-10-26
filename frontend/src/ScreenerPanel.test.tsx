import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ScreenerPanel } from "./ScreenerPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("read-only Market Screener V2 panel", () => {
  it("renders signals, filters by symbol, and never calls control endpoints", async () => {
    window.localStorage.setItem("token", "session-token");
    const status = { running: true, database_ok: true, enabled: true, timeframe: "1m", symbols_monitored: 240, last_beat_at: "2026-10-26T12:00:00", last_signal_at: "2026-10-26T12:01:00", last_universe_refresh: null };
    const signals = [{ id: 7, symbol: "BTCUSDT", signal_type: "VOLUME_SPIKE", rvol: 8.2, volume: 1000, quote_volume: 100000, price: 64000, price_change_pct: 1.4, volume_velocity: 4.1, quality_score: 82, candle_time: null, telegram_sent: true, detected_at: "2026-10-26T12:01:00" }, { id: 8, symbol: "ETHUSDT", signal_type: "DIVERGENCE", rvol: 3.2, volume: 500, quote_volume: 50000, price: 2500, price_change_pct: -0.5, volume_velocity: 1.2, quality_score: 61, candle_time: null, telegram_sent: false, detected_at: "2026-10-26T11:59:00" }];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/screener/status")) return Promise.resolve(new Response(JSON.stringify(status), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.includes("/screener/signals")) return Promise.resolve(new Response(JSON.stringify({ signals }), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.endsWith("/screener/stats")) return Promise.resolve(new Response(JSON.stringify({ total_signals: 25, signals_last_24h: 4, by_type_last_24h: { VOLUME_SPIKE: 3, DIVERGENCE: 1 } }), { status: 200, headers: { "content-type": "application/json" } }));
      throw new Error("Unexpected endpoint: " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ScreenerPanel />);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter signals" }), { target: { value: "btc" } });
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.queryByText("ETHUSDT")).not.toBeInTheDocument();
    const requestedPaths = fetchMock.mock.calls.map(([path]) => String(path));
    expect(requestedPaths.some((path) => path.includes("/control"))).toBe(false);
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === "GET")).toBe(true);
  });
  it("shows an authentication message for an expired session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401, headers: { "content-type": "application/json" } })));
    render(<ScreenerPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in through the existing dashboard/i);
  });
});
