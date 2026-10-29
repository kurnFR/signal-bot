import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchMLExperimentDetails, fetchMLExperiments, fetchMLModels } from "./mlStudio";

const experiment = {
  experiment_id: "btc-trend-test", symbol: "BTCUSDT", market: "spot", timeframe: "1h",
  strategy: "trend_ema_v1", winner_model: "hist_gradient_boosting", threshold: 0.2,
  roc_auc: 0.61, brier_score: 0.18, baseline_test_r: 2.1, baseline_test_win_rate: 42,
  ml_test_r: 4.2, ml_test_win_rate: 54, ml_test_trades: 33, dataset_rows: 727, file_mtime: 1793000000,
};
const model = {
  model_id: "btc-trend-model", experiment_id: "btc-trend-test", model_type: "hist_gradient_boosting",
  base_strategy: "trend_ema_v1", threshold: 0.2, eligible_for_paper: true, has_weights: true,
  test_metrics: { trade_count: 33, profit_factor: 1.2, max_drawdown_pct: 12, net_pnl: 20 },
  eligibility: { eligible: true, reasons: [] },
};
const detail = {
  experiment_id: "btc-trend-test",
  winner: { model_type: "hist_gradient_boosting" },
  experiment: { symbol: "BTCUSDT", ml_test_metrics: { trade_count: 33 } },
};
function installFetch(overrides: Record<string, unknown> = {}) {
  const payloads: Record<string, unknown> = {
    "/api/ml/experiments": { experiments: [experiment] },
    "/api/ml/models": { models: [model] },
    "/api/ml/experiments/btc-trend-test": detail,
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") throw new Error("ML Studio snapshot must use GET");
    const path = new URL(String(input), "http://localhost").pathname;
    return Promise.resolve(new Response(JSON.stringify(payloads[path]), {
      status: 200, headers: { "content-type": "application/json" },
    }));
  }));
}
beforeEach(() => {
  window.localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("ML Studio response contracts", () => {
  it("accepts backend-shaped experiment, model, and detail payloads", async () => {
    const [experiments, models, details] = await Promise.all([
      fetchMLExperiments(), fetchMLModels(), fetchMLExperimentDetails("btc-trend-test"),
    ]);
    expect(experiments.experiments[0].ml_test_trades).toBe(33);
    expect(models.models[0].eligibility?.eligible).toBe(true);
    expect(details.experiment_id).toBe("btc-trend-test");
  });

  it("rejects malformed experiment numeric metrics", async () => {
    installFetch({ "/api/ml/experiments": { experiments: [{ ...experiment, ml_test_trades: "33" }] } });
    await expect(fetchMLExperiments()).rejects.toThrow(/experiments returned an invalid response/i);
  });

  it("rejects malformed model eligibility flags", async () => {
    installFetch({ "/api/ml/models": { models: [{ ...model, eligibility: { eligible: "true", reasons: [] } }] } });
    await expect(fetchMLModels()).rejects.toThrow(/models returned an invalid response/i);
  });

  it("rejects details that do not match the requested experiment identity", async () => {
    installFetch({ "/api/ml/experiments/btc-trend-test": { ...detail, experiment_id: "other-experiment" } });
    await expect(fetchMLExperimentDetails("btc-trend-test")).rejects.toThrow(/details returned an invalid response/i);
  });

  it("rejects path traversal and malformed experiment IDs before making a request", () => {
    await expect(fetchMLExperimentDetails("../outside")).rejects.toThrow(/invalid ml experiment id/i);
    await expect(fetchMLExperimentDetails("bad/id")).rejects.toThrow(/invalid ml experiment id/i);
  });
});
