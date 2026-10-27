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

export function fetchMLExperiments(signal?: AbortSignal) {
  return getJson<MLExperimentsResponse>("/api/ml/experiments", signal);
}
export function fetchMLModels(signal?: AbortSignal) {
  return getJson<MLModelsResponse>("/api/ml/models", signal);
}
export function fetchMLExperimentDetails(experimentId: string, signal?: AbortSignal) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$/.test(experimentId) || experimentId.includes("..")) {
    throw new TypeError("Invalid ML experiment ID");
  }
  return getJson<MLExperimentDetails>(`/api/ml/experiments/${encodeURIComponent(experimentId)}`, signal);
}
