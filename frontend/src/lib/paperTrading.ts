import { getJson } from "./api";

export interface PaperPosition { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; current_price: number; stop_loss: number | null; take_profit: number | null; quantity: number; unrealized_pnl_pct: number | null; unrealized_r: number | null; allocated_capital: number; status: string; last_update_time: string | null }
export interface PaperConfig { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; is_active: boolean; allocated_capital: number; risk_per_trade_pct: number; telegram_enabled: boolean; report_enabled: boolean; created_at: string }
export interface PaperOrder { id: number; symbol: string; market: string; timeframe: string; side: string; order_type: string; status: string; quantity?: number; price?: number | null; created_at: string; filled_at?: string | null; state_reason?: string | null }
export interface PaperTrade { id: number; symbol: string; market: string; timeframe: string; strategy_name: string; direction: string; entry_price: number; exit_price: number; net_pnl: number; net_return_pct: number; r_multiple: number; exit_reason: string; entry_time_wib: string; exit_time_wib: string }
export interface PaperMetrics { totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number; byStrategy: Array<{ strategy: string; totalTrades: number; wins: number; losses: number; winRatePct: number; cumulativeR: number; expectancyR: number; netPnl: number }> }
export interface PaperCircuitBreaker { status: string; emergency_stop_active: boolean; emergency_stop_reason: string | null; active_positions: number; max_concurrent_positions: number; today_realized_pnl_usd: number | null; max_daily_loss_usd: number; open_notional_usd: number | null; max_total_notional_usd: number; can_open_new_position: boolean; message: string }
export interface PaperTradingSnapshot { positions: PaperPosition[]; configs: PaperConfig[]; orders: PaperOrder[]; trades: PaperTrade[]; metrics: PaperMetrics; breaker: PaperCircuitBreaker }

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
  return { positions: positions.positions, configs: configs.configs, orders: orders.orders, trades: trades.trades, metrics: metrics.metrics, breaker };
}
