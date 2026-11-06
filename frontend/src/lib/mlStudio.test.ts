import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deployMLModelToPaper,
  fetchMLExperimentDetails,
  fetchMLExperiments,
  fetchMLModels,
  MLStudioActionError,
  runMLExperiment,
} from "./mlStudio";

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

  it("rejects path traversal and malformed experiment IDs before making a request", async () => {
    await expect(fetchMLExperimentDetails("../outside")).rejects.toThrow(/invalid ml experiment id/i);
    await expect(fetchMLExperimentDetails("bad/id")).rejects.toThrow(/invalid ml experiment id/i);
  });
});

describe("ML Studio mutation contracts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  describe("runMLExperiment", () => {
    it("submits tournament launch request and returns experiment result", async () => {
      window.localStorage.setItem("token", "trader-token");
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          status: "success",
          experiment_id: "exp_123",
          result: { winner: { model_type: "random_forest" } },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await runMLExperiment({
        symbol: "BTCUSDT",
        market: "spot",
        timeframe: "1h",
        strategy_name: "trend_ema_v1",
        model_type: "all",
        target_type: "binary_positive_r",
        min_validation_trades: 5,
      });

      expect(res.status).toBe("success");
      expect(res.experiment_id).toBe("exp_123");
      expect(fetchMock).toHaveBeenCalledWith("/api/ml/run-experiment", expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer trader-token" }),
      }));
    });

    it("rejects empty symbol or strategy without request", async () => {
      await expect(runMLExperiment({
        symbol: "",
        market: "spot",
        timeframe: "1h",
        strategy_name: "trend_ema_v1",
      })).rejects.toThrow(MLStudioActionError);
    });
  });

  describe("deployMLModelToPaper", () => {
    it("submits deployment request and handles actual backend response shape (config, telegram, sync)", async () => {
      const actualBackendResponse = {
        status: "success",
        message: "ML Model 'valid-model-1' successfully activated for BTCUSDT (1h) trend_ema_v1.",
        config: { model_id: "valid-model-1", allocated_capital: 5000, risk_per_trade_pct: 1.0 },
        telegram: null,
        sync: { status: "synced" },
      };
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify(actualBackendResponse),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await deployMLModelToPaper({
        model_id: "valid-model-1",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      });

      expect(res.status).toBe("success");
      expect(res.message).toBe("ML Model 'valid-model-1' successfully activated for BTCUSDT (1h) trend_ema_v1.");
      expect(res.config).toEqual(actualBackendResponse.config);
      expect(res.paper_config).toEqual(actualBackendResponse.config);
      expect(res.telegram).toBeNull();
      expect(res.sync).toEqual({ status: "synced" });
      expect(fetchMock).toHaveBeenCalledWith("/api/ml/deploy-to-paper", expect.objectContaining({
        method: "POST",
      }));
    });

    it("supports paper_config alias for backwards compatibility", async () => {
      const legacyResponse = {
        status: "success",
        message: "Deployed successfully via legacy alias",
        paper_config: { model_id: "legacy-model-1" },
      };
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify(legacyResponse),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      const res = await deployMLModelToPaper({
        model_id: "legacy-model-1",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      });

      expect(res.status).toBe("success");
      expect(res.config).toEqual({ model_id: "legacy-model-1" });
      expect(res.paper_config).toEqual({ model_id: "legacy-model-1" });
    });

    it("rejects with 502 when backend response is missing both config and paper_config", async () => {
      const invalidResponse = {
        status: "success",
        message: "Missing config fields",
      };
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify(invalidResponse),
        { status: 200, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(deployMLModelToPaper({
        model_id: "valid-model-1",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      })).rejects.toThrow("Server returned an invalid deployment response");
    });

    it("propagates 409 when model fails paper eligibility gate", async () => {
      const fetchMock = vi.fn().mockResolvedValue(new Response(
        JSON.stringify({ detail: "Model is not eligible for paper trading: insufficient validation trades" }),
        { status: 409, headers: { "content-type": "application/json" } },
      ));
      vi.stubGlobal("fetch", fetchMock);

      await expect(deployMLModelToPaper({
        model_id: "ineligible-model",
        allocated_capital: 5000,
        risk_per_trade_pct: 1.0,
        send_telegram: false,
      })).rejects.toThrow(/insufficient validation trades/);
    });
  });
});
