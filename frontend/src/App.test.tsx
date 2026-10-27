import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";

const statusResponse = {
  database: { status: "ok", tables: { signals: 42 }, error: null },
  heartbeats: [{ collector: "market-data", status: "ok", lastBeat: "2026-10-26 12:00:00" }],
  binance: { spotApi: "online", futuresApi: "online", enableFuturesConfig: true },
};

describe("frontend foundation shell", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(statusResponse), { status: 200, headers: { "content-type": "application/json" } })));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("provides a skip link and labelled primary navigation", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
  it("switches and persists the selected theme", () => {
    render(<App />);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("signal-bot-ui-theme")).toBe("light");
  });
  it("exposes mobile navigation state and closes it when Escape is pressed", () => {
    render(<App />);
    const sidebar = screen.getByLabelText("Dashboard sidebar");
    const menuButton = screen.getByRole("button", { name: "Open navigation" });
    expect(menuButton).toHaveAttribute("aria-controls", "dashboard-sidebar");
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(menuButton);
    expect(sidebar).toHaveClass("sidebar-open");
    expect(menuButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Close navigation" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(sidebar).not.toHaveClass("sidebar-open");
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    expect(menuButton).toHaveFocus();
    expect(document.querySelector(".mobile-scrim")).not.toBeInTheDocument();
    fireEvent.click(menuButton);
    fireEvent.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(menuButton).toHaveFocus();
  });
  it("changes the active navigation item and keeps non-Overview views disconnected", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Market Screener/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Market Screener" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Market Screener/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/mutating strategy, wallet tracking, trading, worker, deployment, and Telegram actions remain deliberately unavailable/i)).toBeInTheDocument();
  });
  it("routes Backtest Studio to saved results without enabling execution", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Backtest Studio/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Backtest Studio" })).toBeInTheDocument();
    expect(screen.getByText(/Backtest Studio displays saved results and closed-trade evidence/i)).toBeInTheDocument();
  });
  it("routes ML Studio to persisted research without enabling deployment", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /ML Studio/ }));
    expect(screen.getByRole("heading", { level: 1, name: "ML Studio" })).toBeInTheDocument();
    expect(screen.getByText(/ML Studio displays persisted experiments and model eligibility/i)).toBeInTheDocument();
  });
  it("routes Wallet Tracking to the read-only view", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Wallet Tracking/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Wallet Tracking" })).toBeInTheDocument();
    expect(screen.getByText(/Wallet Tracking displays tracker health, monitored addresses, token flow, and recent events/i)).toBeInTheDocument();
  });
  it("routes Research to the read-only positions and lifecycle monitor", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Research/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Research" })).toBeInTheDocument();
    expect(screen.getByText(/Review RetailBot2 positions, closed trades, strategy lifecycle flags/i)).toBeInTheDocument();
  });
  it("routes News AI to the read-only sentiment and events view", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /News AI/ }));
    expect(screen.getByRole("heading", { level: 1, name: "News AI" })).toBeInTheDocument();
    expect(screen.getByText(/News AI displays persisted sentiment metrics, signals, and macro events/i)).toBeInTheDocument();
  });
  it("routes Master Data to read-only coverage and job history", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Master Data/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Master Data" })).toBeInTheDocument();
    expect(screen.getByText(/Master Data displays stored candle coverage, feature availability, and recent job status/i)).toBeInTheDocument();
  });
  it("routes Settings to safe workspace preferences without server mutations", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current session" })).toBeInTheDocument();
    expect(screen.getByText(/System connection details, user management, credential settings/i)).toBeInTheDocument();
  });
});
