import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RetrainingSection } from "./RetrainingSection";

const m = {
  fetchRetrainingStatus: vi.fn(), setRetrainingEnabled: vi.fn(), setRetrainingModelEnabled: vi.fn(),
  removeRetrainingModel: vi.fn(), registerRetrainingModel: vi.fn(), triggerRetraining: vi.fn(),
};
vi.mock("../lib/mlRetraining", () => ({
  fetchRetrainingStatus: (...a: unknown[]) => m.fetchRetrainingStatus(...a),
  setRetrainingEnabled: (...a: unknown[]) => m.setRetrainingEnabled(...a),
  setRetrainingModelEnabled: (...a: unknown[]) => m.setRetrainingModelEnabled(...a),
  removeRetrainingModel: (...a: unknown[]) => m.removeRetrainingModel(...a),
  registerRetrainingModel: (...a: unknown[]) => m.registerRetrainingModel(...a),
  triggerRetraining: (...a: unknown[]) => m.triggerRetraining(...a),
}));

const status = (over = {}) => ({
  enabled: true, retrain_interval_hours: 24, min_new_candles: 48, max_drift_psi: 0.25,
  registered_models_count: 1, recent_audit: [],
  models: [{ key: "BTCUSDT_spot_1h_trend_ema_v1", symbol: "BTCUSDT", market: "spot", timeframe: "1h",
    strategy_name: "trend_ema_v1", model_type: "hist_gradient_boosting", enabled: true, drift_psi: 0.1 }],
  ...over,
});

afterEach(() => { cleanup(); Object.values(m).forEach((f) => f.mockReset()); });

describe("RetrainingSection", () => {
  it("pauses a model and refreshes", async () => {
    m.fetchRetrainingStatus.mockResolvedValue(status());
    m.setRetrainingModelEnabled.mockResolvedValue({});
    render(<RetrainingSection />);
    fireEvent.click(await screen.findByRole("button", { name: /Pause BTCUSDT trend_ema_v1/ }));
    await waitFor(() => expect(m.setRetrainingModelEnabled).toHaveBeenCalledWith("BTCUSDT_spot_1h_trend_ema_v1", false));
    expect(m.fetchRetrainingStatus).toHaveBeenCalledTimes(2);
  });

  it("requires confirmation before removing and says artifacts are kept", async () => {
    m.fetchRetrainingStatus.mockResolvedValue(status());
    m.removeRetrainingModel.mockResolvedValue({});
    render(<RetrainingSection />);
    fireEvent.click(await screen.findByRole("button", { name: /Remove BTCUSDT trend_ema_v1/ }));
    expect(m.removeRetrainingModel).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/artifacts are kept/);
    fireEvent.click(screen.getByRole("button", { name: /Confirm remove/ }));
    await waitFor(() => expect(m.removeRetrainingModel).toHaveBeenCalledWith("BTCUSDT_spot_1h_trend_ema_v1"));
  });

  it("toggles the whole subsystem and disables run-now while off", async () => {
    m.fetchRetrainingStatus.mockResolvedValue(status({ enabled: false }));
    m.setRetrainingEnabled.mockResolvedValue({});
    render(<RetrainingSection />);
    expect(await screen.findByRole("button", { name: "Run due models now" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Enable retraining" }));
    await waitFor(() => expect(m.setRetrainingEnabled).toHaveBeenCalledWith(true));
  });

  it("validates symbol, then registers", async () => {
    m.fetchRetrainingStatus.mockResolvedValue(status({ models: [], registered_models_count: 0 }));
    m.registerRetrainingModel.mockResolvedValue({});
    render(<RetrainingSection />);
    expect(await screen.findByText(/No models are registered/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Register model" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Symbol is required");
    expect(m.registerRetrainingModel).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "ethusdt" } });
    fireEvent.click(screen.getByRole("button", { name: "Register model" }));
    await waitFor(() => expect(m.registerRetrainingModel).toHaveBeenCalledWith(expect.objectContaining({ symbol: "ethusdt", market: "spot" })));
  });

  it("shows load errors", async () => {
    m.fetchRetrainingStatus.mockRejectedValue(new Error("down"));
    render(<RetrainingSection />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
