import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ScreenerPanel } from "./ScreenerPanel";
import { AuthGate } from "./AuthGate";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("read-only Market Screener V2 panel", () => {
  it("renders signals, filters by symbol, and never calls control endpoints", async () => {
    window.localStorage.setItem("token", "session-token");
    const status = { running: true, database_ok: true, enabled: true, timeframe: "1m", symbols_monitored: 240, last_beat_at: "2026-10-26T12:00:00", last_signal_at: "2026-10-26T12:01:00", last_universe_refresh: null };
    const signals = [{ id: 7, symbol: "BTCUSDT", signal_type: "VOLUME_SPIKE", rvol: 8.2, volume: 1000, quote_volume: 100000, price: 64000, price_change_pct: 1.4, volume_velocity: 4.1, quality_score: 82, candle_time: null, telegram_sent: true, detected_at: "2026-10-26T12:01:00" }, { id: 8, symbol: "ETHUSDT", signal_type: "DIVERGENCE", rvol: 3.2, volume: 500, quote_volume: 50000, price: 2500, price_change_pct: -0.5, volume_velocity: 1.2, quality_score: 61, candle_time: null, telegram_sent: false, detected_at: "2026-10-26T11:59:00" }];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") return Promise.resolve(new Response(JSON.stringify({ id: 4, username: "trader01", email: null, role: "trader", must_change_password: false }), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.endsWith("/screener/status")) return Promise.resolve(new Response(JSON.stringify(status), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.includes("/screener/signals")) return Promise.resolve(new Response(JSON.stringify({ signals }), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.endsWith("/screener/stats")) return Promise.resolve(new Response(JSON.stringify({ total_signals: 25, signals_last_24h: 4, by_type_last_24h: { VOLUME_SPIKE: 3, DIVERGENCE: 1 } }), { status: 200, headers: { "content-type": "application/json" } }));
      throw new Error("Unexpected endpoint: " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><ScreenerPanel /></AuthGate>);
    expect(await screen.findByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Price$/ }));
    expect(screen.getByRole("columnheader", { name: "Price" })).toHaveAttribute("aria-sort", "ascending");
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter signals" }), { target: { value: "btc" } });
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.queryByText("ETHUSDT")).not.toBeInTheDocument();
    const requestedPaths = fetchMock.mock.calls.map(([path]) => String(path));
    expect(screen.getByText(/This migration preview is read-only/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save changes/i })).not.toBeInTheDocument();
    expect(requestedPaths.some((path) => path.includes("/control"))).toBe(false);
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === "GET" || options?.method === undefined)).toBe(true);
  });
  it("shows an authentication message for an expired session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401, headers: { "content-type": "application/json" } })));
    render(<ScreenerPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in through the existing dashboard/i);
  });

  it("shows an explicit empty state when the API successfully returns no signals", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/screener/status")) return Promise.resolve(new Response(JSON.stringify({
        running: false, database_ok: true, enabled: false, timeframe: "1m", symbols_monitored: 0,
        last_beat_at: null, last_signal_at: null, last_universe_refresh: null,
      }), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.includes("/screener/signals")) return Promise.resolve(new Response(JSON.stringify({ signals: [] }), { status: 200, headers: { "content-type": "application/json" } }));
      if (path.endsWith("/screener/stats")) return Promise.resolve(new Response(JSON.stringify({
        total_signals: 0, signals_last_24h: 0, by_type_last_24h: {},
      }), { status: 200, headers: { "content-type": "application/json" } }));
      throw new Error("Unexpected endpoint: " + path);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ScreenerPanel />);

    expect(await screen.findByText("No recent smart-money signals were returned.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a recoverable API error and reloads the snapshot after Retry", async () => {
    const successfulPayloads: Record<string, unknown> = {
      "/api/research/screener/status": {
        running: true, database_ok: true, enabled: true, timeframe: "1m", symbols_monitored: 12,
        last_beat_at: null, last_signal_at: null, last_universe_refresh: null,
      },
      "/api/research/screener/signals?limit=50": { signals: [{
        id: 11, symbol: "SOLUSDT", signal_type: "VOLUME_SPIKE", rvol: 2.4, volume: 10,
        quote_volume: 5000, price: 150, price_change_pct: 0.4, volume_velocity: 1.8,
        quality_score: 75, candle_time: null, telegram_sent: false, detected_at: "2026-10-28T10:00:00",
      }] },
      "/api/research/screener/stats": { total_signals: 1, signals_last_24h: 1, by_type_last_24h: { VOLUME_SPIKE: 1 } },
    };
    let recovered = false;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const path = String(input);
      if (!recovered) return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), {
        status: 503, headers: { "content-type": "application/json" },
      }));
      const payload = successfulPayloads[path];
      if (payload === undefined) throw new Error("Unexpected endpoint: " + path);
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: 200, headers: { "content-type": "application/json" },
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ScreenerPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check the API and database status, then retry/i);
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();

    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));

    expect(await screen.findByText("SOLUSDT")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(6);
  });
});
