import { useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchScreenerSnapshot, type ScreenerSnapshot, type ScreenerSignal } from "./lib/screener";

const compactNumber = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const priceNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 8 });
const dateTime = (value: string | null | undefined) => {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
};

export function ScreenerPanel() {
  const [snapshot, setSnapshot] = useState<ScreenerSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchScreenerSnapshot(controller.signal).then(setSnapshot).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) setError("Sign in through the existing dashboard first.");
      else if (cause instanceof ApiError && cause.status === 403) setError("Your account cannot view screener data.");
      else setError("Screener data could not be loaded. Check the API and database status, then retry.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const signals = useMemo(() => {
    const normalized = query.trim().toUpperCase();
    return (snapshot?.signals ?? []).filter((item) => !normalized || item.symbol.toUpperCase().includes(normalized) || item.signal_type.toUpperCase().includes(normalized));
  }, [snapshot, query]);
  const status = snapshot?.status;
  const stat = snapshot?.stats;
  const pill = (ok: boolean) => "status-pill " + (ok ? "status-positive" : "status-warning");
  return <>
    <section className="metrics-grid screener-metrics" aria-label="Smart money screener status" aria-busy={loading}>
      <article className="metric-card"><div className="metric-top"><span>Worker process</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !snapshot ? "Loading…" : status?.running ? "Running" : "Offline"}</div><div className="metric-foot"><span className={pill(Boolean(status?.running))}>{status?.running ? "Heartbeat fresh" : "No fresh heartbeat"}</span><span>{status?.timeframe ?? "Timeframe —"}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Telegram alerts</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{loading && !snapshot ? "Loading…" : status?.enabled ? "Enabled" : "Paused"}</div><div className="metric-foot"><span className={pill(Boolean(status?.enabled))}>{status?.enabled ? "Gate open" : "Gate paused"}</span><span>Read only</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Symbols monitored</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{status?.symbols_monitored ?? (loading ? "Loading…" : "—")}</div><div className="metric-foot"><span className={pill(Boolean(status?.database_ok))}>{status?.database_ok ? "Database available" : "Database unavailable"}</span><span>Current universe</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Signals · 24 hours</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{stat?.signals_last_24h ?? (loading ? "Loading…" : "—")}</div><div className="metric-foot"><span className="status-pill status-neutral">{stat?.total_signals ?? "—"} total</span><span>Stored signal history</span></div></article>
    </section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Market Screener V2</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="panel" aria-labelledby="screener-signal-title">
      <div className="panel-heading"><div><h2 id="screener-signal-title">Recent smart-money signals</h2><p>Latest persisted signals returned by the existing read-only screener API.</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      <div className="screener-toolbar"><label htmlFor="screener-filter">Filter signals</label><div className="screener-search"><Search size={16} aria-hidden="true" /><input id="screener-filter" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Symbol or signal type" /></div><span className="panel-counter">{signals.length} SHOWN</span></div>
      {loading && !snapshot ? <p className="empty-state" role="status">Loading recent signals…</p> : signals.length ? <div className="status-table-wrap"><table className="status-table screener-table"><thead><tr><th scope="col">Symbol</th><th scope="col">Signal</th><th scope="col">Price</th><th scope="col">24h quote vol.</th><th scope="col">RVOL</th><th scope="col">Velocity</th><th scope="col">Quality</th><th scope="col">Telegram</th><th scope="col">Detected</th></tr></thead><tbody>{signals.map((item: ScreenerSignal, index) => <tr key={item.id ?? item.symbol + "-" + item.detected_at + "-" + index}><td className="screener-symbol">{item.symbol}</td><td>{item.signal_type}</td><td>{priceNumber.format(item.price)}</td><td>${compactNumber.format(item.quote_volume)}</td><td>{Number(item.rvol).toFixed(2)}×</td><td>{Number(item.volume_velocity).toFixed(2)}×</td><td><span className={pill(Number(item.quality_score) >= 70)}>{Math.round(Number(item.quality_score))}</span></td><td><span className={pill(item.telegram_sent)}>{item.telegram_sent ? "Sent" : "Not sent"}</span></td><td className="heartbeat-time">{dateTime(item.detected_at)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{error ? "Signal rows are unavailable until the API request succeeds." : query ? "No signals match this filter." : "No recent smart-money signals were returned."}</p>}
      {status?.last_signal_at && <p className="screener-footnote">Last worker signal timestamp: {dateTime(status.last_signal_at)}. This is worker metadata, not a promise that an alert was delivered.</p>}
    </section>
    <p className="screener-safety-note"><ShieldCheck size={15} /> Read-only view. Alert settings and screener controls remain in the legacy dashboard until their mutation contracts and permissions are migrated separately.</p>
  </>;
}
