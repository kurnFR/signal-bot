import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MLStudioPanel } from "./MLStudioPanel";

const experiments = { experiments: [{
  experiment_id: "btc-trend-test", symbol: "BTCUSDT", market: "spot", timeframe: "1h",
  strategy: "trend_ema_v1", winner_model: "hist_gradient_boosting", threshold: 0.2,
  roc_auc: 0.61, brier_score: 0.18, baseline_test_r: 2.1, baseline_test_win_rate: 42,
  ml_test_r: 4.2, ml_test_win_rate: 54, ml_test_trades: 33, dataset_rows: 727, file_mtime: 1793000000,
}] };
const models = { models: [{
  model_id: "btc-trend-model", experiment_id: "btc-trend-test", model_type: "hist_gradient_boosting",
  base_strategy: "trend_ema_v1", threshold: 0.2, eligible_for_paper: true, has_weights: true,
  test_metrics: { trade_count: 33, profit_factor: 1.2, max_drawdown_pct: 12, net_pnl: 20 },
  eligibility: { eligible: true, reasons: [] },
}] };
const detail = { experiment_id: "btc-trend-test", winner: { model_type: "hist_gradient_boosting" }, experiment: { ml_test_metrics: { trade_count: 33 } } };
function response(url: string) {
  const path = new URL(url, "http://localhost").pathname;
  const body = path.endsWith("/models") ? models : path.endsWith("/experiments/btc-trend-test") ? detail : experiments;
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}
describe("MLStudioPanel", () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      return Promise.resolve(response(url));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());
  it("shows persisted experiments and artifact eligibility without mutating state", async () => {
    render(<MLStudioPanel />);
    expect((await screen.findAllByText("btc-trend-test")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("hist_gradient_boosting").length).toBeGreaterThan(0);
    expect(screen.getByText("Passes current gate")).toBeInTheDocument();
    expect(screen.getByText(/Paper activation and Telegram alerts remain separate, explicit controls/i)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });
  it("filters experiments and selects a persisted result", async () => {
    render(<MLStudioPanel />);
    await screen.findAllByText("btc-trend-test");
    fireEvent.change(screen.getByLabelText("Filter experiments"), { target: { value: "does-not-match" } });
    expect(screen.getByText("No experiments match this filter.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter experiments"), { target: { value: "BTCUSDT" } });
    expect((await screen.findAllByText("btc-trend-test")).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Raw persisted experiment contract/)).toBeInTheDocument();
  });

  it("renders successful empty experiment and model lists without an API error", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      const path = new URL(url, "http://localhost").pathname;
      const payload = path.endsWith("/models") ? { models: [] } : { experiments: [] };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MLStudioPanel />);

    expect(await screen.findByText("No saved ML experiments were returned.")).toBeInTheDocument();
    expect(screen.getByText("No saved model manifests were returned.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });

  it("recovers from a temporary experiment API failure after Retry", async () => {
    let recovered = false;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      const path = new URL(url, "http://localhost").pathname;
      if (!recovered && path === "/api/ml/experiments") {
        return Promise.resolve(new Response(JSON.stringify({ detail: "temporary failure" }), { status: 503, headers: { "content-type": "application/json" } }));
      }
      return Promise.resolve(response(url));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MLStudioPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/check API and artifact-directory health, then retry/i);
    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect((await screen.findAllByText("btc-trend-test")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });

  it("launches a separate generator experiment and displays its research eligibility", async () => {
    const generatorResult = {
      status: "success",
      model_id: "generator-test-0123456789abcdef",
      task_type: "signal_generator_v1",
      eligible_for_paper: false,
      decision_policy: { min_confidence: 0.62, min_directional_margin: 0.15 },
      dataset_rows: 500,
      train_rows: 280,
      validation_rows: 90,
      test_rows: 80,
      validation_metrics: { evaluation_mode: "canonical_simulator_validation" },
      test_metrics: {
        evaluation_mode: "canonical_simulator_oos",
        trades: 12,
        profit_factor: 0.9,
        max_drawdown_pct: 18.2,
      },
      message: "Generator model saved as research-only.",
    };
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, "http://localhost").pathname;
      if (init?.method === "POST") {
        expect(path).toBe("/api/ml/generator/run-experiment");
        expect(JSON.parse(String(init.body))).toMatchObject({
          symbol: "BTCUSDT",
          market: "spot",
          timeframe: "1h",
          model_type: "hist_gradient_boosting",
          stop_atr_multiple: 1.5,
          risk_reward_ratio: 2,
          max_hold_bars: 72,
        });
        return Promise.resolve(new Response(JSON.stringify(generatorResult), {
          status: 200,
          headers: { "content-type": "application/json" },
        }));
      }
      return Promise.resolve(response(url));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MLStudioPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /Train generator/i }));

    expect(await screen.findByText("Saved as research-only")).toBeInTheDocument();
    expect(screen.getByText(generatorResult.model_id)).toBeInTheDocument();
    expect(screen.getByText(/62.0% confidence · 15.0% directional margin/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
  });

  it("shows a safe validation error when generator training is rejected", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({ detail: "internal training details" }), {
          status: 422,
          headers: { "content-type": "application/json" },
        }));
      }
      return Promise.resolve(response(url));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MLStudioPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /Train generator/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/longer history or different model settings/i);
    expect(screen.queryByText("internal training details")).not.toBeInTheDocument();
  });


});
