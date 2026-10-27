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
  it("clearly labels the preview as API-connected and keeps the legacy dashboard unchanged", () => {
    render(<App />);
    expect(screen.getByText("Read-only API preview · legacy dashboard unchanged")).toBeInTheDocument();
    expect(screen.getByText(/Overview and Market Screener V2 are connected to existing APIs/i)).toBeInTheDocument();
  });
  it("disables placeholder actions that are not implemented in the preview", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: "Search (preview only)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Notifications (preview only)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Help (preview only)" })).toBeDisabled();
  });
  it("reports the current roadmap phase consistently with the active domain-view phase", () => {
    render(<App />);
    expect(screen.getByText("04 / 05")).toBeInTheDocument();
    expect(screen.getByText("Domain views and final parity")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });
  it("provides a skip link and labelled primary navigation", () => {
    render(<App />);
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
  it("restores the saved theme on the next render", () => {
    window.localStorage.setItem("signal-bot-ui-theme", "light");
    render(<App />);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
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
    const closeButton = document.querySelector<HTMLButtonElement>(".sidebar-close");
    expect(closeButton).not.toBeNull();
    expect(closeButton).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(sidebar).not.toHaveClass("sidebar-open");
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    expect(menuButton).toHaveFocus();
    expect(document.querySelector(".mobile-scrim")).not.toBeInTheDocument();
    fireEvent.click(menuButton);
    fireEvent.click(closeButton!);
    expect(menuButton).toHaveFocus();
  });
  it("closes mobile navigation after selecting a view and restores focus to the menu button", () => {
    render(<App />);
    const menuButton = screen.getByRole("button", { name: "Open navigation" });
    fireEvent.click(menuButton);
    expect(menuButton).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: /Wallet Tracking/ }));

    expect(screen.getByRole("heading", { level: 1, name: "Wallet Tracking" })).toBeInTheDocument();
    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    expect(menuButton).toHaveFocus();
    expect(document.querySelector(".mobile-scrim")).not.toBeInTheDocument();
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
