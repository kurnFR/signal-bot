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
    return !needle || [item.symbol, item.headline, item.sentiment, item.impact_horizon].some((value) => value.toLowerCase().includes(needle));
  }), [data, filter]);

  return <div className="news-ai-view" aria-busy={loading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only News AI overlay</strong><p>Inspect persisted sentiment metrics, processed news signals, and macro/crypto events. This view does not trigger polling, AI inference, strategy execution, or Telegram alerts.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load News AI</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="News sentiment summary">
      <article className="metric-card"><div className="metric-top"><span>Bullish signals</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.bullish_count)}</div><div className="metric-foot"><span>Recent sentiment distribution</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Bearish signals</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.bearish_count)}</div><div className="metric-foot"><span>Recent sentiment distribution</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Neutral signals</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.neutral_count)}</div><div className="metric-foot"><span>Recent sentiment distribution</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Average confidence</span><span className="metric-icon"><Newspaper size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.stats.average_confidence)}</div><div className="metric-foot"><span>Active overlay multiplier: {data ? num(data.stats.active_overlay_multiplier) : "—"}</span></div></article>
    </section>

    <section className="panel" aria-labelledby="news-signals-title"><div className="panel-heading"><div><h2 id="news-signals-title">News AI signals</h2><p>Processed sentiment overlay signals from persisted records.</p></div><span className="panel-counter">{signals.length} ROWS</span></div>
      <label className="filter-field">Filter signals <input aria-label="Filter News AI signals" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, headline, sentiment…" /></label>
      {signals.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Headline</th><th>Sentiment</th><th>Confidence</th><th>Horizon</th><th>Published</th></tr></thead><tbody>{signals.map((row) => <tr key={row.id}><td>{row.symbol}</td><td>{row.headline}</td><td>{row.sentiment}</td><td>{num(row.confidence)}</td><td>{row.impact_horizon}</td><td>{dateLabel(row.published_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading signals…" : "No signals match this filter."}</p>}
    </section>

    <section className="panel" aria-labelledby="news-events-title"><div className="panel-heading"><div><h2 id="news-events-title">Macro & crypto events</h2><p>Recent collected events; the view does not start collectors or fetch new articles.</p></div><span className="panel-counter">{data?.events.length ?? 0} ROWS</span></div>
      {data?.events.length ? <div className="table-scroll"><table><thead><tr><th>Category</th><th>Event</th><th>Importance</th><th>Detected</th></tr></thead><tbody>{data.events.map((row) => <tr key={row.id}><td>{row.category}</td><td>{row.event_text}</td><td>{row.importance}</td><td>{dateLabel(row.detected_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading events…" : "No recent events returned."}</p>}
    </section>
  </div>;
}
