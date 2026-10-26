import { useEffect, useState } from "react";
import { Activity, AlertTriangle, RefreshCw, ShieldCheck, WalletCards } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchPaperTradingSnapshot, type PaperTradingSnapshot } from "./lib/paperTrading";

const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
function dateLabel(value: string | null | undefined) { if (!value) return "—"; const parsed = Date.parse(value); return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value; }
function pnlClass(value: number | null | undefined) { return value == null || value === 0 ? "" : value > 0 ? "paper-positive" : "paper-negative"; }

export function PaperTradingPanel() {
  const [data, setData] = useState<PaperTradingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    fetchPaperTradingSnapshot(controller.signal).then((result) => { if (!controller.signal.aborted) setData(result); }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) setError("Your session has expired. Sign in again to view paper trading data.");
      else if (cause instanceof ApiError && cause.status === 403) setError("Your account cannot view paper trading data.");
      else setError("Paper trading data could not be loaded. Check API and database health, then retry.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const m = data?.metrics;
  const breaker = data?.breaker;
  const metric = (label: string, value: string, note: string, icon: React.ReactNode, className = "") => <article className="metric-card" key={label}><div className="metric-top"><span>{label}</span><span className="metric-icon">{icon}</span></div><div className={"metric-value " + className}>{value}</div><div className="metric-foot"><span>{note}</span></div></article>;
  return <div className="paper-trading-view" aria-busy={loading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only paper trading monitor</strong><p>This view only reads the existing ledger and risk-status APIs. It cannot place/cancel orders, close positions, deploy strategies, sync ticks, or change safety controls.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Paper Trading</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="Paper trading performance">
      {metric("Net realized PnL", m ? money.format(m.netPnl) : loading ? "Loading…" : "—", "Report-enabled strategies", <Activity size={17} />, pnlClass(m?.netPnl))}
      {metric("Win rate", m ? `${number.format(m.winRatePct)}%` : loading ? "Loading…" : "—", m ? `${m.wins} wins · ${m.losses} losses` : "Closed paper trades", <Activity size={17} />)}
      {metric("Expectancy", m ? `${number.format(m.expectancyR)} R` : loading ? "Loading…" : "—", "Average R per closed trade", <Activity size={17} />, pnlClass(m?.expectancyR))}
      {metric("Open positions", data ? String(data.positions.length) : loading ? "Loading…" : "—", `${data?.configs.filter((item) => item.is_active).length ?? "—"} active configs`, <WalletCards size={17} />)}
    </section>
    <section className="panel" aria-labelledby="paper-risk-title"><div className="panel-heading"><div><h2 id="paper-risk-title">Risk guard status</h2><p>Read-only view of the paper engine’s current entry-safety checks.</p></div><span className={"status-pill " + (breaker?.can_open_new_position ? "status-positive" : "status-warning")}>{breaker?.can_open_new_position ? "Entries allowed" : breaker ? "Entries blocked" : "Status unavailable"}</span></div>
      {breaker ? <><div className="paper-risk-grid"><div><span>Breaker state</span><strong>{breaker.status}</strong></div><div><span>Emergency stop</span><strong>{breaker.emergency_stop_active ? "Active" : "Inactive"}</strong></div><div><span>Open positions</span><strong>{breaker.active_positions} / {breaker.max_concurrent_positions}</strong></div><div><span>Today realized PnL</span><strong className={pnlClass(breaker.today_realized_pnl_usd)}>{breaker.today_realized_pnl_usd == null ? "Unavailable" : money.format(breaker.today_realized_pnl_usd)}</strong></div><div><span>Open notional</span><strong>{breaker.open_notional_usd == null ? "Unavailable" : money.format(breaker.open_notional_usd)}</strong></div><div><span>Max daily loss</span><strong>{money.format(breaker.max_daily_loss_usd)}</strong></div></div><p className={"paper-breaker-message " + (breaker.can_open_new_position ? "" : "paper-breaker-warning")}>{!breaker.can_open_new_position && <AlertTriangle size={15} />}{breaker.message}</p></> : <p className="empty-state">{loading ? "Loading risk guard…" : "Risk guard status unavailable."}</p>}
    </section>
    <section className="panel" aria-labelledby="paper-positions-title"><div className="panel-heading"><div><h2 id="paper-positions-title">Open positions</h2><p>Current mark, risk levels, and unrealized performance.</p></div><span className="panel-counter">{data?.positions.length ?? "—"} POSITIONS</span></div>
      {loading && !data ? <p className="empty-state" role="status">Loading open positions…</p> : data?.positions.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th>Symbol / strategy</th><th>Side</th><th>Entry</th><th>Mark</th><th>Stop / target</th><th>Unrealized R</th><th>Updated</th></tr></thead><tbody>{data.positions.map((p) => <tr key={p.id}><td><strong>{p.symbol}</strong><small>{p.strategy_name} · {p.market} · {p.timeframe}</small></td><td>{p.direction}</td><td>{number.format(p.entry_price)}</td><td>{number.format(p.current_price)}</td><td>{p.stop_loss == null ? "—" : number.format(p.stop_loss)} / {p.take_profit == null ? "—" : number.format(p.take_profit)}</td><td className={pnlClass(p.unrealized_r)}>{p.unrealized_r == null ? "—" : `${number.format(p.unrealized_r)} R`}</td><td>{dateLabel(p.last_update_time)}</td></tr>)}</tbody></table></div> : <p className="empty-state">No open paper positions.</p>}
    </section>
    <section className="panel" aria-labelledby="paper-config-title"><div className="panel-heading"><div><h2 id="paper-config-title">Strategy configurations</h2><p>Existing activation, capital, Telegram, and reporting preferences.</p></div><span className="panel-counter">{data?.configs.length ?? "—"} CONFIGS</span></div>
      {data?.configs.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th>Symbol / strategy</th><th>Market</th><th>State</th><th>Capital</th><th>Risk / trade</th><th>Telegram</th><th>Report</th></tr></thead><tbody>{data.configs.map((c) => <tr key={c.id}><td><strong>{c.symbol}</strong><small>{c.strategy_name} · {c.timeframe}</small></td><td>{c.market}</td><td><span className={"status-pill " + (c.is_active ? "status-positive" : "status-neutral")}>{c.is_active ? "Active" : "Inactive"}</span></td><td>{money.format(c.allocated_capital)}</td><td>{number.format(c.risk_per_trade_pct)}%</td><td>{c.telegram_enabled ? "Enabled" : "Disabled"}</td><td>{c.report_enabled ? "Included" : "Hidden"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading strategy configurations…" : "No paper strategy configurations found."}</p>}
    </section>
    <section className="panel" aria-labelledby="paper-orders-title"><div className="panel-heading"><div><h2 id="paper-orders-title">Recent orders</h2><p>Latest durable paper-order ledger entries. No order actions are available here.</p></div><span className="panel-counter">{data?.orders.length ?? "—"} ORDERS</span></div>
      {data?.orders.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th>Symbol</th><th>Side / type</th><th>Status</th><th>Price</th><th>Created</th><th>Reason</th></tr></thead><tbody>{data.orders.map((o) => <tr key={o.id}><td><strong>{o.symbol}</strong><small>{o.market} · {o.timeframe}</small></td><td>{o.side} / {o.order_type}</td><td><span className={"status-pill " + (o.status === "FILLED" ? "status-positive" : o.status === "PENDING" ? "status-warning" : "status-neutral")}>{o.status}</span></td><td>{o.price == null ? "Market / unset" : number.format(o.price)}</td><td>{dateLabel(o.created_at)}</td><td>{o.state_reason || "—"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading recent orders…" : "No recent orders."}</p>}
    </section>
    <section className="panel" aria-labelledby="paper-trades-title"><div className="panel-heading"><div><h2 id="paper-trades-title">Recent closed trades</h2><p>Ledger values use the backend’s canonical net PnL and R-multiple calculations.</p></div><span className="panel-counter">{data?.trades.length ?? "—"} TRADES</span></div>
      {data?.trades.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th>Symbol / strategy</th><th>Direction</th><th>Entry → exit</th><th>Net PnL</th><th>Return</th><th>R multiple</th><th>Exit reason</th></tr></thead><tbody>{data.trades.map((t) => <tr key={t.id}><td><strong>{t.symbol}</strong><small>{t.strategy_name} · {t.market} · {t.timeframe}</small></td><td>{t.direction}</td><td>{number.format(t.entry_price)} → {number.format(t.exit_price)}</td><td className={pnlClass(t.net_pnl)}>{money.format(t.net_pnl)}</td><td className={pnlClass(t.net_return_pct)}>{number.format(t.net_return_pct)}%</td><td className={pnlClass(t.r_multiple)}>{number.format(t.r_multiple)} R</td><td>{t.exit_reason || "—"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading closed trades…" : "No closed paper trades."}</p>}
    </section>
    <div className="paper-refresh-row"><span>Refreshes on demand; no background polling or worker action.</span><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)} disabled={loading}><RefreshCw size={15} /> Refresh snapshot</button></div>
  </div>;
}
