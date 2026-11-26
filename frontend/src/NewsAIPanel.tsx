import { useEffect, useMemo, useState } from "react";
import { Activity, Newspaper, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { useAuth } from "./AuthGate";
import { NewsAISettingsCard } from "./components/NewsAISettingsCard";
import { dismissNewsSignal, fetchNewsSnapshot, type NewsSnapshot } from "./lib/news";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
function num(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "—";
}
const newsDateFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta", day: "2-digit", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
function NewsTime({ value, kind }: { value: string | null | undefined; kind?: string }) {
  const raw = value?.trim();
  if (!raw) return <>—</>;
  const iso = raw.replace(" ", "T");
  const timestamp = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : iso + "Z");
  if (!Number.isFinite(timestamp)) return <>{raw}</>;
  return <><time dateTime={new Date(timestamp).toISOString()}>{newsDateFormat.format(timestamp)}</time>{kind && <small>{kind}</small>}</>;
}

function categoryLabel(value: string | null) {
  if (value === "macro_calendar" || value === "macro") return "Economic calendar";
  if (value === "crypto" || value === "crypto_news") return "Crypto news";
  return value ? value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()) : "—";
}

function sourceLabel(value: string | null) {
  if (value?.toLowerCase() === "forexfactory") return "Forex Factory";
  return value?.replaceAll("_", " ") ?? "—";
}

function Headline({ url, text }: { url: string | null; text: string | null }) {
  return url && /^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noreferrer">{text ?? "Open source"}</a> : <strong>{text ?? "—"}</strong>;
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
  const auth = useAuth();
  const role = (auth?.user.role ?? "").toLowerCase();
  const isAdmin = role === "admin";
  const canDismiss = role === "admin" || role === "trader";
  const [dismissingId, setDismissingId] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const dismiss = async (id: number) => {
    setDismissingId(id); setActionError(null);
    try {
      await dismissNewsSignal(id);
      setConfirmId(null);
      setAttempt((n) => n + 1);
    } catch (cause) {
      setConfirmId(null);
      setActionError(cause instanceof Error ? cause.message : "Could not dismiss the signal.");
    } finally { setDismissingId(null); }
  };
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
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>News AI overlay</strong><p>Inspect persisted collection metrics, AI decisions, execution outcomes, collector heartbeats, signals, and events. This view does not trigger polling, AI inference, strategy execution, or Telegram alerts. Traders and admins can dismiss pending signals; admins tune the guardrails below.</p></div><span className="panel-counter">OPERATOR CONTROLS</span></section>
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
    <section className="panel" aria-labelledby="news-signals-title">
      <div className="panel-heading">
        <div><h2 id="news-signals-title">News AI signals</h2><p>Saved AI decisions and their source events. Times in WIB.</p></div>
        <span className="panel-counter">{signals.length} signals</span>
      </div>
      <label className="filter-field">Filter signals <input aria-label="Filter News AI signals" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, headline, bias, source…" /></label>
      {actionError && <div className="api-error" role="alert">{actionError}</div>}
      {signals.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="News AI signals table">
        <table className="news-table" aria-labelledby="news-signals-title">
          <thead><tr><th scope="col">Symbol</th><th scope="col">Headline</th><th scope="col">Bias</th><th scope="col">Confidence</th><th scope="col">Horizon</th><th scope="col">Decision</th><th scope="col">Created</th>{canDismiss && <th scope="col">Action</th>}</tr></thead>
          <tbody>{signals.map((row) => <tr key={row.id}>
            <td><strong>{row.symbol}</strong>{(row.market || row.timeframe) && <small>{[row.market, row.timeframe].filter(Boolean).join(" · ")}</small>}</td>
            <td className="news-headline"><Headline url={row.url} text={row.headline} />{row.source && <small>{sourceLabel(row.source)}</small>}</td>
            <td>{row.bias ?? "—"}</td>
            <td>{num(row.confidence)}</td>
            <td>{row.time_horizon ?? "—"}</td>
            <td className="news-detail">{row.skip_reason === "dismissed_by_operator" ? <strong>Dismissed</strong> : row.acted_on ? "Acted on" : row.skip_reason ? <><strong>Skipped</strong><small>{row.skip_reason}</small></> : "Pending"}</td>
            <td className="news-date"><NewsTime value={row.created_at} /></td>
            {canDismiss && <td>{!row.acted_on && !row.skip_reason ? (confirmId === row.id
              ? <div role="group" aria-label={`Confirm dismissing ${row.symbol} signal`}><button className="button button-danger" type="button" disabled={dismissingId === row.id} onClick={() => void dismiss(row.id)}>Confirm dismiss {row.symbol}</button>{" "}<button className="button button-secondary" type="button" onClick={() => setConfirmId(null)}>Cancel</button></div>
              : <button className="button button-secondary" type="button" disabled={dismissingId !== null} onClick={() => setConfirmId(row.id)}>Dismiss {row.symbol} signal</button>) : "—"}</td>}
          </tr>)}</tbody>
        </table>
      </div> : <p className="empty-state">{loading ? "Loading signals…" : "No signals match this filter."}</p>}
    </section>
    <NewsAISettingsCard isAdmin={isAdmin} />
    <section className="panel" aria-labelledby="news-events-title">
      <div className="panel-heading">
        <div><h2 id="news-events-title">Macro &amp; crypto events</h2><p>Economic releases and crypto headlines. Times in WIB.</p></div>
        <span className="panel-counter">{data?.events.length ?? 0} events</span>
      </div>
      {data?.events.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Macro and crypto events table">
        <table className="news-table" aria-labelledby="news-events-title">
          <thead><tr><th scope="col">Headline</th><th scope="col">Category</th><th scope="col">Impact</th><th scope="col">Symbols</th><th scope="col">Event time</th><th scope="col">Fetched</th></tr></thead>
          <tbody>{data.events.map((row) => <tr key={row.id}>
            <td className="news-headline"><Headline url={row.url} text={row.headline} /></td>
            <td>{categoryLabel(row.category)}{row.source && <small>{sourceLabel(row.source)}</small>}</td>
            <td>{row.impact ? <span className={`status-pill ${row.impact.toLowerCase() === "high" ? "status-warning" : "status-neutral"}`}>{row.impact.replace(/^./, (letter) => letter.toUpperCase())}</span> : "—"}</td>
            <td>{row.symbols ?? "—"}</td>
            <td className="news-date"><NewsTime value={row.scheduled_at ?? row.published_at} kind={row.scheduled_at ? "Scheduled" : "Published"} /></td>
            <td className="news-date"><NewsTime value={row.fetched_at} /></td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="empty-state">{loading ? "Loading events…" : "No recent events returned."}</p>}
    </section>
    {data && <section className="panel" aria-labelledby="news-heartbeats-title">
      <div className="panel-heading"><div><h2 id="news-heartbeats-title">Collector health</h2><p>Last recorded heartbeat for each News AI process. Times in WIB.</p></div></div>
      {data.stats.heartbeats.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="News collector health table">
        <table className="news-table" aria-labelledby="news-heartbeats-title">
          <thead><tr><th scope="col">Collector</th><th scope="col">Status</th><th scope="col">Detail</th><th scope="col">Last heartbeat</th></tr></thead>
          <tbody>{data.stats.heartbeats.map((item) => <tr key={item.collector_name}>
            <td><strong>{item.collector_name}</strong></td><td>{item.status}</td>
            <td className="news-detail">{item.detail ?? "—"}</td>
            <td className="news-date"><NewsTime value={item.last_beat_time} /></td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="empty-state">No collector heartbeat records returned.</p>}
    </section>}
  </div>;
}
