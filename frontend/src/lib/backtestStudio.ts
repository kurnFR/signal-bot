import { getJson } from "./api";

export interface BacktestStrategy {
  id: string;
  baseName: string;
  isInverse: boolean;
  displayName: string;
  category: string;
  validated: boolean;
  description: string;
  params: Array<{ key: string; label: string; type: string; default?: string | number | boolean; min?: number; max?: number; step?: number }>;
  sharedParams: Array<{ key: string; label: string; type: string; default?: string | number | boolean; min?: number; max?: number; step?: number }>;
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
