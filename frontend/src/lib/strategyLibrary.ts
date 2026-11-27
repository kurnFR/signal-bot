import { assertApiPath, getJson } from "./api";

export interface StrategyCondition {
  indicator?: string;
  operator?: string;
  value?: string | number;
}
export interface CustomStrategy {
  name: string;
  enabled?: boolean;
  active_deployments?: number;
  displayName?: string;
  description?: string;
  rationale?: string;
  longConditions?: StrategyCondition[];
  shortConditions?: StrategyCondition[];
  slAtrMult?: number;
  riskRewardRatio?: number;
  useTrailingStop?: boolean;
  trailActivationR?: number;
  trailDistanceAtrMult?: number;
}
export interface StrategyLibraryResponse { strategies: CustomStrategy[]; legacy_strategies_not_imported?: number }
export interface StrategyIndicator {
  name: string;
  label?: string;
  category?: string;
  description?: string;
  default?: number;
  type?: string;
}
export interface StrategyIndicatorsResponse {
  indicators: StrategyIndicator[];
  operators: string[];
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function isCount(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}
function validCondition(value: unknown): value is StrategyCondition {
  if (!isRecord(value)) return false;
  return (value.indicator === undefined || typeof value.indicator === "string") &&
    (value.operator === undefined || typeof value.operator === "string") &&
    (value.value === undefined || typeof value.value === "string" || isFiniteNumber(value.value));
}
function validCustomStrategy(value: unknown): value is CustomStrategy {
  if (!isRecord(value) || typeof value.name !== "string" || !value.name.trim()) return false;
  if (value.enabled !== undefined && typeof value.enabled !== "boolean") return false;
  if (value.active_deployments !== undefined && !isCount(value.active_deployments)) return false;
  for (const key of ["displayName", "description", "rationale"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "string") return false;
  }
  for (const key of ["longConditions", "shortConditions"] as const) {
    if (value[key] !== undefined && (!Array.isArray(value[key]) || !value[key].every(validCondition))) return false;
  }
  for (const key of ["slAtrMult", "riskRewardRatio", "trailActivationR", "trailDistanceAtrMult"] as const) {
    if (value[key] !== undefined && !isFiniteNumber(value[key])) return false;
  }
  return value.useTrailingStop === undefined || typeof value.useTrailingStop === "boolean";
}
function validStrategyIndicator(value: unknown): value is StrategyIndicator {
  if (!isRecord(value) || typeof value.name !== "string" || !value.name.trim()) return false;
  for (const key of ["label", "category", "description", "type"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "string") return false;
  }
  return value.default === undefined || isFiniteNumber(value.default);
}
export async function fetchCustomStrategies(signal?: AbortSignal): Promise<StrategyLibraryResponse> {
  const response = await getJson<unknown>("/api/custom-strategy/list", signal);
  if (!isRecord(response) || !Array.isArray(response.strategies) || !response.strategies.every(validCustomStrategy) ||
      (response.legacy_strategies_not_imported !== undefined && !isCount(response.legacy_strategies_not_imported))) {
    throw new Error("Strategy library returned an invalid response");
  }
  return response as unknown as StrategyLibraryResponse;
}
export async function fetchStrategyIndicators(signal?: AbortSignal): Promise<StrategyIndicatorsResponse> {
  const response = await getJson<unknown>("/api/custom-strategy/indicators", signal);
  if (!isRecord(response) || !Array.isArray(response.indicators) ||
      !response.indicators.every(validStrategyIndicator) || !Array.isArray(response.operators) ||
      !response.operators.every((operator) => typeof operator === "string")) {
    throw new Error("Strategy indicators returned an invalid response");
  }
  return response as unknown as StrategyIndicatorsResponse;
}

export interface QuickTestResult {
  status: string;
  symbol: string;
  market: string;
  timeframe: string;
  totalCandles: number;
  totalTrades: number;
  winRate: number | null;
  winRatePct: number | null;
  netR: number | null;
  profitFactor: number | null;
  maxDrawdownR: number | null;
  expectancyR: number | null;
}

export class StrategyQuickTestError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "StrategyQuickTestError";
  }
}
function validNullableFiniteNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}
function validQuickTestResult(value: unknown): value is QuickTestResult {
  if (!isRecord(value) || !isNonEmptyString(value.status) || !isNonEmptyString(value.symbol) ||
      !isNonEmptyString(value.market) || !isNonEmptyString(value.timeframe) ||
      !isCount(value.totalCandles) || !isCount(value.totalTrades)) return false;
  return ["winRate", "winRatePct", "netR", "profitFactor", "maxDrawdownR", "expectancyR"]
    .every((key) => validNullableFiniteNumber(value[key]));
}

export async function quickTestCustomStrategy(
  strategy: CustomStrategy,
  input: { symbol: string; market: "spot" | "futures"; timeframe: string },
): Promise<QuickTestResult> {
  const path = "/api/custom-strategy/quick-test";
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if no session token is available.
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify({
      strategy: {
        name: strategy.name,
        displayName: strategy.displayName || strategy.name,
        description: strategy.description || "Custom rule-based strategy",
        rationale: strategy.rationale || "",
        longConditions: strategy.longConditions || [],
        shortConditions: strategy.shortConditions || [],
        slAtrMult: strategy.slAtrMult ?? 1.5,
        riskRewardRatio: strategy.riskRewardRatio ?? 2.5,
        useTrailingStop: strategy.useTrailingStop ?? false,
        trailActivationR: strategy.trailActivationR ?? 1,
        trailDistanceAtrMult: strategy.trailDistanceAtrMult ?? 1.5,
      },
      ...input,
    }),
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
      : `Quick test failed (${response.status})`;
    throw new StrategyQuickTestError(response.status, detail);
  }
  if (!validQuickTestResult(payload)) {
    throw new StrategyQuickTestError(response.status, "Quick test returned an invalid result payload");
  }
  return payload;
}

export interface StrategyConditionInput {
  indicator: string;
  operator: string;
  value: string | number;
}
export interface CreateCustomStrategyInput {
  name: string;
  displayName: string;
  description: string;
  rationale: string;
  longConditions: StrategyConditionInput[];
  shortConditions: StrategyConditionInput[];
  slAtrMult: number;
  riskRewardRatio: number;
  useTrailingStop: boolean;
  trailActivationR: number;
  trailDistanceAtrMult: number;
}
export interface StrategyMutationResult {
  status: string;
  name?: string;
  displayName?: string;
  message: string;
}
export interface StrategyDeploymentReference { id: number; symbol: string; timeframe: string }

export class StrategyLibraryActionError extends Error {
  constructor(readonly status: number, message: string, readonly deployments: StrategyDeploymentReference[] = []) {
    super(message);
    this.name = "StrategyLibraryActionError";
  }
}
async function mutateStrategyLibrary(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<StrategyMutationResult> {
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if no session token is available.
  }
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = isRecord(payload) ? payload.detail : null;
    const message = typeof detail === "string" ? detail : isRecord(detail) && typeof detail.message === "string"
      ? detail.message : `Strategy library action failed (${response.status})`;
    const deployments = isRecord(detail) && Array.isArray(detail.deployments) ? detail.deployments.filter(
      (row): row is StrategyDeploymentReference => isRecord(row) && isCount(row.id) &&
        typeof row.symbol === "string" && typeof row.timeframe === "string",
    ) : [];
    throw new StrategyLibraryActionError(response.status, message.slice(0, 400), deployments);
  }
  if (!isRecord(payload) || typeof payload.message !== "string" ||
      (payload.status !== undefined && typeof payload.status !== "string") ||
      (payload.name !== undefined && typeof payload.name !== "string") ||
      (payload.displayName !== undefined && typeof payload.displayName !== "string")) {
    throw new StrategyLibraryActionError(response.status, "Strategy library returned an invalid action response");
  }
  return payload as unknown as StrategyMutationResult;
}
export function createCustomStrategy(input: CreateCustomStrategyInput): Promise<StrategyMutationResult> {
  return mutateStrategyLibrary("/api/custom-strategy/create", "POST", input);
}
export function updateCustomStrategy(name: string, input: CreateCustomStrategyInput): Promise<StrategyMutationResult> {
  return mutateStrategyLibrary(`/api/custom-strategy/${encodeURIComponent(name)}`, "PUT", input);
}

export function setCustomStrategyEnabled(name: string, enabled: boolean, force = false): Promise<StrategyMutationResult> {
  return mutateStrategyLibrary(`/api/custom-strategy/${encodeURIComponent(name)}/enabled?force=${force}`, "PATCH", { enabled });
}

export function deleteCustomStrategy(name: string): Promise<StrategyMutationResult> {
  const normalized = name.trim().toLowerCase();
  if (!/^[a-z0-9_]{1,80}$/.test(normalized)) {
    return Promise.reject(new StrategyLibraryActionError(400, "Strategy name is invalid."));
  }
  return mutateStrategyLibrary(`/api/custom-strategy/${encodeURIComponent(normalized)}`, "DELETE");
}

export interface AIStrategyChatMessage {
  role: "user" | "assistant";
  content: string;
}
export interface AIStrategyDraftResponse {
  status: string;
  provider: string;
  strategy: CustomStrategy;
  chatResponse: string;
}
export async function generateAIStrategyDraft(
  prompt: string,
  history: AIStrategyChatMessage[] = [],
  currentConfig?: Partial<CustomStrategy>,
): Promise<AIStrategyDraftResponse> {
  const path = "/api/custom-strategy/generate-ai";
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server rejects the request if no session token is available.
  }
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify({ prompt: prompt.trim(), history, currentConfig }),
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
      : `AI strategy generation failed (${response.status})`;
    throw new StrategyLibraryActionError(response.status, detail);
  }
  if (!isRecord(payload) || !isNonEmptyString(payload.status) || !isNonEmptyString(payload.provider) ||
      !validCustomStrategy(payload.strategy) || typeof payload.chatResponse !== "string") {
    throw new StrategyLibraryActionError(response.status, "AI generator returned an invalid strategy draft");
  }
  return payload as unknown as AIStrategyDraftResponse;
}
