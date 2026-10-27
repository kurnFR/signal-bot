import { useEffect, useMemo, useState } from "react";
import { Database, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchMasterDataSnapshot, type MasterDataSnapshot } from "./lib/masterData";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
function num(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "—";
}
function dateLabel(value: string | null | undefined) {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value;
}
function jobDate(value: string | number | undefined) {
  if (typeof value === "number") return dateLabel(new Date(value * 1000).toISOString());
  return dateLabel(value);
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view Master Data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view Master Data.";
  return "Master Data could not be loaded. Check API/database health, then retry.";
}

export function MasterDataPanel() {
  const [data, setData] = useState<MasterDataSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchMasterDataSnapshot(controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  const coverage = useMemo(() => (data?.coverage ?? []).filter((row) => {
    const needle = filter.trim().toLowerCase();
    return !needle || [row.symbol, row.market, row.timeframe].some((part) => part.toLowerCase().includes(needle));
  }), [data, filter]);

  return <div className="master-data-view" aria-busy={loading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only Master Data</strong><p>Review stored OHLCV coverage, computed feature availability, and background job status. Backfill and feature-build actions can write data or launch workers, so they remain unavailable in this preview.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Master Data</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="Master Data summary">
      <article className="metric-card"><div className="metric-top"><span>Total candles</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.totalCandles)}</div><div className="metric-foot"><span>Persisted OHLCV rows</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Distinct symbols</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.distinctSymbols)}</div><div className="metric-foot"><span>Across stored datasets</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Coverage entries</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.coverageEntries)}</div><div className="metric-foot"><span>Symbol / market / timeframe</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Jobs listed</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.jobs.length)}</div><div className="metric-foot"><span>Recent persisted and live status</span></div></article>
    </section>
    <section className="panel" aria-labelledby="master-coverage-title"><div className="panel-heading"><div><h2 id="master-coverage-title">Dataset coverage</h2><p>Stored candle counts and feature availability from the existing API.</p></div><span className="panel-counter">{coverage.length} ROWS</span></div>
      <label className="filter-field">Filter datasets <input aria-label="Filter datasets" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, market, timeframe…" /></label>
      {coverage.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Market</th><th>Timeframe</th><th>Candles</th><th>Features</th><th>First date</th><th>Last date</th></tr></thead><tbody>{coverage.map((row) => <tr key={row.symbol + row.market + row.timeframe}><td>{row.symbol}</td><td>{row.market}</td><td>{row.timeframe}</td><td>{num(row.candleCount)}</td><td>{row.hasFeatures ? "Available" : "Missing"} ({num(row.featureCount)})</td><td>{dateLabel(row.startDate)}</td><td>{dateLabel(row.endDate)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading datasets…" : "No datasets match this filter."}</p>}
    </section>
    <section className="panel" aria-labelledby="master-jobs-title"><div className="panel-heading"><div><h2 id="master-jobs-title">Recent background jobs</h2><p>Read-only job history; no job is started or resumed here.</p></div><span className="panel-counter">{data?.jobs.length ?? 0} ROWS</span></div>
      {data?.jobs.length ? <div className="table-scroll"><table><thead><tr><th>Job</th><th>Dataset</th><th>Status</th><th>Progress</th><th>Created</th><th>Message</th></tr></thead><tbody>{data.jobs.map((row, index) => <tr key={row.id ?? row.job_id ?? index}><td>{row.type}</td><td>{[row.symbol, row.market, row.timeframe].filter(Boolean).join(" · ") || "—"}</td><td>{row.status}</td><td>{typeof row.progress === "number" ? num(row.progress) + "%" : "—"}</td><td>{jobDate(row.created_at)}</td><td>{row.message ?? row.error ?? "—"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading jobs…" : "No recent background jobs returned."}</p>}
    </section>
  </div>;
}
