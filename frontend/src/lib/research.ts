import { getJson } from "./api";

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
  market?: string;
  timeframe?: string;
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
  market?: string;
  timeframe?: string;
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
export interface ResearchSnapshot {
  positions: ResearchPosition[];
  trades: ResearchTrade[];
  stats: ResearchStats;
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
function optionalString(value: unknown): boolean {
  return value === undefined || isString(value);
}
function validPosition(value: unknown): value is ResearchPosition {
  return isRecord(value) && isCount(value.id) && isNonEmptyString(value.symbol) &&
    isNonEmptyString(value.side) && isNonEmptyString(value.mode) &&
    isFiniteNumber(value.entry_price) && nullableNumber(value.stop_price) &&
    nullableNumber(value.target_price) && nullableNumber(value.atr) &&
    nullableNumber(value.rr) && isFiniteNumber(value.units) &&
    isNonEmptyString(value.strategy) && nullableNumber(value.confluence_score) &&
    nullableString(value.reason) && nullableString(value.entry_time) &&
    optionalString(value.market) && optionalString(value.timeframe) &&
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
    nullableString(value.exit_time) &&
    optionalString(value.market) && optionalString(value.timeframe);
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
    isRecord(value.by_mode.paper);
}

/** Read-only Research snapshot from the Paper Trading ledger
 * (`paper_positions` / `paper_trades`). Validate the real
 * FastAPI/DB serializer contracts before rendering. */
export async function fetchResearchSnapshot(signal?: AbortSignal): Promise<ResearchSnapshot> {
  const [positions, trades, stats] = await Promise.all([
    getJson<unknown>("/api/research/retailbot2/positions?limit=100", signal),
    getJson<unknown>("/api/research/retailbot2/trades?limit=50", signal),
    getJson<unknown>("/api/research/retailbot2/stats", signal),
  ]);
  if (!isRecord(positions) || !Array.isArray(positions.positions) || !positions.positions.every(validPosition)) {
    throw new Error("Research positions returned an invalid response");
  }
  if (!isRecord(trades) || !Array.isArray(trades.trades) || !trades.trades.every(validTrade)) {
    throw new Error("Research trades returned an invalid response");
  }
  if (!validStats(stats)) throw new Error("Research stats returned an invalid response");
  return {
    positions: positions.positions,
    trades: trades.trades,
    stats,
  };
}
