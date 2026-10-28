import { useEffect, useMemo, useState } from "react";
import { Activity, Newspaper, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchNewsSnapshot, type NewsSnapshot } from "./lib/news";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
function num(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "—";
}
function dateLabel(value: string | null | undefined) {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view News AI data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view News AI data.";
  return "News AI data could not be loaded. Check API/database health, then retry.";
}

export function NewsAIPanel() {
  const [data, setData] = useState<NewsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchNewsSnapshot(controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  const signals = useMemo(() => (data?.signals ?? []).filter((item) => {
    const needle = filter.trim().toLowerCase();
    return !needle || [item.symbol, item.headline, item.bias, item.time_horizon, item.source]
      .some((value) => String(value ?? "").toLowerCase().includes(needle));
  }), [data, filter]);

  return <div className="news-ai-view" aria-busy={loading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only News AI overlay</strong><p>Inspect persisted collection metrics, AI decisions, execution outcomes, collector heartbeats, signals, and events. This view does not trigger polling, AI inference, strategy execution, or Telegram alerts.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load News AI</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="News AI operational summary">
      <article className="metric-card"><div className="metric-top"><span>Collected events</span><span className="metric-icon"><Newspaper size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.total_events_collected)}</div><div className="metric-foot"><span>Persisted news and calendar records</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Events evaluated</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.events_evaluated_by_ai)}</div><div className="metric-foot"><span>Records marked processed</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>AI signals</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.high_confidence_signals)}</div><div className="metric-foot"><span>Persisted signal records</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Average confidence</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.avg_confidence)}</div><div className="metric-foot"><span>Stored confidence average</span></div></article>
    </section>
    {data && <section className="panel" aria-labelledby="news-performance-title"><div className="panel-heading"><div><h2 id="news-performance-title">Paper performance</h2><p>Realized results for closed <code>news_ai_overlay</code> trades only.</p></div></div><div className="metrics-grid">
      <article className="metric-card"><div className="metric-top"><span>Closed trades</span></div><div className="metric-value">{num(data.stats.performance.closed_trades)}</div></article>
      <article className="metric-card"><div className="metric-top"><span>Win rate</span></div><div className="metric-value">{data.stats.performance.win_rate == null ? "—" : `${num(data.stats.performance.win_rate)}%`}</div></article>
      <article className="metric-card"><div className="metric-top"><span>Profit factor</span></div><div className="metric-value">{num(data.stats.performance.profit_factor)}</div></article>
      <article className="metric-card"><div className="metric-top"><span>Total R</span></div><div className="metric-value">{num(data.stats.performance.total_r)}</div></article>
    </div></section>}
    <section className="panel" aria-labelledby="news-signals-title"><div className="panel-heading"><div><h2 id="news-signals-title">News AI signals</h2><p>Stored AI decisions joined with the event context where available.</p></div><span className="panel-counter">{signals.length} ROWS</span></div>
      <label className="filter-field">Filter signals <input aria-label="Filter News AI signals" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, headline, bias, source…" /></label>
      {signals.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Symbol</th><th scope="col">Headline</th><th scope="col">Bias</th><th scope="col">Confidence</th><th scope="col">Horizon</th><th scope="col">Decision</th><th scope="col">Created</th></tr></thead><tbody>{signals.map((row) => <tr key={row.id}><td>{row.symbol}<div>{[row.market, row.timeframe].filter(Boolean).join(" · ")}</div></td><td>{row.url ? <a href={row.url} target="_blank" rel="noreferrer">{row.headline ?? "Open source"}</a> : row.headline ?? "—"}<div>{row.source ?? "—"}</div></td><td>{row.bias ?? "—"}</td><td>{num(row.confidence)}</td><td>{row.time_horizon ?? "—"}</td><td>{row.acted_on ? "Acted on" : row.skip_reason ? `Skipped: ${row.skip_reason}` : "Pending"} </td><td>{dateLabel(row.created_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading signals…" : "No signals match this filter."}</p>}
    </section>
    <section className="panel" aria-labelledby="news-events-title"><div className="panel-heading"><div><h2 id="news-events-title">Macro & crypto events</h2><p>Collected event feed; this view does not start collectors or fetch new articles.</p></div><span className="panel-counter">{data?.events.length ?? 0} ROWS</span></div>
      {data?.events.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Category</th><th scope="col">Headline</th><th scope="col">Impact</th><th scope="col">Symbols</th><th scope="col">Published</th><th scope="col">Fetched</th></tr></thead><tbody>{data.events.map((row) => <tr key={row.id}><td>{row.category ?? "—"}<div>{row.source ?? "—"}</div></td><td>{row.url ? <a href={row.url} target="_blank" rel="noreferrer">{row.headline ?? "Open source"}</a> : row.headline ?? "—"}</td><td>{row.impact ?? "—"}</td><td>{row.symbols ?? "—"}</td><td>{dateLabel(row.published_at)}</td><td>{dateLabel(row.fetched_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading events…" : "No recent events returned."}</p>}
    </section>
    {data && <section className="panel" aria-labelledby="news-heartbeats-title"><div className="panel-heading"><div><h2 id="news-heartbeats-title">Collector health</h2><p>Last recorded heartbeat for each News AI process.</p></div></div>{data.stats.heartbeats.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Collector</th><th scope="col">Status</th><th scope="col">Detail</th><th scope="col">Last heartbeat</th></tr></thead><tbody>{data.stats.heartbeats.map((item) => <tr key={item.collector_name}><td>{item.collector_name}</td><td>{item.status}</td><td>{item.detail ?? "—"}</td><td>{dateLabel(item.last_beat_time)}</td></tr>)}</tbody></table></div> : <p className="empty-state">No collector heartbeat records returned.</p>}</section>}
  </div>;
}
