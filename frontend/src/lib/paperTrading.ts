import { getJson } from "./api";

export interface PaperPosition { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; current_price: number; stop_loss: number | null; take_profit: number | null; quantity: number; unrealized_pnl_pct: number | null; unrealized_r: number | null; allocated_capital: number; status: string; last_update_time: string | null }
export interface PaperConfig { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; is_active: boolean; allocated_capital: number; risk_per_trade_pct: number; telegram_enabled: boolean; report_enabled: boolean; created_at: string }
export interface PaperOrder { id: number; symbol: string; market: string; timeframe: string; side: string; order_type: string; status: string; quantity?: number; price?: number | null; created_at: string; filled_at?: string | null; state_reason?: string | null }
export interface PaperTrade { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; exit_price: number; net_pnl: number; net_return_pct: number; r_multiple: number; exit_reason: string; entry_time_wib: string; exit_time_wib: string }
export interface PaperMetrics { totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number; byStrategy: Array<{ strategy: string; totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number }> }
export interface PaperCircuitBreaker { status: string; emergency_stop_active: boolean; emergency_stop_reason: string | null; active_positions: number; max_concurrent_positions: number; today_realized_pnl_usd: number | null; max_daily_loss_usd: number; open_notional_usd: number | null; max_total_notional_usd: number; can_open_new_position: boolean; message: string }
export interface PaperTradingSnapshot { positions: PaperPosition[]; configs: PaperConfig[]; orders: PaperOrder[]; trades: PaperTrade[]; metrics: PaperMetrics; breaker: PaperCircuitBreaker }

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
function optionalNullableFinite(record: Record<string, unknown>, key: string): boolean {
  return !(key in record) || nullableFinite(record[key]);
}
function validPosition(value: unknown): boolean {
  if (!isRecord(value) || !positiveId(value.id)) return false;
  if (!["symbol", "market", "timeframe", "strategy_name", "direction", "status"].every((key) => requiredText(value[key]))) return false;
  if (!["entry_price", "current_price", "quantity", "allocated_capital"].every((key) => finite(value[key]))) return false;
  if (!["stop_loss", "take_profit", "unrealized_pnl_pct", "unrealized_r"].every((key) => nullableFinite(value[key]))) return false;
  return optionalNullableFinite(value, "entry_time") &&
    nullableText(value.last_update_time);
}
function validConfig(value: unknown): boolean {
  return isRecord(value) && positiveId(value.id) &&
    ["symbol", "market", "timeframe", "strategy_name", "created_at"].every((key) => requiredText(value[key])) &&
    typeof value.is_active === "boolean" &&
    finite(value.allocated_capital) && finite(value.risk_per_trade_pct) &&
    typeof value.telegram_enabled === "boolean" && typeof value.report_enabled === "boolean";
}
function validOrder(value: unknown): boolean {
  if (!isRecord(value) || !positiveId(value.id)) return false;
  if (!["symbol", "market", "timeframe", "side", "order_type", "status", "created_at"].every((key) => requiredText(value[key]))) return false;
  if (!optionalNullableFinite(value, "price") || !optionalNullableFinite(value, "quantity")) return false;
  return (!("filled_at" in value) || nullableText(value.filled_at)) &&
    (!("state_reason" in value) || nullableText(value.state_reason));
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
