import { getJson } from "./api";

export interface NewsStats {
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  average_confidence: number;
  active_overlay_multiplier: number;
}
export interface NewsSignal {
  id: number; symbol: string; headline: string; sentiment: string;
  confidence: number; impact_horizon: string; published_at: string;
}
export interface NewsEvent {
  id: number; category: string; event_text: string; importance: string; detected_at: string;
}
export interface NewsSnapshot { stats: NewsStats; signals: NewsSignal[]; events: NewsEvent[] }

/** Read-only news overlay data. Does not poll sources, call AI, or execute trades. */
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
