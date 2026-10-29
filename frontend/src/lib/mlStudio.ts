import { getJson } from "./api";

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
