import { assertApiPath, getJson } from "./api";

export interface StrategyCondition {
  indicator?: string;
  operator?: string;
  value?: string | number;
}
export interface CustomStrategy {
  name: string;
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
export interface StrategyLibraryResponse { strategies: CustomStrategy[] }
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
export function fetchCustomStrategies(signal?: AbortSignal) {
  return getJson<StrategyLibraryResponse>("/api/custom-strategy/list", signal);
}
export function fetchStrategyIndicators(signal?: AbortSignal) {
  return getJson<StrategyIndicatorsResponse>("/api/custom-strategy/indicators", signal);
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
  if (!payload || typeof payload !== "object" || !("totalTrades" in payload) || typeof payload.totalTrades !== "number" ||
      !("totalCandles" in payload) || typeof payload.totalCandles !== "number") {
    throw new StrategyQuickTestError(response.status, "Quick test returned an invalid result payload");
  }
  return payload as QuickTestResult;
}
