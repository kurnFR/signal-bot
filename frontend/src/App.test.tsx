import { beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";

describe("frontend foundation shell", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });
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
  it("changes the active preview navigation item without calling the backend", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Market Screener/ }));
    expect(screen.getByRole("heading", { level: 1, name: "Market Screener" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Market Screener/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/does not connect to trading APIs/i)).toBeInTheDocument();
  });
});