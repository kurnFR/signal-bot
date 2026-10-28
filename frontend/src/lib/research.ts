import { getJson } from "./api";

export interface ResearchPosition {
  id: number; symbol: string; mode: string; entry_price: number; current_price: number;
  unrealized_pnl: number; opened_at: string;
}
export interface ResearchTrade {
  id: number; symbol: string; side: string; mode: string; entry_price: number;
  exit_price: number; realized_pnl: number; closed_at: string;
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
  strategy_id: string; mode: string; evaluation_enabled: boolean; paper_enabled: boolean;
  telegram_enabled: boolean; report_enabled: boolean; auto_promote_enabled: boolean;
}
export interface ResearchSnapshot {
  positions: ResearchPosition[]; trades: ResearchTrade[]; stats: ResearchStats;
  strategies: ResearchStrategy[];
}

/** Research dashboard reads only; lifecycle mutations remain on the legacy dashboard. */
export async function fetchResearchSnapshot(signal?: AbortSignal): Promise<ResearchSnapshot> {
  const [positions, trades, stats, strategies] = await Promise.all([
    getJson<{ positions: ResearchPosition[] }>("/api/research/retailbot2/positions?limit=100", signal),
    getJson<{ trades: ResearchTrade[] }>("/api/research/retailbot2/trades?limit=50", signal),
    getJson<ResearchStats>("/api/research/retailbot2/stats", signal),
    getJson<{ strategies: ResearchStrategy[] }>("/api/research/retailbot2/strategies", signal),
  ]);
  return {
    positions: Array.isArray(positions.positions) ? positions.positions : [],
    trades: Array.isArray(trades.trades) ? trades.trades : [],
    stats,
    strategies: Array.isArray(strategies.strategies) ? strategies.strategies : [],
  };
}
