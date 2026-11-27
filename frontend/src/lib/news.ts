import { apiRequest, getJson } from "./api";

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
function nullableFiniteNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}
function nullableNewsValue(value: unknown): boolean {
  return value === null || isString(value) || isFiniteNumber(value);
}
function validPerformance(value: unknown): value is NewsPerformance {
  if (!isRecord(value) || !isCount(value.closed_trades) || !isCount(value.wins) ||
      !isCount(value.losses) || !nullableFiniteNumber(value.win_rate) ||
      !nullableFiniteNumber(value.expectancy_r) || !nullableFiniteNumber(value.profit_factor) ||
      !isFiniteNumber(value.total_r) || !isFiniteNumber(value.net_pnl) ||
      !isFiniteNumber(value.max_drawdown_r)) return false;
  const winRate = value.win_rate;
  return winRate === null || (isFiniteNumber(winRate) && winRate >= 0 && winRate <= 100);
}
function validHeartbeat(value: unknown): value is NewsHeartbeat {
  return isRecord(value) && isNonEmptyString(value.collector_name) &&
    isNonEmptyString(value.status) && nullableString(value.detail) &&
    nullableString(value.last_beat_time);
}
function validStats(value: unknown): value is NewsStats {
  if (!isRecord(value) || !isCount(value.total_events_collected) ||
      !isCount(value.events_evaluated_by_ai) || !isCount(value.high_confidence_signals) ||
      !nullableFiniteNumber(value.avg_confidence) || !isCount(value.trades_opened) ||
      !isCount(value.pending_signals) || !validPerformance(value.performance) ||
      !Array.isArray(value.heartbeats) || !value.heartbeats.every(validHeartbeat)) return false;
  const avgConfidence = value.avg_confidence;
  return avgConfidence === null || (isFiniteNumber(avgConfidence) && avgConfidence >= 0 && avgConfidence <= 100);
}
function validNewsSignal(value: unknown): value is NewsSignal {
  if (!isRecord(value) || !isCount(value.id) || !isNonEmptyString(value.symbol) ||
      !nullableString(value.market) || !nullableString(value.timeframe) ||
      !nullableString(value.bias) || !nullableFiniteNumber(value.confidence) ||
      !nullableString(value.reasoning) || !nullableString(value.invalidation_condition) ||
      !nullableString(value.time_horizon) || !nullableString(value.trade_timing) ||
      typeof value.acted_on !== "boolean" ||
      !(value.paper_position_id === null || isCount(value.paper_position_id)) ||
      !nullableString(value.skip_reason) || !nullableString(value.created_at) ||
      !nullableString(value.headline) || !nullableString(value.url) ||
      !nullableString(value.source) || !nullableString(value.category) ||
      !nullableString(value.country) || !nullableString(value.impact) ||
      !nullableString(value.scheduled_at) || !nullableNewsValue(value.actual_value) ||
      !nullableNewsValue(value.forecast_value) || !nullableNewsValue(value.previous_value)) return false;
  const confidence = value.confidence;
  return confidence === null || (isFiniteNumber(confidence) && confidence >= 0 && confidence <= 100);
}
function validNewsEvent(value: unknown): value is NewsEvent {
  return isRecord(value) && isCount(value.id) &&
    nullableString(value.source) && nullableString(value.category) &&
    nullableString(value.headline) && nullableString(value.url) &&
    nullableString(value.symbols) && nullableString(value.country) &&
    nullableString(value.impact) && nullableString(value.published_at) &&
    nullableString(value.scheduled_at) && nullableNewsValue(value.actual_value) &&
    nullableNewsValue(value.forecast_value) && nullableNewsValue(value.previous_value) &&
    nullableString(value.processed_at) && nullableString(value.fetched_at);
}

/** Read-only News AI data. Validate persisted payloads; this does not start collectors, call AI, or execute trades. */
export async function fetchNewsSnapshot(signal?: AbortSignal): Promise<NewsSnapshot> {
  const [stats, signals, events] = await Promise.all([
    getJson<unknown>("/api/news/stats", signal),
    getJson<unknown>("/api/news/signals?limit=50", signal),
    getJson<unknown>("/api/news/events?limit=30", signal),
  ]);
  if (!validStats(stats)) throw new Error("News stats returned an invalid response");
  if (!isRecord(signals) || !Array.isArray(signals.signals) || !signals.signals.every(validNewsSignal)) {
    throw new Error("News signals returned an invalid response");
  }
  if (!isRecord(events) || !Array.isArray(events.events) || !events.events.every(validNewsEvent)) {
    throw new Error("News events returned an invalid response");
  }
  return { stats, signals: signals.signals, events: events.events };
}

export type NewsAISettingKey =
  | "min_confidence" | "max_trades_per_day"
  | "stop_atr_mult" | "tp_r_scalp" | "tp_r_intraday" | "tp_r_swing"
  | "poll_interval_seconds" | "execution_poll_interval_seconds";

export type NewsAISettingsPatch = Partial<Record<NewsAISettingKey, number | null>>;

export interface NewsAISettings extends Partial<Record<NewsAISettingKey, number>> {
  min_confidence: number;
  max_trades_per_day: number;
  source: "default" | "override";
  defaults: Partial<Record<NewsAISettingKey, number>>;
  overrides?: Partial<Record<NewsAISettingKey, number>>;
  updated_by: string | null;
  updated_at: string | null;
}

export const NEWS_MIN_CONFIDENCE_FLOOR = 50;
export const NEWS_MAX_TRADES_CEILING = 20;

function validSettings(value: unknown): value is NewsAISettings {
  return isRecord(value) && Number.isFinite(value.min_confidence) && Number.isFinite(value.max_trades_per_day)
    && (value.source === "default" || value.source === "override") && isRecord(value.defaults)
    && Object.values(value.defaults).every(Number.isFinite)
    && (value.overrides === undefined || (isRecord(value.overrides) && Object.values(value.overrides).every(Number.isFinite)));
}

export async function fetchNewsAISettings(signal?: AbortSignal): Promise<NewsAISettings> {
  const data = await getJson<unknown>("/api/news/settings", signal);
  if (!validSettings(data)) throw new Error("News AI settings returned an invalid response");
  return data;
}

export async function updateNewsAISettings(changes: NewsAISettingsPatch): Promise<void> {
  await apiRequest("/api/news/settings", { method: "PUT", json: changes });
}

export async function resetNewsAISettings(): Promise<void> {
  await apiRequest("/api/news/settings", { method: "DELETE" });
}

/** Finalize a pending signal as dismissed so the execution worker never trades it. */
export async function dismissNewsSignal(id: number): Promise<void> {
  await apiRequest(`/api/news/signals/${id}/dismiss`, { method: "POST" });
}
