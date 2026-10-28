import { getJson } from "./api";

export interface NewsPerformance {
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  total_r: number;
  net_pnl: number;
  max_drawdown_r: number;
}
export interface NewsHeartbeat {
  collector_name: string;
  status: string;
  detail: string | null;
  last_beat_time: string | null;
}
export interface NewsStats {
  total_events_collected: number;
  events_evaluated_by_ai: number;
  high_confidence_signals: number;
  avg_confidence: number | null;
  trades_opened: number;
  pending_signals: number;
  performance: NewsPerformance;
  heartbeats: NewsHeartbeat[];
}
export interface NewsSignal {
  id: number;
  symbol: string;
  market: string | null;
  timeframe: string | null;
  bias: string | null;
  confidence: number | null;
  reasoning: string | null;
  invalidation_condition: string | null;
  time_horizon: string | null;
  trade_timing: string | null;
  acted_on: boolean;
  paper_position_id: number | null;
  skip_reason: string | null;
  created_at: string | null;
  headline: string | null;
  url: string | null;
  source: string | null;
  category: string | null;
  country: string | null;
  impact: string | null;
  scheduled_at: string | null;
  actual_value: string | number | null;
  forecast_value: string | number | null;
  previous_value: string | number | null;
}
export interface NewsEvent {
  id: number;
  source: string | null;
  category: string | null;
  headline: string | null;
  url: string | null;
  symbols: string | null;
  country: string | null;
  impact: string | null;
  published_at: string | null;
  scheduled_at: string | null;
  actual_value: string | number | null;
  forecast_value: string | number | null;
  previous_value: string | number | null;
  processed_at: string | null;
  fetched_at: string | null;
}
export interface NewsSnapshot { stats: NewsStats; signals: NewsSignal[]; events: NewsEvent[] }

/** Read-only News AI data. This does not start collectors, call AI, or execute trades. */
export async function fetchNewsSnapshot(signal?: AbortSignal): Promise<NewsSnapshot> {
  const [stats, signals, events] = await Promise.all([
    getJson<NewsStats>("/api/news/stats", signal),
    getJson<{ signals: NewsSignal[] }>("/api/news/signals?limit=50", signal),
    getJson<{ events: NewsEvent[] }>("/api/news/events?limit=30", signal),
  ]);
  return {
    stats,
    signals: Array.isArray(signals.signals) ? signals.signals : [],
    events: Array.isArray(events.events) ? events.events : [],
  };
}
