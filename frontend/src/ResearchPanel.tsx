import { useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchResearchSnapshot, type ResearchSnapshot } from "./lib/research";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
function value(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return numberFormat.format(value);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return "—";
}
function dateLabel(value: string | null | undefined) {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view Research data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view Research data.";
  return "Research data could not be loaded. Check API/database health, then retry.";
}

export function ResearchPanel() {
  const [data, setData] = useState<ResearchSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchResearchSnapshot(controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  const strategies = useMemo(() => (data?.strategies ?? []).filter((row) => {
    const needle = filter.trim().toLowerCase();
    return !needle || row.strategy_id.toLowerCase().includes(needle) || row.mode.toLowerCase().includes(needle);
  }), [data, filter]);

  return <div className="research-view" aria-busy={loading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only Research monitor</strong><p>Review RetailBot2 positions, closed trades, strategy lifecycle flags, and saved performance summaries. This preview does not change strategy evaluation, paper tracking, Telegram alerts, or report visibility; those controls remain in the legacy dashboard.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Research</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="Research summary">
      <article className="metric-card"><div className="metric-top"><span>Open positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.positions.length ?? "—"}</div><div className="metric-foot"><span>Active RetailBot2 trades</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Recent closed trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.trades.length ?? "—"}</div><div className="metric-foot"><span>Latest 50 returned by API</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Registered strategy rows</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.strategies.length ?? "—"}</div><div className="metric-foot"><span>Shadow and inverse modes</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Telegram-enabled rows</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.strategies.filter((row) => row.telegram_enabled).length ?? "—"}</div><div className="metric-foot"><span>Persisted lifecycle readback</span></div></article>
    </section>

    <section className="panel" aria-labelledby="research-performance-title">
      <div className="panel-heading"><div><h2 id="research-performance-title">Performance by mode</h2><p>Realized results and open-trade counts from the saved Research stats API.</p></div><span className="panel-counter">{data ? 2 : 0} MODES</span></div>
      {loading && !data ? <p className="empty-state">Loading performance…</p> : <div className="table-scroll"><table><thead><tr><th>Mode</th><th>Realized PnL</th><th>Closed trades</th><th>Wins</th><th>Win rate</th><th>Open trades</th></tr></thead><tbody>
        {(["shadow", "inverse"] as const).map((mode) => {
          const stats = data?.stats.by_mode[mode];
          return <tr key={mode}><td>{mode}</td><td>{stats ? value(stats.realized_pnl) : "—"}</td><td>{stats ? value(stats.closed_count) : "—"}</td><td>{stats ? value(stats.wins) : "—"}</td><td>{stats?.win_rate == null ? "—" : `${value(stats.win_rate)}%`}</td><td>{stats ? value(stats.open_count) : "—"}</td></tr>;
        })}
      </tbody></table></div>}
    </section>

    <section className="panel" aria-labelledby="research-strategy-performance-title">
      <div className="panel-heading"><div><h2 id="research-strategy-performance-title">Performance by strategy</h2><p>Closed-trade aggregates after report-visibility filtering.</p></div><span className="panel-counter">{data?.stats.by_strategy.length ?? 0} ROWS</span></div>
      {loading && !data ? <p className="empty-state">Loading strategy performance…</p> : data?.stats.by_strategy.length ? <div className="table-scroll"><table><thead><tr><th>Strategy</th><th>Mode</th><th>Closed trades</th><th>Wins</th><th>Losses</th><th>Realized PnL</th><th>Last updated</th></tr></thead><tbody>
        {data.stats.by_strategy.map((row) => <tr key={row.strategy + row.mode}><td>{row.strategy}</td><td>{row.mode}</td><td>{value(row.total_trades)}</td><td>{value(row.wins)}</td><td>{value(row.losses)}</td><td>{value(row.total_pnl)}</td><td>{dateLabel(row.last_updated)}</td></tr>)}
      </tbody></table></div> : <p className="empty-state">No strategy performance rows returned.</p>}
    </section>

    <section className="panel" aria-labelledby="research-positions-title"><div className="panel-heading"><div><h2 id="research-positions-title">Active positions</h2><p>Current open Research Bot positions from the existing API.</p></div><span className="panel-counter">{data?.positions.length ?? 0} ROWS</span></div>
      {data?.positions.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Mode</th><th>Entry</th><th>Current</th><th>Unrealized PnL</th><th>Opened</th></tr></thead><tbody>{data.positions.map((row) => <tr key={row.id}><td>{row.symbol}</td><td>{row.mode}</td><td>{value(row.entry_price)}</td><td>{value(row.current_price)}</td><td>{value(row.unrealized_pnl)}</td><td>{dateLabel(row.opened_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading positions…" : "No active Research positions returned."}</p>}
    </section>

    <section className="panel" aria-labelledby="research-trades-title"><div className="panel-heading"><div><h2 id="research-trades-title">Recent closed trades</h2><p>Historical trades returned by the Research API.</p></div><span className="panel-counter">{data?.trades.length ?? 0} ROWS</span></div>
      {data?.trades.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Side</th><th>Mode</th><th>Entry</th><th>Exit</th><th>Realized PnL</th><th>Closed</th></tr></thead><tbody>{data.trades.map((row) => <tr key={row.id}><td>{row.symbol}</td><td>{row.side}</td><td>{row.mode}</td><td>{value(row.entry_price)}</td><td>{value(row.exit_price)}</td><td>{value(row.realized_pnl)}</td><td>{dateLabel(row.closed_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading trades…" : "No closed trades returned."}</p>}
    </section>

    <section className="panel" aria-labelledby="research-strategies-title"><div className="panel-heading"><div><h2 id="research-strategies-title">Strategy lifecycle snapshot</h2><p>Read-only view of persisted evaluation, paper, Telegram, and report flags.</p></div><span className="panel-counter">{strategies.length} ROWS</span></div>
      <label className="filter-field">Filter strategies <input aria-label="Filter strategies" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Strategy ID or mode" /></label>
      {strategies.length ? <div className="table-scroll"><table><thead><tr><th>Strategy</th><th>Mode</th><th>Evaluation</th><th>Paper</th><th>Telegram</th><th>Report</th><th>Auto-promote</th></tr></thead><tbody>{strategies.map((row) => <tr key={row.strategy_id + row.mode}><td>{row.strategy_id}</td><td>{row.mode}</td><td>{row.evaluation_enabled ? "On" : "Off"}</td><td>{row.paper_enabled ? "On" : "Off"}</td><td>{row.telegram_enabled ? "On" : "Off"}</td><td>{row.report_enabled ? "Visible" : "Hidden"}</td><td>{row.auto_promote_enabled ? "On" : "Unavailable"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading strategies…" : "No strategies match this filter."}</p>}
    </section>
  </div>;
}
