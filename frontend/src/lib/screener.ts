import { getJson } from "./api";

export interface ScreenerStatus {
  running: boolean;
  database_ok: boolean;
  enabled: boolean;
  timeframe: string;
  symbols_monitored: number;
  last_beat_at: string | null;
  last_signal_at: string | null;
  last_universe_refresh: string | null;
}

export interface ScreenerSignal {
  id: number;
  symbol: string;
  signal_type: string;
  rvol: number;
  volume: number;
  quote_volume: number;
  price: number;
  price_change_pct: number;
  volume_velocity: number;
  quality_score: number;
  candle_time: string | null;
  telegram_sent: boolean;
  detected_at: string;
}

export interface ScreenerStats {
  total_signals: number;
  signals_last_24h: number;
  by_type_last_24h: Record<string, number>;
}

export interface ScreenerSnapshot { status: ScreenerStatus; signals: ScreenerSignal[]; stats: ScreenerStats }

/** Read-only snapshot. No screener control endpoint is called from this module. */
export async function fetchScreenerSnapshot(signal?: AbortSignal): Promise<ScreenerSnapshot> {
  const [status, signalPayload, stats] = await Promise.all([
    getJson<ScreenerStatus>("/api/research/screener/status", signal),
    getJson<{ signals: ScreenerSignal[] }>("/api/research/screener/signals?limit=50", signal),
    getJson<ScreenerStats>("/api/research/screener/stats", signal),
  ]);
  return { status, signals: signalPayload.signals, stats };
}
