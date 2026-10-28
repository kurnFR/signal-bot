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
});
