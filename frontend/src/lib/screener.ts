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

export interface ScreenerControlResponse {
  enabled: boolean;
  overrides: Record<string, number | boolean>;
  updated_at: string | null;
  tunable_fields: string[];
}
export interface ScreenerControlSaveResult { status: string; note: string }
export class ScreenerControlError extends Error {
  constructor(readonly status: number, message: string) { super(message); this.name = "ScreenerControlError"; }
}

export function fetchScreenerControl(signal?: AbortSignal): Promise<ScreenerControlResponse> {
  return getJson<ScreenerControlResponse>("/api/research/screener/control", signal);
}

/** The only non-read-only request in the screener feature; its path is fixed and server-authorized. */
export async function saveScreenerControl(enabled: boolean, overrides: Record<string, number | boolean>): Promise<ScreenerControlSaveResult> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try { const token = window.localStorage.getItem("token"); if (token) headers.Authorization = `Bearer ${token}`; } catch { /* The server will return an auth error if no token is available. */ }
  const response = await fetch("/api/research/screener/control", { method: "POST", credentials: "same-origin", headers, body: JSON.stringify({ enabled, overrides }) });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Session state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string" ? payload.detail.slice(0, 300) : `Screener settings could not be saved (${response.status})`;
    throw new ScreenerControlError(response.status, detail);
  }
  if (!payload || typeof payload !== "object" || !("status" in payload) || payload.status !== "ok") throw new ScreenerControlError(response.status, "Screener returned an invalid save response");
  return payload as ScreenerControlSaveResult;
}
