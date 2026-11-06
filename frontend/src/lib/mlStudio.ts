import { assertApiPath, getJson, postJson } from "./api";

export class MLStudioActionError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "MLStudioActionError";
    this.status = status;
  }
}

function tokenFromStorage(): string | null {
  try {
    const token = window.localStorage.getItem("token");
    if (!token) return null;
    const trimmed = token.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

async function mlMutationRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  assertApiPath(path);
  const token = tokenFromStorage();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Session expired reset */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
    throw new MLStudioActionError(401, "Your session has expired. Sign in again.");
  }

  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`;
    if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.trim().length > 0) {
      message = payload.detail.trim();
    }
    throw new MLStudioActionError(response.status, message);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new MLStudioActionError(502, "Server returned a non-JSON response");
  }

  return payload as T;
}

export interface MLExperimentSummary {
  experiment_id: string;
  symbol: string;
  market: string;
  timeframe: string;
  strategy: string;
  winner_model: string;
  threshold: number;
  roc_auc: number | null;
  brier_score: number | null;
  baseline_test_r: number;
  baseline_test_win_rate: number;
  ml_test_r: number;
  ml_test_win_rate: number;
  ml_test_trades: number;
  dataset_rows: number;
  file_mtime: number;
}
export interface MLModelManifest {
  model_id: string;
  experiment_id?: string;
  model_type?: string;
  base_strategy?: string;
  strategy_params?: Record<string, unknown>;
  threshold?: number;
  test_metrics?: Record<string, unknown>;
  eligible_for_paper?: boolean;
  has_weights?: boolean;
  eligibility?: { eligible: boolean; reasons: string[] };
  created_at?: string;
  [key: string]: unknown;
}
export interface MLExperimentsResponse { experiments: MLExperimentSummary[] }
export interface MLModelsResponse { models: MLModelManifest[] }
export type MLExperimentDetails = Record<string, unknown>;

const SAFE_ML_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$/;
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isString(value: unknown): value is string {
  return typeof value === "string";
}
function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.trim().length > 0;
}
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function isCount(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}
function nullableFiniteNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}
function validExperimentSummary(value: unknown): value is MLExperimentSummary {
  if (!isRecord(value) || !isNonEmptyString(value.experiment_id) || !SAFE_ML_ID.test(value.experiment_id) ||
      !isNonEmptyString(value.symbol) || !isNonEmptyString(value.market) || !isNonEmptyString(value.timeframe) ||
      !isNonEmptyString(value.strategy) || !isNonEmptyString(value.winner_model) ||
      !isFiniteNumber(value.threshold) || value.threshold < 0 || value.threshold > 1 ||
      !nullableFiniteNumber(value.roc_auc) || !nullableFiniteNumber(value.brier_score) ||
      !isFiniteNumber(value.baseline_test_r) || !isFiniteNumber(value.baseline_test_win_rate) ||
      !isFiniteNumber(value.ml_test_r) || !isFiniteNumber(value.ml_test_win_rate) ||
      !isCount(value.ml_test_trades) || !isCount(value.dataset_rows) || !isFiniteNumber(value.file_mtime)) return false;
  return value.baseline_test_win_rate >= 0 && value.baseline_test_win_rate <= 100 &&
    value.ml_test_win_rate >= 0 && value.ml_test_win_rate <= 100;
}
function validModelManifest(value: unknown): value is MLModelManifest {
  if (!isRecord(value) || !isNonEmptyString(value.model_id) || !SAFE_ML_ID.test(value.model_id) ||
      typeof value.has_weights !== "boolean" || !isRecord(value.eligibility) ||
      typeof value.eligibility.eligible !== "boolean" || !Array.isArray(value.eligibility.reasons) ||
      !value.eligibility.reasons.every(isString)) return false;
  for (const key of ["experiment_id", "model_type", "base_strategy"] as const) {
    if (value[key] !== undefined && !isString(value[key])) return false;
  }
  if (value.strategy_params !== undefined && !isRecord(value.strategy_params)) return false;
  if (value.threshold !== undefined && (!isFiniteNumber(value.threshold) || value.threshold < 0 || value.threshold > 1)) return false;
  if (value.test_metrics !== undefined && !isRecord(value.test_metrics)) return false;
  if (value.eligible_for_paper !== undefined && typeof value.eligible_for_paper !== "boolean") return false;
  if (value.created_at !== undefined && !isString(value.created_at)) return false;
  return true;
}

/** Saved ML results are read-only; reject malformed summaries/manifests before using their eligibility state. */
export async function fetchMLExperiments(signal?: AbortSignal): Promise<MLExperimentsResponse> {
  const payload = await getJson<unknown>("/api/ml/experiments", signal);
  if (!isRecord(payload) || !Array.isArray(payload.experiments) || !payload.experiments.every(validExperimentSummary)) {
    throw new Error("ML experiments returned an invalid response");
  }
  return payload as unknown as MLExperimentsResponse;
}
export async function fetchMLModels(signal?: AbortSignal): Promise<MLModelsResponse> {
  const payload = await getJson<unknown>("/api/ml/models", signal);
  if (!isRecord(payload) || !Array.isArray(payload.models) || !payload.models.every(validModelManifest)) {
    throw new Error("ML models returned an invalid response");
  }
  return payload as unknown as MLModelsResponse;
}
export async function fetchMLExperimentDetails(experimentId: string, signal?: AbortSignal): Promise<MLExperimentDetails> {
  if (!SAFE_ML_ID.test(experimentId) || experimentId.includes("..")) {
    throw new TypeError("Invalid ML experiment ID");
  }
  const payload = await getJson<unknown>(`/api/ml/experiments/${encodeURIComponent(experimentId)}`, signal);
  if (!isRecord(payload) || payload.experiment_id !== experimentId ||
      !isRecord(payload.experiment) || !isRecord(payload.winner)) {
    throw new Error("ML experiment details returned an invalid response");
  }
  return payload;
}


export interface MLGeneratorExperimentRequest {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
  model_type: "logistic_regression" | "random_forest" | "hist_gradient_boosting";
  min_net_return: number;
  min_validation_trades: number;
  stop_atr_multiple: number;
  risk_reward_ratio: number;
  max_hold_bars: number;
  seed: number;
}
export interface MLGeneratorExperimentResponse {
  status: "success";
  model_id: string;
  task_type: "signal_generator_v1";
  eligible_for_paper: boolean;
  decision_policy: { min_confidence: number; min_directional_margin: number };
  dataset_rows: number;
  train_rows: number;
  validation_rows: number;
  test_rows: number;
  validation_metrics: Record<string, unknown>;
  test_metrics: Record<string, unknown>;
  message: string;
}

/** Launch only the dedicated independent generator experiment API. */
export async function runMLGeneratorExperiment(
  request: MLGeneratorExperimentRequest,
): Promise<MLGeneratorExperimentResponse> {
  const payload = await postJson<unknown, MLGeneratorExperimentRequest>(
    "/api/ml/generator/run-experiment",
    request,
  );
  if (!isRecord(payload) || payload.status !== "success" ||
      !isNonEmptyString(payload.model_id) || !SAFE_ML_ID.test(payload.model_id) ||
      payload.task_type !== "signal_generator_v1" ||
      typeof payload.eligible_for_paper !== "boolean" ||
      !isRecord(payload.decision_policy) ||
      !isFiniteNumber(payload.decision_policy.min_confidence) ||
      payload.decision_policy.min_confidence <= 0 || payload.decision_policy.min_confidence >= 1 ||
      !isFiniteNumber(payload.decision_policy.min_directional_margin) ||
      payload.decision_policy.min_directional_margin <= 0 || payload.decision_policy.min_directional_margin >= 1 ||
      !isCount(payload.dataset_rows) || !isCount(payload.train_rows) ||
      !isCount(payload.validation_rows) || !isCount(payload.test_rows) ||
      !isRecord(payload.validation_metrics) || !isRecord(payload.test_metrics) ||
      !isNonEmptyString(payload.message)) {
    throw new Error("ML generator experiment returned an invalid response");
  }
  return payload as unknown as MLGeneratorExperimentResponse;
}

export interface RunMLExperimentRequest {
  symbol: string;
  market: "spot" | "futures";
  timeframe: "15m" | "1h" | "4h" | "1d";
  strategy_name: string;
  model_type?: string;
  target_type?: string;
  min_validation_trades?: number;
}

export interface RunMLExperimentResponse {
  status: "success";
  experiment_id: string;
  result: Record<string, unknown>;
}

export interface DeployMLToPaperRequest {
  model_id: string;
  allocated_capital: number;
  risk_per_trade_pct: number;
  send_telegram: boolean;
}

export interface DeployMLToPaperResponse {
  status: "success";
  message: string;
  paper_config: Record<string, unknown>;
}

/** Launch an execution-aware ML tournament experiment across multiple classification models. */
export async function runMLExperiment(
  request: RunMLExperimentRequest,
  signal?: AbortSignal,
): Promise<RunMLExperimentResponse> {
  if (!isNonEmptyString(request.symbol) || !isNonEmptyString(request.strategy_name)) {
    throw new MLStudioActionError(400, "Symbol and strategy name are required");
  }
  const payload = await mlMutationRequest<unknown>("/api/ml/run-experiment", {
    method: "POST",
    body: JSON.stringify({
      symbol: request.symbol.trim().toUpperCase(),
      market: request.market,
      timeframe: request.timeframe,
      strategy_name: request.strategy_name.trim(),
      model_type: request.model_type?.trim().toLowerCase() || "all",
      target_type: request.target_type?.trim() || "binary_positive_r",
      min_validation_trades: request.min_validation_trades ?? 5,
    }),
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success" || !isNonEmptyString(payload.experiment_id) || !isRecord(payload.result)) {
    throw new MLStudioActionError(502, "Server returned an invalid tournament response");
  }
  return {
    status: "success",
    experiment_id: payload.experiment_id,
    result: payload.result,
  };
}

/** Deploy an eligible trained ML model to Live Paper Trading as a signal filter. */
export async function deployMLModelToPaper(
  request: DeployMLToPaperRequest,
  signal?: AbortSignal,
): Promise<DeployMLToPaperResponse> {
  if (!isNonEmptyString(request.model_id) || !SAFE_ML_ID.test(request.model_id)) {
    throw new MLStudioActionError(400, "Invalid model ID");
  }
  if (!isFiniteNumber(request.allocated_capital) || request.allocated_capital < 100) {
    throw new MLStudioActionError(400, "Allocated capital must be at least $100");
  }
  if (!isFiniteNumber(request.risk_per_trade_pct) || request.risk_per_trade_pct < 0.1 || request.risk_per_trade_pct > 10.0) {
    throw new MLStudioActionError(400, "Risk per trade must be between 0.1% and 10.0%");
  }
  const payload = await mlMutationRequest<unknown>("/api/ml/deploy-to-paper", {
    method: "POST",
    body: JSON.stringify({
      model_id: request.model_id.trim(),
      allocated_capital: request.allocated_capital,
      risk_per_trade_pct: request.risk_per_trade_pct,
      send_telegram: request.send_telegram,
    }),
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success" || !isNonEmptyString(payload.message) || !isRecord(payload.paper_config)) {
    throw new MLStudioActionError(502, "Server returned an invalid deployment response");
  }
  return {
    status: "success",
    message: payload.message,
    paper_config: payload.paper_config,
  };
}
