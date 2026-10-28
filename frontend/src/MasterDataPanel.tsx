import { useEffect, useMemo, useState } from "react";
import { useAuth } from "./AuthGate";
import { Database, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchMasterDataSnapshot, MasterDataActionError, triggerMasterDataBackfill, triggerMasterDataFeatureBuild, type MasterDataSnapshot } from "./lib/masterData";

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
  const auth = useAuth();
  const canManageJobs = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [backfillSymbol, setBackfillSymbol] = useState("BTCUSDT");
  const [backfillMarket, setBackfillMarket] = useState<"spot" | "futures">("spot");
  const [backfillTimeframe, setBackfillTimeframe] = useState("1h");
  const [backfillYears, setBackfillYears] = useState("1");
  const [autoComputeFeatures, setAutoComputeFeatures] = useState(true);
  const [pendingAction, setPendingAction] = useState<
    | { kind: "backfill"; symbol: string; market: "spot" | "futures"; timeframe: string; years: number; auto_compute_features: boolean }
    | { kind: "features"; symbol: string; market: "spot" | "futures"; timeframe: string }
    | null
  >(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
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

  const reviewBackfill = () => {
    const symbol = backfillSymbol.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{1,30}$/.test(symbol)) { setActionError("Enter a valid symbol using letters, numbers, dot, underscore, or hyphen."); return; }
    const years = Number(backfillYears);
    if (!Number.isFinite(years) || years < 0.01 || years > 10) { setActionError("Backfill years must be between 0.01 and 10."); return; }
    setActionError(null); setActionMessage(null);
    setPendingAction({ kind: "backfill", symbol, market: backfillMarket, timeframe: backfillTimeframe, years, auto_compute_features: autoComputeFeatures });
  };

  const confirmPendingAction = async () => {
    if (!pendingAction) return;
    setActionBusy(true); setActionError(null); setActionMessage(null);
    try {
      const job = pendingAction.kind === "backfill"
        ? await triggerMasterDataBackfill({ symbol: pendingAction.symbol, market: pendingAction.market, timeframe: pendingAction.timeframe, years: pendingAction.years, auto_compute_features: pendingAction.auto_compute_features })
        : await triggerMasterDataFeatureBuild({ symbol: pendingAction.symbol, market: pendingAction.market, timeframe: pendingAction.timeframe });
      setPendingAction(null);
      setActionMessage(`${pendingAction.kind === "backfill" ? "Backfill" : "Feature build"} job queued (${job.id}).`);
      setAttempt((value) => value + 1);
    } catch (cause) {
      if (cause instanceof MasterDataActionError && cause.status === 403) setActionError("Your account cannot start Master Data jobs. Only admin or trader roles can perform these actions.");
      else setActionError(cause instanceof Error ? cause.message : "Unable to start the Master Data job. Please retry.");
    } finally { setActionBusy(false); }
  };

  return <div className="master-data-view" aria-busy={loading || actionBusy}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only Master Data</strong><p>Review stored OHLCV coverage, computed feature availability, and background job status. Backfill and feature-build actions can write data or launch workers, so they remain unavailable in this preview.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Master Data</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {actionError && <div className="api-error" role="alert"><div><strong>Master Data action failed</strong><p>{actionError}</p></div><button className="button button-secondary" onClick={() => setActionError(null)}>Dismiss</button></div>}
    {actionMessage && <p className="control-feedback control-success" role="status">{actionMessage}</p>}
    {canManageJobs ? <section className="panel" aria-labelledby="master-backfill-title">
      <div className="panel-heading"><div><h2 id="master-backfill-title">Backfill historical data</h2><p>Starts a background job that writes OHLCV rows and can optionally compute features.</p></div><span className="panel-counter">ADMIN / TRADER</span></div>
      <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); reviewBackfill(); }}>
        <label className="screener-control-field">Symbol<input aria-label="Backfill symbol" value={backfillSymbol} maxLength={30} required onChange={(event) => setBackfillSymbol(event.target.value)} /></label>
        <label className="screener-control-field">Market<select aria-label="Backfill market" value={backfillMarket} onChange={(event) => setBackfillMarket(event.target.value as "spot" | "futures")}><option value="spot">Spot</option><option value="futures">Futures</option></select></label>
        <label className="screener-control-field">Timeframe<select aria-label="Backfill timeframe" value={backfillTimeframe} onChange={(event) => setBackfillTimeframe(event.target.value)}>{["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label className="screener-control-field">Years (0.01–10)<input aria-label="Backfill years" type="number" min="0.01" max="10" step="0.01" value={backfillYears} required onChange={(event) => setBackfillYears(event.target.value)} /></label>
        <label className="screener-alert-toggle"><input type="checkbox" checked={autoComputeFeatures} onChange={(event) => setAutoComputeFeatures(event.target.checked)} /><span><strong>Compute features after backfill</strong><small>Feature calculation may take additional time.</small></span></label>
        <div><button className="button button-primary" type="submit" disabled={actionBusy || loading}>Review backfill</button></div>
      </form>
    </section> : <p className="empty-state">Backfill and feature-build controls are restricted to admin and trader roles. This session is view-only.</p>}
    <section className="metrics-grid" aria-label="Master Data summary">
      <article className="metric-card"><div className="metric-top"><span>Total candles</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.totalCandles)}</div><div className="metric-foot"><span>Persisted OHLCV rows</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Distinct symbols</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.distinctSymbols)}</div><div className="metric-foot"><span>Across stored datasets</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Coverage entries</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.summary.coverageEntries)}</div><div className="metric-foot"><span>Symbol / market / timeframe</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Jobs listed</span><span className="metric-icon"><Database size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : num(data?.jobs.length)}</div><div className="metric-foot"><span>Recent persisted and live status</span></div></article>
    </section>
    <section className="panel" aria-labelledby="master-coverage-title"><div className="panel-heading"><div><h2 id="master-coverage-title">Dataset coverage</h2><p>Stored candle counts and feature availability from the existing API.</p></div><span className="panel-counter">{coverage.length} ROWS</span></div>
      <label className="filter-field">Filter datasets <input aria-label="Filter datasets" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, market, timeframe…" /></label>
      {coverage.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Market</th><th>Timeframe</th><th>Candles</th><th>Features</th><th>First date</th><th>Last date</th>{canManageJobs && <th>Actions</th>}</tr></thead><tbody>{coverage.map((row) => <tr key={row.symbol + row.market + row.timeframe}><td>{row.symbol}</td><td>{row.market}</td><td>{row.timeframe}</td><td>{num(row.candleCount)}</td><td>{row.hasFeatures ? "Available" : "Missing"} ({num(row.featureCount)})</td><td>{dateLabel(row.startDate)}</td><td>{dateLabel(row.endDate)}</td>{canManageJobs && <td>{(row.market === "spot" || row.market === "futures") && <button className="button button-secondary" type="button" disabled={actionBusy || loading} onClick={() => { setActionError(null); setActionMessage(null); setPendingAction({ kind: "features", symbol: row.symbol, market: row.market as "spot" | "futures", timeframe: row.timeframe }); }}>Build features</button>}</td>}</tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading datasets…" : "No datasets match this filter."}</p>}
    </section>
    <section className="panel" aria-labelledby="master-jobs-title"><div className="panel-heading"><div><h2 id="master-jobs-title">Recent background jobs</h2><p>Read-only job history; no job is started or resumed here.</p></div><span className="panel-counter">{data?.jobs.length ?? 0} ROWS</span></div>
      {data?.jobs.length ? <div className="table-scroll"><table><thead><tr><th>Job</th><th>Dataset</th><th>Status</th><th>Progress</th><th>Created</th><th>Message</th></tr></thead><tbody>{data.jobs.map((row, index) => <tr key={row.id ?? row.job_id ?? index}><td>{row.type}</td><td>{[row.symbol, row.market, row.timeframe].filter(Boolean).join(" · ") || "—"}</td><td>{row.status}</td><td>{typeof row.progress === "number" ? num(row.progress) + "%" : "—"}</td><td>{jobDate(row.created_at)}</td><td>{row.message ?? row.error ?? "—"}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading jobs…" : "No recent background jobs returned."}</p>}
    </section>
    {pendingAction && <section className="panel" role="dialog" aria-modal="true" aria-labelledby="master-action-confirm-title">
      <div className="panel-heading"><div><h2 id="master-action-confirm-title">Confirm {pendingAction.kind === "backfill" ? "backfill" : "feature build"}</h2><p>This starts a background job and may write to the database. Review the details before continuing.</p></div></div>
      <p><strong>Dataset:</strong> {[pendingAction.symbol, pendingAction.market, pendingAction.timeframe].join(" · ")}</p>
      {pendingAction.kind === "backfill" && <p><strong>History:</strong> {pendingAction.years} year(s); automatic feature build {pendingAction.auto_compute_features ? "enabled" : "disabled"}.</p>}
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={actionBusy} onClick={() => setPendingAction(null)}>Cancel</button><button className="button button-primary" type="button" disabled={actionBusy} onClick={() => void confirmPendingAction()}>{actionBusy ? "Starting job…" : pendingAction.kind === "backfill" ? "Start backfill" : "Start feature build"}</button></div>
    </section>}
  </div>;
}
