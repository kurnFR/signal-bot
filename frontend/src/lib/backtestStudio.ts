import { assertApiPath, getJson } from "./api";

export interface BacktestStrategyParameter {
  key: string;
  label: string;
  type: string;
  default?: string | number | boolean;
  min?: number;
  max?: number;
  step?: number;
  factor?: number;
}
export interface BacktestStrategy {
  id: string;
  baseName: string;
  isInverse: boolean;
  displayName: string;
  category: string;
  validated: boolean;
  description: string;
  params: BacktestStrategyParameter[];
  sharedParams: BacktestStrategyParameter[];
}
export interface BacktestRunSummary {
  id: number;
  symbol: string;
  market: string;
  timeframe: string;
  strategy: string;
  dataSegment: string;
  totalTrades: number;
  winRatePct: number | null;
  expectancyR: number | null;
  profitFactor: number | null;
  maxDrawdownR: number | null;
  createdAt: string;
}
export interface BacktestTrade {
  direction: string;
  entryTime: string | null;
  entryTimeWib: string;
  entryPrice: number;
  exitTime: string | null;
  exitTimeWib: string;
  exitPrice: number;
  exitReason: string;
  stopLoss: number;
  takeProfit: number;
  netReturnPct: number;
  rMultiple: number | null;
  holdingBars: number;
}
export interface BacktestRunDetails extends BacktestRunSummary {
  params: Record<string, unknown> | null;
}
export interface BacktestHistoryResponse { runs: BacktestRunSummary[] }
export interface BacktestStrategiesResponse { strategies: BacktestStrategy[] }
export interface BacktestDetailsResponse { run: BacktestRunDetails; trades: BacktestTrade[] }

export function fetchBacktestStrategies(signal?: AbortSignal) {
  return getJson<BacktestStrategiesResponse>("/api/backtest/strategies", signal);
}
export function fetchBacktestHistory(signal?: AbortSignal) {
  return getJson<BacktestHistoryResponse>("/api/backtest/history?limit=100", signal);
}
export function fetchBacktestRunDetails(runId: number, signal?: AbortSignal) {
  if (!Number.isSafeInteger(runId) || runId < 1) throw new TypeError("Backtest run ID must be a positive integer");
  return getJson<BacktestDetailsResponse>(`/api/backtest/runs/${runId}`, signal);
}

export interface BacktestExecutionInput {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
  strategy: string;
  data_segment: "full" | "train" | "holdout";
  initial_capital: number;
  risk_per_trade_pct: number;
  save_to_db: true;
  params: Record<string, string | number | boolean>;
}

export interface BacktestExecutionResult {
  runId: number | null;
  symbol: string;
  market: string;
  timeframe: string;
  strategy: string;
  dataSegment: string;
  totalCandles: number;
  metrics: {
    total_trades: number;
    win_rate_pct: number | null;
    expectancy_r: number | null;
    profit_factor: number | null;
    max_drawdown_r: number | null;
    [key: string]: number | null;
  };
  trades: unknown[];
}

export class BacktestExecutionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "BacktestExecutionError";
  }
}

/** Execute one explicitly confirmed, persisted backtest for an admin/trader session. */
export async function executeBacktest(input: BacktestExecutionInput): Promise<BacktestExecutionResult> {
  const path = "/api/backtest/run";
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if the browser cannot provide a session token.
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(input),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail.slice(0, 400)
      : `Backtest failed (${response.status})`;
    throw new BacktestExecutionError(response.status, detail);
  }
  if (!payload || typeof payload !== "object" || !("metrics" in payload) || !payload.metrics ||
      typeof payload.metrics !== "object" || !("trades" in payload) || !Array.isArray(payload.trades) ||
      !("runId" in payload) || (payload.runId !== null && typeof payload.runId !== "number")) {
    throw new BacktestExecutionError(response.status, "Backtest returned an invalid result payload");
  }
  return payload as BacktestExecutionResult;
}


export interface PromotionPolicy {
  enabled: boolean;
  min_test_trades: number;
  min_profit_factor: number;
  min_expectancy_r: number;
  max_drawdown_r: number;
  min_rank_score: number;
  require_holdout: boolean;
  send_telegram: boolean;
}

export interface PromotionAuditEvent {
  decision_hash: string;
  symbol: string;
  market: string;
  timeframe: string;
  data_segment: string;
  strategy_name: string;
  status: string;
  reason: string;
  paper_config_id: number | null;
  telegram_result: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PromotionSnapshot {
  policy: PromotionPolicy;
  events: PromotionAuditEvent[];
}

/** Read-only promotion state; never updates policy or triggers promotion/Telegram actions. */
export async function fetchPromotionSnapshot(signal?: AbortSignal): Promise<PromotionSnapshot> {
  const [policyPayload, auditPayload] = await Promise.all([
    getJson<{ policy: PromotionPolicy }>("/api/backtest/promotion-policy", signal),
    getJson<{ events: PromotionAuditEvent[] }>("/api/backtest/promotion-audit?limit=5", signal),
  ]);
  if (!policyPayload || !policyPayload.policy || !Array.isArray(auditPayload?.events)) {
    throw new Error("Promotion status returned an invalid response");
  }
  return { policy: policyPayload.policy, events: auditPayload.events };
}


export interface PromotionPolicySaveResult {
  status: "ok" | "saved_readback_unavailable";
  policy: PromotionPolicy;
  warning?: string;
}

export class PromotionPolicySaveError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "PromotionPolicySaveError";
  }
}

function isPromotionPolicy(value: unknown): value is PromotionPolicy {
  if (!value || typeof value !== "object") return false;
  const policy = value as Record<string, unknown>;
  return typeof policy.enabled === "boolean" &&
    Number.isInteger(policy.min_test_trades) &&
    typeof policy.min_profit_factor === "number" && Number.isFinite(policy.min_profit_factor) &&
    typeof policy.min_expectancy_r === "number" && Number.isFinite(policy.min_expectancy_r) &&
    typeof policy.max_drawdown_r === "number" && Number.isFinite(policy.max_drawdown_r) &&
    typeof policy.min_rank_score === "number" && Number.isFinite(policy.min_rank_score) &&
    typeof policy.require_holdout === "boolean" &&
    typeof policy.send_telegram === "boolean";
}

/** Save only the policy configuration. This does not execute a backtest or send Telegram. */
export async function savePromotionPolicy(policy: PromotionPolicy): Promise<PromotionPolicySaveResult> {
  const path = "/api/backtest/promotion-policy";
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if the browser cannot provide a session token.
  }
  const response = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(policy),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail.slice(0, 400)
      : `Promotion policy save failed (${response.status})`;
    throw new PromotionPolicySaveError(response.status, detail);
  }
  if (!payload || typeof payload !== "object" ||
      !("status" in payload) || (payload.status !== "ok" && payload.status !== "saved_readback_unavailable") ||
      !("policy" in payload) || !isPromotionPolicy(payload.policy)) {
    throw new PromotionPolicySaveError(response.status, "Promotion policy save returned an invalid response");
  }
  const result = payload as PromotionPolicySaveResult;
  if (result.status === "saved_readback_unavailable" && typeof result.warning !== "string") {
    throw new PromotionPolicySaveError(response.status, "Promotion policy was saved but the server did not return a readback warning");
  }
  return result;
}
