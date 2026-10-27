import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./SettingsPanel";

describe("SettingsPanel", () => {
  it("shows safe session metadata and toggles only local appearance preference", () => {
    const onToggleTheme = vi.fn();
    render(<SettingsPanel theme="dark" onToggleTheme={onToggleTheme} username="tester" role="viewer" />);
    expect(screen.getByText("tester")).toBeInTheDocument();
    expect(screen.getByText("viewer")).toBeInTheDocument();
    expect(screen.getByText(/API credentials, and Telegram controls are not exposed/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it("uses safe fallback labels when no authenticated user is present", () => {
    render(<SettingsPanel theme="light" onToggleTheme={() => undefined} />);
    expect(screen.getByText("Preview workspace")).toBeInTheDocument();
    expect(screen.getByText("Unauthenticated preview")).toBeInTheDocument();
  });
});
