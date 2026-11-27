import { getJson, apiRequest, ApiError } from "./api";

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
function validScreenerStatus(value: unknown): value is ScreenerStatus {
  if (!isRecord(value) || typeof value.running !== "boolean" ||
      typeof value.database_ok !== "boolean" || typeof value.enabled !== "boolean" ||
      !isNonEmptyString(value.timeframe) || !isCount(value.symbols_monitored) ||
      !nullableString(value.last_beat_at) || !nullableString(value.last_signal_at) ||
      !nullableString(value.last_universe_refresh)) return false;
  return value.pid === undefined || value.pid === null || isCount(value.pid);
}
function validScreenerSignal(value: unknown): value is ScreenerSignal {
  return isRecord(value) && isCount(value.id) && isNonEmptyString(value.symbol) &&
    isNonEmptyString(value.signal_type) &&
    ["rvol", "volume", "quote_volume", "price", "price_change_pct", "volume_velocity", "quality_score"]
      .every((key) => isFiniteNumber(value[key])) &&
    nullableString(value.candle_time) && typeof value.telegram_sent === "boolean" &&
    isNonEmptyString(value.detected_at);
}
function validScreenerStats(value: unknown): value is ScreenerStats {
  return isRecord(value) && isCount(value.total_signals) &&
    isCount(value.signals_last_24h) && isRecord(value.by_type_last_24h) &&
    Object.values(value.by_type_last_24h).every(isCount);
}

export async function fetchScreenerStatus(signal?: AbortSignal): Promise<ScreenerStatus> {
  const status = await getJson<unknown>("/api/research/screener/status", signal);
  if (!validScreenerStatus(status)) throw new Error("Screener status returned an invalid response");
  return status;
}

/** Read-only snapshot. Reject malformed backend rows instead of rendering partial or misleading metrics. */
export async function fetchScreenerSnapshot(signal?: AbortSignal): Promise<ScreenerSnapshot> {
  const [status, signalPayload, stats] = await Promise.all([
    fetchScreenerStatus(signal),
    getJson<unknown>("/api/research/screener/signals?limit=50", signal),
    getJson<unknown>("/api/research/screener/stats", signal),
  ]);
  if (!isRecord(signalPayload) || !Array.isArray(signalPayload.signals) ||
      !signalPayload.signals.every(validScreenerSignal)) {
    throw new Error("Screener signals returned an invalid response");
  }
  if (!validScreenerStats(stats)) throw new Error("Screener stats returned an invalid response");
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

export async function fetchScreenerControl(signal?: AbortSignal): Promise<ScreenerControlResponse> {
  const payload = await getJson<unknown>("/api/research/screener/control", signal);
  if (!isRecord(payload) || typeof payload.enabled !== "boolean" || !isRecord(payload.overrides) ||
      !nullableString(payload.updated_at) || !Array.isArray(payload.tunable_fields) ||
      !payload.tunable_fields.every(isNonEmptyString)) {
    throw new Error("Screener control returned an invalid response");
  }
  const tunable = new Set(payload.tunable_fields);
  const overridesValid = Object.entries(payload.overrides).every(([key, value]) =>
    tunable.has(key) && (typeof value === "boolean" || isFiniteNumber(value)));
  if (!overridesValid) throw new Error("Screener control returned an invalid response");
  return payload as unknown as ScreenerControlResponse;
}

/** The only non-read-only request in the screener feature; its path is fixed and server-authorized. */
export async function saveScreenerControl(enabled: boolean, overrides: Record<string, number | boolean>): Promise<ScreenerControlSaveResult> {
  try {
    const payload = await apiRequest<unknown>("/api/research/screener/control", {
      method: "POST",
      json: { enabled, overrides },
    });
    if (!isRecord(payload) || payload.status !== "ok" || !isNonEmptyString(payload.note)) {
      throw new ScreenerControlError(200, "Screener returned an invalid save response");
    }
    return payload as unknown as ScreenerControlSaveResult;
  } catch (err) {
    if (err instanceof ApiError) {
      const detail = err.detail?.slice(0, 300) || err.message;
      throw new ScreenerControlError(err.status, detail);
    }
    throw err;
  }
}
