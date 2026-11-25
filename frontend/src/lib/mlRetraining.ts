import { apiRequest, getJson } from "./api";

export interface RetrainingModel {
  key: string;
  symbol: string;
  market: string;
  timeframe: string;
  strategy_name: string;
  model_type?: string;
  enabled?: boolean;
  status?: string;
  last_retrained_at?: string | null;
  drift_psi?: number;
}

export interface RetrainingStatus {
  enabled: boolean;
  retrain_interval_hours: number;
  min_new_candles: number;
  max_drift_psi: number;
  registered_models_count: number;
  models: RetrainingModel[];
  recent_audit: Array<Record<string, unknown>>;
}

export interface RegisterRetrainingRequest {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
  strategy_name: string;
  model_type: string;
}

export async function fetchRetrainingStatus(signal?: AbortSignal): Promise<RetrainingStatus> {
  const data = await getJson<Partial<RetrainingStatus>>("/api/ml/retraining/status", signal);
  if (typeof data.enabled !== "boolean" || !Array.isArray(data.models)) {
    throw new Error("Unexpected retraining status response");
  }
  return { recent_audit: [], retrain_interval_hours: 24, min_new_candles: 48, max_drift_psi: 0.25,
    registered_models_count: data.models.length, ...data } as RetrainingStatus;
}

export const setRetrainingEnabled = (enabled: boolean) =>
  apiRequest("/api/ml/retraining/enabled", { method: "PUT", json: { enabled } });

export const setRetrainingModelEnabled = (key: string, enabled: boolean) =>
  apiRequest(`/api/ml/retraining/models/${encodeURIComponent(key)}`, { method: "PATCH", json: { enabled } });

export const removeRetrainingModel = (key: string) =>
  apiRequest(`/api/ml/retraining/models/${encodeURIComponent(key)}`, { method: "DELETE" });

export const registerRetrainingModel = (req: RegisterRetrainingRequest) =>
  apiRequest("/api/ml/retraining/register", {
    method: "POST",
    // auto_promote is not implemented server-side, so the UI never requests it.
    json: { ...req, symbol: req.symbol.trim().toUpperCase(), strategy_name: req.strategy_name.trim(), auto_promote: false },
  });

export const triggerRetraining = (force: boolean) =>
  apiRequest(`/api/ml/retraining/trigger?force=${force ? "true" : "false"}`, { method: "POST" });
