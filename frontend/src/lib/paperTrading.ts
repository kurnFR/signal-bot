import { getJson, apiRequest, ApiError, type ApiRequestOptions } from "./api";
import { parseTelegramDeliveryStatus, type TelegramDeliveryOutcome } from "./telegram";
export { parseTelegramDeliveryStatus, type TelegramDeliveryOutcome };

export interface PaperPosition { funding_pnl?: number | null; id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; current_price: number; stop_loss: number | null; take_profit: number | null; quantity: number; unrealized_pnl_pct: number | null; unrealized_r: number | null; allocated_capital: number; status: string; last_update_time: number }
export interface PaperConfig { strategy_params?: Record<string, string | number | boolean> | null; id: number; symbol: string; market: string; timeframe: string; strategy_name: string; is_active: boolean; allocated_capital: number; risk_per_trade_pct: number; telegram_enabled: boolean; report_enabled: boolean; archived_at?: string | null; realized_equity?: number; ml_model_id?: string | null; ml_generator_model_id?: string | null; created_at: string }
export interface PaperOrder { id: number; config_id: number; symbol: string; market: string; timeframe: string; strategy_name: string; signal_open_time: number; entry_time: number; direction: string; requested_entry_price: number; stop_loss: number; take_profit: number; atr_at_signal: number; ml_model_id: string | null; ml_probability: number | null; status: string; state_reason: string | null; fill_price: number | null; position_id: number | null; created_at: string; updated_at: string; filled_at: string | null; terminal_at: string | null }
export interface PaperTrade { funding_pnl?: number | null; id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; exit_price: number; net_pnl: number; net_return_pct: number; r_multiple: number; exit_reason: string; entry_time_wib: string; exit_time_wib: string }
export interface PaperMetricsExitReasonRow { exitReason: string; trades: number; wins: number; losses: number; netPnl: number; sumR: number }
export interface PaperMetrics {
  totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number;
  pureTrades?: number; pureWins?: number; pureLosses?: number; pureWinRatePct?: number; pureNetPnl?: number; pureSumR?: number; pureCumulativeR?: number; pureExpectancyR?: number; avgWinR?: number; avgLossR?: number;
  exitReasonBreakdown?: PaperMetricsExitReasonRow[];
  byStrategy: Array<{ strategy: string; symbol?: string | null; market?: string; timeframe?: string; configId?: number | null; totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number; pureTrades?: number; pureWins?: number; pureLosses?: number; pureWinRatePct?: number; pureNetPnl?: number; pureSumR?: number; pureExpectancyR?: number; avgWinR?: number; avgLossR?: number }>
  byStrategySymbol?: Array<{ strategy: string; symbol?: string | null; totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number; pureTrades?: number; pureWinRatePct?: number; pureNetPnl?: number; pureExpectancyR?: number }>
}

/** Exit mode of a paper deployment, from its stored strategy_params.
 * Pure TP/SL applies only to NEW trades; open positions keep the mode
 * they were opened with. */
export function paperExitModeLabel(strategyParams: PaperConfig["strategy_params"]): "Pure TP/SL" | "Dynamic" {
  return strategyParams?.PURE_TP_SL === true ? "Pure TP/SL" : "Dynamic";
}
export interface PaperCircuitBreaker { status: string; emergency_stop_active: boolean; emergency_stop_reason: string | null; active_positions: number; max_concurrent_positions: number; today_realized_pnl_usd: number | null; max_daily_loss_usd: number; open_notional_usd: number | null; max_total_notional_usd: number; can_open_new_position: boolean; message: string }
export interface PaperTradingSnapshot { positions: PaperPosition[]; configs: PaperConfig[]; orders: PaperOrder[]; trades: PaperTrade[]; metrics: PaperMetrics; breaker: PaperCircuitBreaker }

export interface UpdatePaperConfigRequest {
  create_only?: boolean;
  symbol: string;
  market: string;
  timeframe: string;
  strategy_name: string;
  is_active: boolean;
  allocated_capital: number;
  risk_per_trade_pct: number;
  ml_model_id?: string | null;
  ml_generator_model_id?: string | null;
  telegram_enabled: boolean;
  report_enabled: boolean;
  params?: Record<string, unknown> | null;
}

export interface GeneratorModelSummary {
  model_id: string;
  experiment_id: string;
  model_type: string;
  symbol?: string | null;
  market?: string | null;
  timeframe?: string | null;
  feature_columns?: string[];
  created_at?: string;
  eligible_for_paper: boolean;
  eligibility: { eligible: boolean; reasons: string[] };
  test_metrics?: Record<string, unknown>;
}

export interface UpdatePaperConfigResponse {
  status: string;
  config?: unknown;
}

export interface DeployPaperStrategyRequest {
  symbol: string;
  market: "spot" | "futures";
  timeframe: "15m" | "1h" | "4h" | "1d";
  strategy_name: string;
  allocated_capital: number;
  risk_per_trade_pct: number;
  send_telegram: boolean;
  rank?: number | null;
  rank_score?: number | null;
  ml_model_id?: string | null;
  params?: Record<string, unknown> | null;
}

export interface DeployPaperStrategyResponse {
  status: "success" | "partial";
  message: string;
  config: unknown;
  telegramAlert?: unknown;
  telegram?: unknown;
  syncResult?: unknown;
}

export class PaperTradingActionError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "PaperTradingActionError";
    this.status = status;
  }
}

async function paperMutationRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  try {
    const opts: ApiRequestOptions = {
      method: (options.method as any) ?? "GET",
      headers: options.headers as Record<string, string> | undefined,
      signal: options.signal ?? undefined,
    };
    if (options.body !== undefined) {
      if (typeof options.body === "string") {
        try {
          opts.json = JSON.parse(options.body);
        } catch {
          opts.body = options.body;
        }
      } else {
        opts.body = options.body;
      }
    }
    return await apiRequest<T>(path, opts);
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.status === 401) {
        throw new PaperTradingActionError(401, "Your session has expired. Sign in again.");
      }
      if (err.code === "invalid-content-type") {
        throw new PaperTradingActionError(502, "Server returned a non-JSON response");
      }
      throw new PaperTradingActionError(err.status, err.message);
    }
    throw err;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function nullableFinite(value: unknown): boolean {
  return value === null || finite(value);
}
function validMetricRow(value: unknown): boolean {
  if (!isRecord(value) || typeof value.strategy !== "string") return false;
  return ["totalTrades", "wins", "losses", "winRatePct", "cumulativeR", "expectancyR", "netPnl"].every((key) => finite(value[key]));
}

function positiveId(value: unknown): boolean {
  return Number.isSafeInteger(value) && (value as number) > 0;
}
function requiredText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function nullableText(value: unknown): boolean {
  return value === null || typeof value === "string";
}
function validPosition(value: unknown): boolean {
  if (!isRecord(value) || !positiveId(value.id)) return false;
  if (!["symbol", "market", "timeframe", "strategy_name", "direction", "status"].every((key) => requiredText(value[key]))) return false;
  if (!["entry_price", "current_price", "quantity", "allocated_capital"].every((key) => finite(value[key]))) return false;
  if (!["stop_loss", "take_profit", "unrealized_pnl_pct", "unrealized_r"].every((key) => nullableFinite(value[key]))) return false;
  return finite(value.entry_time) && finite(value.last_update_time);
}
function validConfig(value: unknown): boolean {
  return isRecord(value) && positiveId(value.id) &&
    ["symbol", "market", "timeframe", "strategy_name", "created_at"].every((key) => requiredText(value[key])) &&
    typeof value.is_active === "boolean" &&
    (value.strategy_params == null || (isRecord(value.strategy_params) && Object.values(value.strategy_params).every(
      (item) => typeof item === "string" || typeof item === "boolean" || finite(item),
    ))) &&
    finite(value.allocated_capital) && finite(value.risk_per_trade_pct) &&
    typeof value.telegram_enabled === "boolean" && typeof value.report_enabled === "boolean";
}
function validOrder(value: unknown): boolean {
  if (!isRecord(value) || !positiveId(value.id) || !positiveId(value.config_id)) return false;
  if (!["symbol", "market", "timeframe", "strategy_name", "direction", "status", "created_at"].every((key) => requiredText(value[key]))) return false;
  if (!["signal_open_time", "entry_time"].every((key) => finite(value[key]))) return false;
  if (!["requested_entry_price", "stop_loss", "take_profit", "atr_at_signal", "ml_probability", "fill_price"].every((key) => nullableFinite(value[key]))) return false;
  return nullableText(value.state_reason) && nullableText(value.filled_at);
}

function validTrade(value: unknown): boolean {
  if (!isRecord(value) || !positiveId(value.id)) return false;
  if (!["symbol", "market", "timeframe", "strategy_name", "direction", "exit_reason", "entry_time_wib", "exit_time_wib"].every((key) => typeof value[key] === "string")) return false;
  return ["entry_price", "exit_price", "net_pnl", "net_return_pct", "r_multiple"].every((key) => finite(value[key]));
}
function validRows(value: unknown, key: string, validator: (row: unknown) => boolean): boolean {
  return isRecord(value) && Array.isArray(value[key]) && value[key].every(validator);
}

function validMetrics(value: unknown): value is PaperMetrics {
  if (!isRecord(value) || !Array.isArray(value.byStrategy)) return false;
  return ["totalTrades", "wins", "losses", "winRatePct", "cumulativeR", "expectancyR", "netPnl"].every((key) => finite(value[key])) &&
    value.byStrategy.every(validMetricRow);
}
function validBreaker(value: unknown): value is PaperCircuitBreaker {
  if (!isRecord(value) || typeof value.status !== "string" || typeof value.emergency_stop_active !== "boolean" ||
      !(value.emergency_stop_reason === null || typeof value.emergency_stop_reason === "string") ||
      typeof value.can_open_new_position !== "boolean" || typeof value.message !== "string") return false;
  if (!["active_positions", "max_concurrent_positions", "max_daily_loss_usd", "max_total_notional_usd"].every((key) => finite(value[key]))) return false;
  return nullableFinite(value.today_realized_pnl_usd) && nullableFinite(value.open_notional_usd);
}

/** Read-only paper trading overview. No order, position, or worker mutations are exposed. */
export async function fetchPaperTradingSnapshot(signal?: AbortSignal): Promise<PaperTradingSnapshot> {
  const [positions, configs, orders, trades, metrics, breaker] = await Promise.all([
    getJson<{ positions: PaperPosition[] }>("/api/paper/positions", signal),
    getJson<{ configs: PaperConfig[] }>("/api/paper/configs", signal),
    getJson<{ orders: PaperOrder[] }>("/api/paper/orders?limit=20", signal),
    getJson<{ trades: PaperTrade[] }>("/api/paper/trades?limit=20", signal),
    getJson<{ metrics: PaperMetrics }>("/api/paper/metrics", signal),
    getJson<PaperCircuitBreaker>("/api/paper/circuit-breakers", signal),
  ]);
  if (!validRows(positions, "positions", validPosition) ||
      !validRows(configs, "configs", validConfig) ||
      !validRows(orders, "orders", validOrder) ||
      !validRows(trades, "trades", validTrade) ||
      !isRecord(metrics) || !validMetrics(metrics.metrics) || !validBreaker(breaker)) {
    throw new Error("Paper trading API returned an invalid response; risk status is not verified");
  }
  return { positions: positions.positions, configs: configs.configs, orders: orders.orders, trades: trades.trades, metrics: metrics.metrics, breaker };
}

/** Cancel a pending paper order. Protected by actor attribution and transition conflict detection. */
export async function cancelPaperOrder(orderId: number, signal?: AbortSignal): Promise<{ status: string; order_id: number }> {
  if (!positiveId(orderId)) {
    throw new PaperTradingActionError(400, "Invalid paper order ID");
  }
  const payload = await paperMutationRequest<unknown>(`/api/paper/orders/${orderId}/cancel`, {
    method: "POST",
    signal,
  });
  if (!isRecord(payload) || payload.status !== "cancelled" || payload.order_id !== orderId) {
    throw new PaperTradingActionError(502, "Server returned an invalid cancellation response");
  }
  return { status: "cancelled", order_id: orderId };
}

/** Manually close an active paper position at simulated market price. */
export async function closePaperPosition(positionId: number, signal?: AbortSignal): Promise<Record<string, unknown>> {
  if (!positiveId(positionId)) {
    throw new PaperTradingActionError(400, "Invalid paper position ID");
  }
  const payload = await paperMutationRequest<unknown>(`/api/paper/positions/${positionId}/close`, {
    method: "POST",
    signal,
  });
  if (!isRecord(payload)) {
    throw new PaperTradingActionError(502, "Server returned an invalid position close response");
  }
  return payload;
}

/** Deploy a strategy to live paper trading with explicit risk and optional Telegram preferences. */
export async function deployPaperStrategy(
  req: DeployPaperStrategyRequest,
  signal?: AbortSignal,
): Promise<DeployPaperStrategyResponse> {
  if (!requiredText(req.symbol) || !requiredText(req.strategy_name)) {
    throw new PaperTradingActionError(400, "Symbol and strategy name are required");
  }
  if (!finite(req.allocated_capital) || req.allocated_capital < 100) {
    throw new PaperTradingActionError(400, "Allocated capital must be at least $100");
  }
  if (!finite(req.risk_per_trade_pct) || req.risk_per_trade_pct < 0.1 || req.risk_per_trade_pct > 10.0) {
    throw new PaperTradingActionError(400, "Risk per trade must be between 0.1% and 10.0%");
  }
  const payload = await paperMutationRequest<unknown>("/api/paper/deploy", {
    method: "POST",
    body: JSON.stringify({
      symbol: req.symbol.trim().toUpperCase(),
      market: req.market,
      timeframe: req.timeframe,
      strategy_name: req.strategy_name.trim(),
      allocated_capital: req.allocated_capital,
      risk_per_trade_pct: req.risk_per_trade_pct,
      send_telegram: req.send_telegram,
      rank: req.rank ?? undefined,
      rank_score: req.rank_score ?? undefined,
      ml_model_id: req.ml_model_id ?? undefined,
      params: req.params ?? undefined,
    }),
    signal,
  });
  if (!isRecord(payload) || (payload.status !== "success" && payload.status !== "partial") || typeof payload.message !== "string") {
    throw new PaperTradingActionError(502, "Server returned an invalid deployment response");
  }
  const telegramStatus = payload.telegramAlert !== undefined ? payload.telegramAlert : payload.telegram;
  return {
    status: payload.status,
    message: payload.message,
    config: payload.config,
    telegramAlert: telegramStatus,
    telegram: telegramStatus,
    syncResult: payload.syncResult,
  };
}

/** Trip the emergency stop circuit breaker to halt new paper entries immediately. */
export async function tripPaperCircuitBreaker(
  reason = "Manual emergency stop via Web Dashboard",
  signal?: AbortSignal,
): Promise<{ status: string; message: string; breaker: PaperCircuitBreaker }> {
  const query = new URLSearchParams({ reason });
  const payload = await paperMutationRequest<unknown>(`/api/paper/circuit-breakers/emergency-stop?${query.toString()}`, {
    method: "POST",
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success" || !validBreaker(payload.breaker)) {
    throw new PaperTradingActionError(502, "Server returned an invalid circuit breaker trip response");
  }
  return { status: "success", message: String(payload.message || reason), breaker: payload.breaker };
}

/** Reset the emergency stop circuit breaker to re-enable new paper entries. */
export async function resetPaperCircuitBreaker(
  signal?: AbortSignal,
): Promise<{ status: string; message: string; breaker: PaperCircuitBreaker }> {
  const payload = await paperMutationRequest<unknown>("/api/paper/circuit-breakers/reset", {
    method: "POST",
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success" || !validBreaker(payload.breaker)) {
    throw new PaperTradingActionError(502, "Server returned an invalid circuit breaker reset response");
  }
  return { status: "success", message: String(payload.message || "Circuit breaker reset"), breaker: payload.breaker };
}

/** Update an existing paper strategy configuration (activate/pause, toggle telegram, toggle report). */
export async function updatePaperConfig(
  req: UpdatePaperConfigRequest,
  signal?: AbortSignal,
): Promise<UpdatePaperConfigResponse> {
  if (!requiredText(req.symbol) || !requiredText(req.strategy_name)) {
    throw new PaperTradingActionError(400, "Symbol and strategy name are required");
  }
  if (!finite(req.allocated_capital) || req.allocated_capital <= 0) {
    throw new PaperTradingActionError(400, "Allocated capital must be positive");
  }
  if (!finite(req.risk_per_trade_pct) || req.risk_per_trade_pct <= 0) {
    throw new PaperTradingActionError(400, "Risk per trade must be positive");
  }

  const payload = await paperMutationRequest<unknown>("/api/paper/configs", {
    method: "POST",
    body: JSON.stringify({
      symbol: req.symbol.trim().toUpperCase(),
      market: req.market.trim().toLowerCase(),
      timeframe: req.timeframe.trim(),
      strategy_name: req.strategy_name.trim(),
      is_active: Boolean(req.is_active),
      allocated_capital: req.allocated_capital,
      risk_per_trade_pct: req.risk_per_trade_pct,
      ml_model_id: req.ml_model_id ?? null,
      ml_generator_model_id: req.ml_generator_model_id ?? null,
      telegram_enabled: Boolean(req.telegram_enabled),
      report_enabled: Boolean(req.report_enabled),
      params: req.params ?? undefined,
      create_only: req.create_only,
    }),
    signal,
  });

  if (!isRecord(payload) || payload.status !== "success") {
    throw new PaperTradingActionError(502, "Server returned an invalid configuration update response");
  }
  return { status: "success", config: payload.config };
}

export async function fetchGeneratorModels(signal?: AbortSignal): Promise<GeneratorModelSummary[]> {
  const payload = await getJson<{ models: GeneratorModelSummary[] }>("/api/ml/generator-models", signal);
  if (!isRecord(payload) || !Array.isArray(payload.models)) {
    throw new Error("Invalid generator models response");
  }
  return payload.models;
}


export async function updatePaperReportPreference(
  scope: { symbol?: string; strategy_name?: string },
  field: "report_enabled" | "telegram_enabled",
  enabled: boolean,
): Promise<number> {
  const payload = await paperMutationRequest<unknown>("/api/paper/configs/preferences", {
    method: "POST",
    body: JSON.stringify({ ...scope, [field]: enabled }),
  });
  if (!isRecord(payload) || payload.status !== "success" || !finite(payload.updated)) {
    throw new PaperTradingActionError(502, "Server returned an invalid preference update response");
  }
  return payload.updated as number;
}

export async function archivePaperConfig(id: number): Promise<void> {
  await paperMutationRequest(`/api/paper/configs/${id}`, { method: "DELETE" });
}

/** Hard-delete a deployment with no ledger history. */
export async function purgePaperConfig(id: number, allowUnarchived = false): Promise<void> {
  if (allowUnarchived) {
    await paperMutationRequest(`/api/paper/configs/${id}/permanent?allow_unarchived=true`, { method: "DELETE" });
  } else {
    await paperMutationRequest(`/api/paper/configs/${id}/permanent`, { method: "DELETE" });
  }
}

export type PaperDeploymentSettings = Partial<Pick<PaperConfig,
  "is_active" | "allocated_capital" | "risk_per_trade_pct" | "telegram_enabled" | "report_enabled" | "ml_model_id" | "ml_generator_model_id"
>> & { params?: PaperConfig["strategy_params"] };

export async function patchPaperDeployment(id: number, settings: PaperDeploymentSettings): Promise<void> {
  await paperMutationRequest(`/api/paper/configs/${id}`, { method: "PATCH", body: JSON.stringify(settings) });
}

export async function fetchPaperReportForPeriod(days: number, signal: AbortSignal): Promise<PaperMetrics> {
  const payload = await getJson<{ metrics: PaperMetrics }>(`/api/paper/metrics?days=${days}`, signal);
  if (!isRecord(payload) || !validMetrics(payload.metrics)) throw new Error("Invalid paper performance response");
  return payload.metrics;
}

export type BatchPaperConfigAction =
  | "activate_all"
  | "pause_all"
  | "telegram_all_on"
  | "telegram_all_off"
  | "report_all_on"
  | "report_all_off";

export interface BatchPaperConfigResponse {
  status: string;
  strategy: string;
  action: string;
  updated: number;
}

/** Batch-update all pair configurations belonging to a single strategy. */
export async function batchUpdatePaperStrategy(
  strategyName: string,
  action: BatchPaperConfigAction,
  signal?: AbortSignal,
): Promise<BatchPaperConfigResponse> {
  if (!requiredText(strategyName)) {
    throw new PaperTradingActionError(400, "Strategy name is required");
  }
  const payload = await paperMutationRequest<unknown>("/api/paper/configs/batch", {
    method: "POST",
    body: JSON.stringify({ strategy_name: strategyName.trim(), action }),
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success" || !finite(payload.updated)) {
    throw new PaperTradingActionError(502, "Server returned an invalid batch update response");
  }
  return {
    status: "success",
    strategy: typeof payload.strategy === "string" ? payload.strategy : strategyName.trim(),
    action: typeof payload.action === "string" ? payload.action : action,
    updated: payload.updated,
  };
}

/** Manually trigger a paper trading market tick sync and strategy evaluation. */
export async function syncPaperMarketTick(signal?: AbortSignal): Promise<{ status: string; results: unknown }> {
  const payload = await paperMutationRequest<unknown>("/api/paper/sync", {
    method: "POST",
    signal,
  });
  if (!isRecord(payload) || payload.status !== "success") {
    throw new PaperTradingActionError(502, "Server returned an invalid sync response");
  }
  return { status: "success", results: payload.results };
}
