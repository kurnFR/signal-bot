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
    window.history.replaceState(null, "", "/");
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(statusResponse), { status: 200, headers: { "content-type": "application/json" } })));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("restores the requested view on direct entry and browser navigation", () => {
    window.history.replaceState(null, "", "/modern/paper-trading");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Paper Trading", level: 1 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Overview/ }));
    expect(window.location.pathname).toBe("/modern/overview");
    window.history.replaceState(null, "", "/modern/paper-trading");
    fireEvent.popState(window);
    expect(screen.getByRole("heading", { name: "Paper Trading", level: 1 })).toBeInTheDocument();
  });
  it("renders the quantitative platform header and footer", async () => {
    render(<App />);
    expect(screen.getByText("Signal Bot Quantitative Platform")).toBeInTheDocument();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByLabelText("Live clock (UTC+7)")).toHaveTextContent(/^\d{2}:\d{2}:\d{2} WIB$/);
    expect(screen.getByText(/v2\.0\.0 .*MariaDB/i)).toBeInTheDocument();
  });
  it("transitions the live status indicator to Disconnected when system status check fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network connection lost")));
    render(<App />);
    expect(await screen.findByText("Disconnected")).toBeInTheDocument();
  });
  it("identifies the degraded service and opens the health overview", async () => {
    window.history.replaceState(null, "", "/modern/settings");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(Response.json({
      ...statusResponse,
      heartbeats: [{ collector: "wallet-worker", status: "stale", lastBeat: null }],
    }))));
    render(<App />);
    const badge = await screen.findByRole("button", { name: /open system health overview.*wallet-worker: stale/i });
    expect(badge).toHaveTextContent("Degraded");
    fireEvent.click(badge);
    expect(screen.getByRole("heading", { name: "Overview", level: 1 })).toBeInTheDocument();
  });
  it("renders topbar actions including theme toggle and navigation breadcrumbs", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
  });
  it("displays dynamic view metadata for the active operations workspace", () => {
    render(<App />);
    expect(screen.getByText("SIGNAL BOT / OPERATIONS")).toBeInTheDocument();
    expect(screen.getByText(/Real-time engine status, collector health, and database metrics/i)).toBeInTheDocument();
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
    const closeButton = document.querySelector<HTMLButtonElement>("button.sidebar-close");
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
  it("changes the active navigation item to Market Screener", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Market Screener/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Market Screener" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Market Screener/ })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { name: "Screener configuration" })).toBeInTheDocument();
    expect(screen.getByText(/configuration editing is restricted to admin and trader roles/i)).toBeInTheDocument();
  });
  it("describes Strategy Library management accurately", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Strategy Library/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Strategy Library" })).toBeInTheDocument();
    expect(screen.getByText(/STRATEGIES \/ QUANT CATALOG/i)).toBeInTheDocument();
  });
  it("routes Backtest Studio accurately", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Backtest Studio/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Backtest Studio" })).toBeInTheDocument();
    expect(screen.getByText(/HISTORICAL ANALYSIS \/ QUANT LAB/i)).toBeInTheDocument();
  });
  it("routes ML Studio accurately", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /ML Studio/ }));
    expect(screen.getByRole("heading", { level: 1, name: "ML Studio" })).toBeInTheDocument();
    expect(screen.getByText(/MACHINE LEARNING \/ AI ENGINE/i)).toBeInTheDocument();
  });
  it("routes Wallet Tracking to the active view", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Wallet Tracking/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Wallet Tracking" })).toBeInTheDocument();
    expect(screen.getByText(/ON-CHAIN \/ SMART MONEY/i)).toBeInTheDocument();
  });
  it("hides Research navigation by default while keeping /modern/research route functional and revealing nav when UI_SHOW_RETAILBOT2_HISTORY is enabled", async () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: /^Research$/ })).not.toBeInTheDocument();

    window.history.replaceState(null, "", "/modern/research");
    fireEvent.popState(window);
    expect(await screen.findByRole("heading", { level: 1, name: "Research" })).toBeInTheDocument();

    cleanup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          Response.json({ ...statusResponse, uiFlags: { showRetailbot2History: true } }),
        ),
      ),
    );
    window.history.replaceState(null, "", "/");
    render(<App />);
    const researchNav = await screen.findByRole("button", { name: /^Research$/ });
    fireEvent.click(researchNav);
    expect(screen.getByRole("heading", { level: 1, name: "Research" })).toBeInTheDocument();
    expect(screen.getByText(/RESEARCH \/ BOT LAB/i)).toBeInTheDocument();
  });
  it("routes News AI to the active view", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /News AI/ }));
    expect(screen.getByRole("heading", { level: 1, name: "News AI" })).toBeInTheDocument();
    expect(screen.getByText(/SENTIMENT \/ MACRO AI/i)).toBeInTheDocument();
  });
  it("routes Master Data to the active view", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Master Data/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Master Data" })).toBeInTheDocument();
    expect(screen.getByText(/DATA PIPELINE \/ CANDLES/i)).toBeInTheDocument();
  });
  it("routes Settings to safe workspace preferences without server mutations", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Appearance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current session" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Change My Password" })).toBeInTheDocument();
  });
  it("toggles desktop sidebar collapse from brand and topbar buttons and persists state", () => {
    render(<App />);
    const frame = document.querySelector(".app-frame");
    const sidebar = screen.getByLabelText("Dashboard sidebar");
    const brandToggle = screen.getByRole("button", { name: "Collapse sidebar" });
    const topbarToggle = screen.getByRole("button", { name: "Collapse navigation sidebar" });

    expect(frame).not.toHaveClass("sidebar-is-collapsed");
    expect(sidebar).not.toHaveClass("sidebar-collapsed");
    expect(brandToggle).toHaveAttribute("aria-controls", "dashboard-sidebar");
    expect(brandToggle).toHaveAttribute("aria-expanded", "true");
    expect(topbarToggle).toHaveAttribute("aria-controls", "dashboard-sidebar");
    expect(topbarToggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(brandToggle);
    expect(frame).toHaveClass("sidebar-is-collapsed");
    expect(sidebar).toHaveClass("sidebar-collapsed");
    expect(window.localStorage.getItem("sidebar_collapsed")).toBe("1");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Expand navigation sidebar" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /Overview/ })).toHaveAttribute("title", "Overview");

    fireEvent.click(screen.getByRole("button", { name: "Expand navigation sidebar" }));
    expect(frame).not.toHaveClass("sidebar-is-collapsed");
    expect(sidebar).not.toHaveClass("sidebar-collapsed");
    expect(window.localStorage.getItem("sidebar_collapsed")).toBe("0");
  });
  it("restores collapsed sidebar state from localStorage and supports Cmd/Ctrl+B shortcut", () => {
    window.localStorage.setItem("sidebar_collapsed", "1");
    render(<App />);
    const sidebar = screen.getByLabelText("Dashboard sidebar");
    const mobileMenu = screen.getByRole("button", { name: "Open navigation" });

    expect(sidebar).toHaveClass("sidebar-collapsed");
    expect(mobileMenu).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(sidebar).not.toHaveClass("sidebar-collapsed");
    expect(window.localStorage.getItem("sidebar_collapsed")).toBe("0");

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(sidebar).toHaveClass("sidebar-collapsed");
    expect(window.localStorage.getItem("sidebar_collapsed")).toBe("1");

    // Mobile drawer state remains independent of desktop collapse
    fireEvent.click(mobileMenu);
    expect(mobileMenu).toHaveAttribute("aria-expanded", "true");
    expect(sidebar).toHaveClass("sidebar-open");
  });
});
