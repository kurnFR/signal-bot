import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ServicesSection } from "./ServicesSection";

const fetchServicesMock = vi.fn();
const setServiceEnabledMock = vi.fn();

vi.mock("../lib/services", () => ({
  fetchServices: (...a: unknown[]) => fetchServicesMock(...a),
  setServiceEnabled: (...a: unknown[]) => setServiceEnabledMock(...a),
}));

const row = (over = {}) => ({
  service: "paper_engine", label: "Paper Trading Engine", description: "d",
  pauseWarning: "Open positions are NOT monitored while paused.",
  enabled: true, updatedBy: null, updatedAt: null,
  heartbeats: [{ name: "paper_engine", status: "ok", detail: null, lastBeat: "2026-11-25 10:00:00" }],
  ...over,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchServicesMock.mockReset();
  setServiceEnabledMock.mockReset();
});

describe("ServicesSection", () => {
  it("requires explicit confirmation (with risk warning) before pausing", async () => {
    fetchServicesMock.mockResolvedValue([row()]);
    setServiceEnabledMock.mockResolvedValue(undefined);
    render(<ServicesSection isAdmin />);
    fireEvent.click(await screen.findByRole("button", { name: /Pause Paper Trading Engine/ }));
    expect(setServiceEnabledMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/NOT monitored/);
    fireEvent.click(screen.getByRole("button", { name: /Confirm pause/ }));
    await waitFor(() => expect(setServiceEnabledMock).toHaveBeenCalledWith("paper_engine", false));
  });

  it("resumes a paused service without a confirmation step", async () => {
    fetchServicesMock.mockResolvedValue([row({ enabled: false })]);
    setServiceEnabledMock.mockResolvedValue(undefined);
    render(<ServicesSection isAdmin />);
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Resume Paper Trading Engine/ }));
    await waitFor(() => expect(setServiceEnabledMock).toHaveBeenCalledWith("paper_engine", true));
  });

  it("shows launch guidance when the optional worker has no heartbeat", async () => {
    fetchServicesMock.mockResolvedValue([
      row({ enabled: false, heartbeats: [] }),
      row({ service: "paper_report", label: "Paper Performance Reports", heartbeats: [] }),
    ]);
    render(<ServicesSection isAdmin />);
    expect(await screen.findByText("PAPER_ENGINE_ENABLED=1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume Paper Trading Engine" })).toBeInTheDocument();
    expect(screen.getByText("PAPER_REPORT_ENABLED=1 (requires the Telegram outbox worker)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause Paper Performance Reports" })).toBeInTheDocument();
  });

  it("shows a stale heartbeat for an enabled service past its threshold", async () => {
    fetchServicesMock.mockResolvedValue([row({
      staleAfterSeconds: 180,
      heartbeats: [{ lastBeat: new Date(Date.now() - 181_000).toISOString() }],
    })]);
    render(<ServicesSection isAdmin />);
    expect(await screen.findByText("Stale heartbeat")).toBeInTheDocument();
  });

  it("resumes the Screener without overwriting its threshold overrides", async () => {
    fetchServicesMock.mockResolvedValue([]);
    const overrides = { min_rvol: 2.5 };
    const calls: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      const path = new URL(input, "http://localhost").pathname;
      const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
      if (path.endsWith("/status")) return json({
        running: false, database_ok: true, enabled: false, timeframe: "1m",
        symbols_monitored: 0, last_beat_at: null, last_signal_at: null, last_universe_refresh: null,
      });
      if (path.endsWith("/control") && init?.method === "POST") {
        calls.push(JSON.parse(String(init.body)));
        return json({ status: "ok", note: "Saved" });
      }
      return json({ enabled: false, overrides, updated_at: null, tunable_fields: Object.keys(overrides) });
    }));
    render(<ServicesSection isAdmin />);
    fireEvent.click(await screen.findByRole("button", { name: "Resume Screener" }));
    await waitFor(() => expect(calls).toEqual([{ enabled: true, overrides }]));
    expect(setServiceEnabledMock).not.toHaveBeenCalled();
  });

  it("does not call the admin API for non-admins", async () => {
    render(<ServicesSection isAdmin={false} />);
    await waitFor(() => expect(screen.queryByText("Loading services…")).not.toBeInTheDocument());
    expect(fetchServicesMock).not.toHaveBeenCalled();
  });

  it("surfaces load errors", async () => {
    fetchServicesMock.mockRejectedValue(new Error("boom"));
    render(<ServicesSection isAdmin />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
