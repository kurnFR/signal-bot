import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SystemStatusPanel } from "./SystemStatusPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("read-only Overview status panel", () => {
  it("renders sanitized system status and heartbeat metadata", async () => {
    window.localStorage.setItem("token", "session-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      database: { status: "ok", tables: { signals: 10, trades: 3 }, error: null },
      heartbeats: [{ collector: "market-data", status: "ok", detail: "not rendered", lastBeat: "2026-10-26 12:00:00" }],
      binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: true },
    }), { status: 200, headers: { "content-type": "application/json" } })));
    render(<SystemStatusPanel />);
    expect(await screen.findByText("market-data")).toBeInTheDocument();
    expect(screen.getByText("2 tables")).toBeInTheDocument();
    expect(screen.getAllByText("online").length).toBeGreaterThan(0);
    expect(screen.queryByText("not rendered")).not.toBeInTheDocument();
  });
  it("renders a missing heartbeat timestamp as unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      database: { status: "ok", tables: {}, error: null },
      heartbeats: [{ collector: "market-data", status: "ok", lastBeat: null }],
      binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
    }), { status: 200, headers: { "content-type": "application/json" } })));
    render(<SystemStatusPanel />);
    expect(await screen.findByText("market-data")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });
  it("shows a recoverable authentication error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401, headers: { "content-type": "application/json" } })));
    render(<SystemStatusPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/session has expired\. sign in again/i);
  });

  it("renders a successful empty heartbeat response without an API error", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      database: { status: "ok", tables: {}, error: null },
      heartbeats: [],
      binance: { spotApi: "online", futuresApi: "offline", enableFuturesConfig: false },
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPanel />);

    expect(await screen.findByText("No collector heartbeat records were returned.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("recovers from a temporary status API failure after Retry", async () => {
    let recovered = false;
    const payload = {
      database: { status: "ok", tables: { signals: 10 }, error: null },
      heartbeats: [{ collector: "market-data", status: "ok", lastBeat: "2026-10-26 12:00:00" }],
      binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
    };
    const fetchMock = vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      expect(!init?.method || init.method === "GET").toBe(true);
      if (!recovered) return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), { status: 503, headers: { "content-type": "application/json" } }));
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemStatusPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status could not be loaded.*retry/i);
    recovered = true;
    render; // retry remains an explicit user action
    screen.getByRole("button", { name: /Retry/i }).click();
    expect(await screen.findByText("market-data")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

});
