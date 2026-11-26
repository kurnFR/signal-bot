import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewsAISettingsCard } from "./NewsAISettingsCard";

const m = { fetchNewsAISettings: vi.fn(), updateNewsAISettings: vi.fn(), resetNewsAISettings: vi.fn() };
vi.mock("../lib/news", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/news")>();
  return {
    ...actual,
    fetchNewsAISettings: (...a: unknown[]) => m.fetchNewsAISettings(...a),
    updateNewsAISettings: (...a: unknown[]) => m.updateNewsAISettings(...a),
    resetNewsAISettings: (...a: unknown[]) => m.resetNewsAISettings(...a),
  };
});

const settings = (over = {}) => ({
  min_confidence: 75, max_trades_per_day: 3, source: "default", defaults: { min_confidence: 75, max_trades_per_day: 3 },
  updated_by: null, updated_at: null, ...over,
});

afterEach(() => { cleanup(); Object.values(m).forEach((f) => f.mockReset()); });

describe("NewsAISettingsCard", () => {
  it("renders nothing and never calls the admin API for non-admins", () => {
    const { container } = render(<NewsAISettingsCard isAdmin={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(m.fetchNewsAISettings).not.toHaveBeenCalled();
  });

  it("validates ranges client-side before saving", async () => {
    m.fetchNewsAISettings.mockResolvedValue(settings());
    render(<NewsAISettingsCard isAdmin />);
    const field = await screen.findByLabelText(/Minimum confidence/);
    await waitFor(() => expect(field).toHaveValue("75"));
    fireEvent.change(field, { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save guardrails" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/from 50 to 100/);
    expect(m.updateNewsAISettings).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "80" } });
    fireEvent.change(screen.getByLabelText(/Max trades per day/), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save guardrails" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/from 0 to 20/);
  });

  it("saves valid values and allows reset only when an override exists", async () => {
    m.fetchNewsAISettings.mockResolvedValue(settings());
    m.updateNewsAISettings.mockResolvedValue(undefined);
    render(<NewsAISettingsCard isAdmin />);
    const field = await screen.findByLabelText(/Minimum confidence/);
    await waitFor(() => expect(field).toHaveValue("75"));
    expect(screen.getByRole("button", { name: "Reset to defaults" })).toBeDisabled();
    fireEvent.change(field, { target: { value: "85" } });
    fireEvent.change(screen.getByLabelText(/Max trades per day/), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save guardrails" }));
    await waitFor(() => expect(m.updateNewsAISettings).toHaveBeenCalledWith(85, 0));
  });

  it("resets an override", async () => {
    m.fetchNewsAISettings.mockResolvedValue(settings({ source: "override", min_confidence: 90, updated_by: "ops" }));
    m.resetNewsAISettings.mockResolvedValue(undefined);
    render(<NewsAISettingsCard isAdmin />);
    fireEvent.click(await screen.findByRole("button", { name: "Reset to defaults" }));
    await waitFor(() => expect(m.resetNewsAISettings).toHaveBeenCalled());
  });
});
