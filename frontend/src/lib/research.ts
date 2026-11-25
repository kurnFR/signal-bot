import { assertApiPath, getJson } from "./api";

export class ResearchActionError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ResearchActionError";
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

async function researchMutationRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
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
    throw new ResearchActionError(401, "Your session has expired. Sign in again.");
  }

  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`;
    if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.trim().length > 0) {
      message = payload.detail.trim();
    }
    throw new ResearchActionError(response.status, message);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new ResearchActionError(502, "Server returned a non-JSON response");
  }

  return payload as T;
}

export interface ResearchPosition {
  id: number;
  symbol: string;
  side: string;
  mode: string;
  entry_price: number;
  stop_price: number | null;
  target_price: number | null;
  atr: number | null;
  rr: number | null;
  units: number;
  strategy: string;
  strategies_json?: unknown;
  confluence_score: number | null;
  reason: string | null;
  entry_time: string | null;
}
export interface ResearchTrade {
  id: number;
  symbol: string;
  side: string;
  mode: string;
  entry_price: number;
  stop_price: number | null;
  target_price: number | null;
  units: number;
  strategy: string;
  confluence_score: number | null;
  status: string;
  exit_price: number | null;
  exit_reason: string | null;
  net_pnl: number;
  pnl_percent: number | null;
  entry_time: string | null;
  exit_time: string | null;
}
export interface ResearchStrategyStats {
  strategy: string;
  mode: string;
  total_trades: number;
  wins: number;
  losses: number;
  total_pnl: number;
  last_updated: string | null;
}
export interface ResearchModeStats {
  realized_pnl: number;
  closed_count: number;
  wins: number;
  win_rate: number | null;
  open_count: number;
}
export interface ResearchStats {
  by_strategy: ResearchStrategyStats[];
  by_mode: Record<string, ResearchModeStats>;
}
export interface ResearchStrategy {
  strategy_id: string;
  mode: string;
  evaluation_enabled: boolean;
  paper_enabled: boolean;
  telegram_enabled: boolean;
  report_enabled: boolean;
  auto_promote_enabled: boolean;
  historical_only?: boolean;
  updated_at?: string | null;
}
export interface ResearchSnapshot {
  positions: ResearchPosition[];
  trades: ResearchTrade[];
  stats: ResearchStats;
  strategies: ResearchStrategy[];
}

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
function nullableString(value: unknown): boolean {
  return value === null || isString(value);
}
function nullableNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}
function validPosition(value: unknown): value is ResearchPosition {
  return isRecord(value) && isCount(value.id) && isNonEmptyString(value.symbol) &&
    isNonEmptyString(value.side) && isNonEmptyString(value.mode) &&
    isFiniteNumber(value.entry_price) && nullableNumber(value.stop_price) &&
    nullableNumber(value.target_price) && nullableNumber(value.atr) &&
    nullableNumber(value.rr) && isFiniteNumber(value.units) &&
    isNonEmptyString(value.strategy) && nullableNumber(value.confluence_score) &&
    nullableString(value.reason) && nullableString(value.entry_time) &&
    (value.strategies_json === undefined || value.strategies_json === null || isString(value.strategies_json) || isRecord(value.strategies_json));
}
function validTrade(value: unknown): value is ResearchTrade {
  return isRecord(value) && isCount(value.id) && isNonEmptyString(value.symbol) &&
    isNonEmptyString(value.side) && isNonEmptyString(value.mode) &&
    isFiniteNumber(value.entry_price) && nullableNumber(value.stop_price) &&
    nullableNumber(value.target_price) && isFiniteNumber(value.units) &&
    isNonEmptyString(value.strategy) && nullableNumber(value.confluence_score) &&
    isNonEmptyString(value.status) && nullableNumber(value.exit_price) &&
    nullableString(value.exit_reason) && isFiniteNumber(value.net_pnl) &&
    nullableNumber(value.pnl_percent) && nullableString(value.entry_time) &&
    nullableString(value.exit_time);
}
function validStrategyStats(value: unknown): value is ResearchStrategyStats {
  return isRecord(value) && isNonEmptyString(value.strategy) && isNonEmptyString(value.mode) &&
    isCount(value.total_trades) && isCount(value.wins) && isCount(value.losses) &&
    isFiniteNumber(value.total_pnl) && nullableString(value.last_updated);
}
function validModeStats(value: unknown): value is ResearchModeStats {
  return isRecord(value) && isFiniteNumber(value.realized_pnl) &&
    isCount(value.closed_count) && isCount(value.wins) &&
    nullableNumber(value.win_rate) && isCount(value.open_count);
}
function validStats(value: unknown): value is ResearchStats {
  return isRecord(value) && Array.isArray(value.by_strategy) &&
    value.by_strategy.every(validStrategyStats) && isRecord(value.by_mode) &&
    Object.values(value.by_mode).every(validModeStats) &&
    isRecord(value.by_mode.shadow) && isRecord(value.by_mode.inverse);
}
function validStrategy(value: unknown): value is ResearchStrategy {
  if (!isRecord(value) || !isNonEmptyString(value.strategy_id) || !isNonEmptyString(value.mode) ||
      typeof value.evaluation_enabled !== "boolean" || typeof value.paper_enabled !== "boolean" ||
      typeof value.telegram_enabled !== "boolean" || typeof value.report_enabled !== "boolean" ||
      typeof value.auto_promote_enabled !== "boolean") return false;
  return (value.historical_only === undefined || typeof value.historical_only === "boolean") &&
    (value.updated_at === undefined || nullableString(value.updated_at));
}

/** Read-only Research snapshot. Validate the real FastAPI/DB serializer contracts before rendering. */
export async function fetchResearchSnapshot(signal?: AbortSignal, includeStrategies = true): Promise<ResearchSnapshot> {
  // Batch requests in 2 concurrent pairs to stay well within retailbot2_pool size (pool_size=3)
  const [positions, stats] = await Promise.all([
    getJson<unknown>("/api/research/retailbot2/positions?limit=100", signal),
    getJson<unknown>("/api/research/retailbot2/stats", signal),
  ]);
  const [trades, strategies] = await Promise.all([
    getJson<unknown>("/api/research/retailbot2/trades?limit=50", signal),
    includeStrategies ? getJson<unknown>("/api/research/retailbot2/strategies", signal) : Promise.resolve({ strategies: [] }),
  ]);
  if (!isRecord(positions) || !Array.isArray(positions.positions) || !positions.positions.every(validPosition)) {
    throw new Error("Research positions returned an invalid response");
  }
  if (!isRecord(trades) || !Array.isArray(trades.trades) || !trades.trades.every(validTrade)) {
    throw new Error("Research trades returned an invalid response");
  }
  if (!validStats(stats)) throw new Error("Research stats returned an invalid response");
  if (!isRecord(strategies) || !Array.isArray(strategies.strategies) || !strategies.strategies.every(validStrategy)) {
    throw new Error("Research strategies returned an invalid response");
  }
  return {
    positions: positions.positions,
    trades: trades.trades,
    stats,
    strategies: strategies.strategies,
  };
}

export interface StrategyLifecycleRequest {
  strategy_id: string;
  mode: "shadow" | "inverse";
  evaluation_enabled: boolean;
  paper_enabled: boolean;
  telegram_enabled: boolean;
  report_enabled: boolean;
  auto_promote_enabled?: boolean;
}

export interface StrategyLifecycleResponse {
  status: string;
  strategy_id: string;
  mode: string;
  settings: Record<string, unknown>;
  note?: string;
}

/** Update RetailBot2 strategy lifecycle flags (evaluation, paper tracking, Telegram alerts, report visibility). */
export async function saveStrategyLifecycle(
  request: StrategyLifecycleRequest,
  signal?: AbortSignal,
): Promise<StrategyLifecycleResponse> {
  if (!isNonEmptyString(request.strategy_id) || (request.mode !== "shadow" && request.mode !== "inverse")) {
    throw new ResearchActionError(400, "Valid strategy ID and mode are required");
  }
  const payload = await researchMutationRequest<unknown>("/api/research/retailbot2/strategies", {
    method: "POST",
    body: JSON.stringify({
      strategy_id: request.strategy_id,
      mode: request.mode,
      evaluation_enabled: Boolean(request.evaluation_enabled),
      paper_enabled: Boolean(request.paper_enabled),
      telegram_enabled: Boolean(request.telegram_enabled),
      report_enabled: Boolean(request.report_enabled),
      auto_promote_enabled: false,
    }),
    signal,
  });
  if (!isRecord(payload) || payload.status !== "ok" || payload.strategy_id !== request.strategy_id || payload.mode !== request.mode) {
    throw new ResearchActionError(502, "Server returned an invalid strategy lifecycle response");
  }
  return {
    status: "ok",
    strategy_id: request.strategy_id,
    mode: request.mode,
    settings: isRecord(payload.settings) ? payload.settings : {},
    note: typeof payload.note === "string" ? payload.note : undefined,
  };
}

/** Stop evaluation and paper entries, disable Telegram alerts, and hide a RetailBot2 strategy from reports. */
export async function stopAndHideStrategy(
  strategyId: string,
  mode: "shadow" | "inverse",
  signal?: AbortSignal,
): Promise<StrategyLifecycleResponse> {
  return saveStrategyLifecycle(
    {
      strategy_id: strategyId,
      mode,
      evaluation_enabled: false,
      paper_enabled: false,
      telegram_enabled: false,
      report_enabled: false,
      auto_promote_enabled: false,
    },
    signal,
  );
}
