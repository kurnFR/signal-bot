import { useEffect, useMemo, useState } from "react";
import { useAuth } from "./AuthGate";
import { ArrowRight, Database, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { cancelMasterDataJob, fetchMasterDataJobs, fetchMasterDataSnapshot, fetchPopularCrypto, searchCryptoSymbols, MasterDataActionError, triggerMasterDataBackfill, triggerMasterDataFeatureBuild, deleteMasterDataset, type DeleteDatasetInput, type CryptoMarketTicker, type MasterDataSnapshot } from "./lib/masterData";

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
  const [cryptoQuery, setCryptoQuery] = useState("");
  const [cryptoMarket, setCryptoMarket] = useState<"all" | "spot" | "futures">("all");
  const [cryptoResults, setCryptoResults] = useState<CryptoMarketTicker[]>([]);
  const [cryptoSearchBusy, setCryptoSearchBusy] = useState(false);
  const [cryptoSearchError, setCryptoSearchError] = useState<string | null>(null);
  const [cryptoSearched, setCryptoSearched] = useState(false);
  const [popularCoins, setPopularCoins] = useState<CryptoMarketTicker[]>([]);
  const [popularLoading, setPopularLoading] = useState(true);
  const [popularError, setPopularError] = useState<string | null>(null);
  const [popularAttempt, setPopularAttempt] = useState(0);
  const auth = useAuth();
  const isAdmin = (auth?.user.role ?? "").toLowerCase() === "admin";
  const [pendingCancel, setPendingCancel] = useState<string | null>(null);
  const [jobRefreshError, setJobRefreshError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DeleteDatasetInput | null>(null);
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
  useEffect(() => {
    const controller = new AbortController();
    setPopularLoading(true); setPopularError(null);
    fetchPopularCrypto("spot", controller.signal).then((result) => {
      if (!controller.signal.aborted) setPopularCoins(result.items);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setPopularError(cause instanceof Error ? cause.message : "Popular markets are temporarily unavailable.");
    }).finally(() => {
      if (!controller.signal.aborted) setPopularLoading(false);
    });
    return () => controller.abort();
  }, [popularAttempt]);

  const hasLiveJobs = data?.jobs.some((job) => ["queued", "running", "cancelling"].includes(job.status)) ?? false;
  useEffect(() => {
    if (!hasLiveJobs || loading || actionBusy) return;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      try {
        const jobs = await fetchMasterDataJobs(controller.signal);
        if (!controller.signal.aborted) {
          setData((previous) => previous && { ...previous, jobs });
          setJobRefreshError(null);
        }
      } catch (cause) {
        if (!controller.signal.aborted) setJobRefreshError(errorMessage(cause));
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 3000);
      }
    };
    timer = window.setTimeout(poll, 3000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [hasLiveJobs, loading, actionBusy]);

  const coverage = useMemo(() => (data?.coverage ?? []).filter((row) => {
    const needle = filter.trim().toLowerCase();
    return !needle || [row.symbol, row.market, row.timeframe].some((part) => part.toLowerCase().includes(needle));
  }), [data, filter]);

  const searchAssets = async () => {
    setCryptoSearchBusy(true); setCryptoSearchError(null); setCryptoSearched(true);
    try {
      const result = await searchCryptoSymbols(cryptoQuery, cryptoMarket);
      setCryptoResults(result.results);
    } catch (cause) {
      setCryptoResults([]);
      setCryptoSearchError(cause instanceof Error ? cause.message : "Unable to search Binance markets. Please retry.");
    } finally { setCryptoSearchBusy(false); }
  };
  const useCryptoForBackfill = (coin: CryptoMarketTicker) => {
    if (!canManageJobs) return;
    setBackfillSymbol(coin.symbol);
    setBackfillMarket(coin.market);
    setActionError(null);
    setActionMessage(`${coin.symbol} selected. Review the backfill settings and confirm before starting a job.`);
  };
  const reviewBackfill = () => {
    const symbol = backfillSymbol.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{1,30}$/.test(symbol)) { setActionError("Enter a valid symbol using letters, numbers, dot, underscore, or hyphen."); return; }
    const years = Number(backfillYears);
    if (!Number.isFinite(years) || years < 0.05 || years > 10) { setActionError("Backfill years must be between 0.05 and 10."); return; }
    setActionError(null); setActionMessage(null);
    setPendingAction({ kind: "backfill", symbol, market: backfillMarket, timeframe: backfillTimeframe, years, auto_compute_features: autoComputeFeatures });
  };

  const confirmCancel = async () => {
    if (pendingCancel === null) return;
    setActionBusy(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const job = await cancelMasterDataJob(pendingCancel);
      setData((previous) => previous && {
        ...previous,
        jobs: previous.jobs.map((row) => row.id === pendingCancel ? job : row),
      });
      setPendingCancel(null);
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.detail || cause.message : "Could not cancel the job.");
    } finally {
      setActionBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setActionBusy(true); setActionError(null); setActionMessage(null);
    try {
      const result = await deleteMasterDataset(pendingDelete);
      setActionMessage(`Deleted ${num(result.features_deleted)} feature row(s) and ${num(result.candles_deleted)} candle(s).`);
      setPendingDelete(null);
      setAttempt((n) => n + 1);
    } catch (cause) {
      setPendingDelete(null);
      setActionError(cause instanceof MasterDataActionError ? cause.message : "Could not delete the dataset.");
    } finally {
      setActionBusy(false);
    }
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
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Master Data operations</strong><p>Review stored OHLCV coverage and job status. Backfill and feature-build jobs can write data or launch workers, so they are restricted to admin/trader roles and require explicit confirmation.</p></div><span className="panel-counter">ROLE GATED</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Master Data</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {actionError && <div className="api-error" role="alert"><div><strong>Master Data action failed</strong><p>{actionError}</p></div><button className="button button-secondary" onClick={() => setActionError(null)}>Dismiss</button></div>}
    {actionMessage && <p className="control-feedback control-success" role="status">{actionMessage}</p>}
    <section className="panel" aria-labelledby="crypto-search-title">
      <div className="panel-heading"><div><h2 id="crypto-search-title"><Search size={17} className="inline-icon" /> Binance asset search</h2><p>Search spot and futures USDT pairs using the backend’s cached Binance market data. Selecting a pair only fills the backfill form; it never starts a job.</p></div><span className="panel-counter">READ ONLY</span></div>
      <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); void searchAssets(); }}>
        <label className="screener-control-field">Search cryptocurrency symbols<input aria-label="Search cryptocurrency symbols" value={cryptoQuery} maxLength={80} onChange={(event) => setCryptoQuery(event.target.value)} placeholder="Symbol or keyword (e.g. SOL, PEPE, SUI)" /></label>
        <label className="screener-control-field">Market to search<select aria-label="Search market" value={cryptoMarket} onChange={(event) => setCryptoMarket(event.target.value as "all" | "spot" | "futures")}><option value="all">All markets</option><option value="spot">Spot only</option><option value="futures">Futures only</option></select></label>
        <div><button className="button button-primary" type="submit" disabled={cryptoSearchBusy}>{cryptoSearchBusy ? "Searching…" : "Search symbols"} <ArrowRight size={15} /></button></div>
      </form>
      {cryptoSearchError && <div className="api-error" role="alert"><div><strong>Market search failed</strong><p>{cryptoSearchError}</p></div><button className="button button-secondary" type="button" onClick={() => void searchAssets()} disabled={cryptoSearchBusy}><RefreshCw size={15} /> Retry</button></div>}
      {cryptoSearched && !cryptoSearchBusy && !cryptoSearchError && cryptoResults.length === 0 && <p className="empty-state">No matching USDT pairs found on Binance.</p>}
      {cryptoResults.length > 0 && <div className="overflow-x-auto"><table className="custom-table crypto-search-table"><thead><tr><th scope="col">Symbol</th><th scope="col">Market</th><th scope="col">Last price</th><th scope="col">24h change</th><th scope="col">24h volume (USDT)</th><th scope="col">Action</th></tr></thead><tbody>{cryptoResults.map((coin) => <tr key={coin.market + coin.symbol}><td><strong>{coin.symbol}</strong></td><td><span className={coin.market === "futures" ? "market-badge market-badge-futures" : "market-badge"}>{coin.market}</span></td><td>{Number.isFinite(coin.lastPrice) ? `${coin.lastPrice.toLocaleString(undefined, { maximumFractionDigits: 8 })}` : "—"}</td><td>{Number.isFinite(coin.priceChangePercent) ? `${coin.priceChangePercent >= 0 ? "+" : ""}${coin.priceChangePercent.toFixed(2)}%` : "—"}</td><td>{Number.isFinite(coin.quoteVolume) ? `${coin.quoteVolume.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</td><td>{canManageJobs ? <button className="button button-secondary" type="button" onClick={() => useCryptoForBackfill(coin)} aria-label={`Use ${coin.symbol} in backfill form`}>Use in backfill form</button> : <span className="settings-readonly-tag">Backfill: admin/trader</span>}</td></tr>)}</tbody></table></div>}
      <div className="panel-subsection" aria-label="Popular spot markets">
        <div className="panel-heading"><div><h3>Popular liquidity pairs</h3><p>Highest-quote-volume spot pairs from the backend market cache.</p></div><button className="button button-secondary" type="button" disabled={popularLoading} onClick={() => setPopularAttempt((value) => value + 1)}><RefreshCw size={14} /> Refresh</button></div>
        {popularError && <div className="error-banner" role="alert"><p>Popular markets are temporarily unavailable. {popularError}</p><button className="button button-secondary" type="button" onClick={() => setPopularAttempt((value) => value + 1)}>Retry</button></div>}
        {popularLoading ? <p className="empty-state">Loading popular markets…</p> : popularCoins.length === 0 ? <p className="empty-state">No popular markets are available right now.</p> : <div className="crypto-market-grid">{popularCoins.map((coin) => <article className="crypto-market-card" key={coin.symbol}>
          <div className="panel-heading"><div><h3>{coin.symbol}</h3><p>{coin.market.toUpperCase()} · USDT</p></div><span className={coin.priceChangePercent >= 0 ? "market-change-positive" : "market-change-negative"}>{coin.priceChangePercent >= 0 ? "+" : ""}{coin.priceChangePercent.toFixed(2)}%</span></div>
          <strong className="crypto-market-price">{Number.isFinite(coin.lastPrice) ? `${coin.lastPrice.toLocaleString(undefined, { maximumFractionDigits: 8 })}` : "—"}</strong>
          <p>24h volume: {Number.isFinite(coin.quoteVolume) ? `${(coin.quoteVolume / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })}M` : "—"}</p>
          {canManageJobs ? <button className="button button-secondary" type="button" onClick={() => useCryptoForBackfill(coin)} aria-label={`Use ${coin.symbol} in backfill form`}>Use in backfill form</button> : <span className="settings-readonly-tag">Backfill: admin/trader</span>}
        </article>)}</div>}
      </div>
    </section>
    {canManageJobs ? <section className="panel" aria-labelledby="master-backfill-title">
      <div className="panel-heading"><div><h2 id="master-backfill-title">Backfill historical data</h2><p>Starts a background job that writes OHLCV rows and can optionally compute features.</p></div><span className="panel-counter">ADMIN / TRADER</span></div>
      <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); reviewBackfill(); }}>
        <label className="screener-control-field">Symbol<input aria-label="Backfill symbol" value={backfillSymbol} maxLength={30} required onChange={(event) => setBackfillSymbol(event.target.value)} /></label>
        <label className="screener-control-field">Market<select aria-label="Backfill market" value={backfillMarket} onChange={(event) => setBackfillMarket(event.target.value as "spot" | "futures")}><option value="spot">Spot</option><option value="futures">Futures</option></select></label>
        <label className="screener-control-field">Timeframe<select aria-label="Backfill timeframe" value={backfillTimeframe} onChange={(event) => setBackfillTimeframe(event.target.value)}>{["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label className="screener-control-field">Years (0.05–10)<input aria-label="Backfill years" type="number" min="0.05" max="10" step="0.01" value={backfillYears} required onChange={(event) => setBackfillYears(event.target.value)} /></label>
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
      {coverage.length ? <div className="table-scroll"><table><thead><tr><th>Symbol</th><th>Market</th><th>Timeframe</th><th>Candles</th><th>Features</th><th>First date</th><th>Last date</th>{canManageJobs && <th>Actions</th>}</tr></thead><tbody>{coverage.map((row) => <tr key={row.symbol + row.market + row.timeframe}><td>{row.symbol}</td><td>{row.market}</td><td>{row.timeframe}</td><td>{num(row.candleCount)}</td><td>{row.hasFeatures ? "Available" : "Missing"} ({num(row.featureCount)})</td><td>{dateLabel(row.startDate)}</td><td>{dateLabel(row.endDate)}</td>{canManageJobs && <td>{(row.market === "spot" || row.market === "futures") && <button className="button button-secondary" type="button" disabled={actionBusy || loading} onClick={() => { setActionError(null); setActionMessage(null); setPendingAction({ kind: "features", symbol: row.symbol, market: row.market as "spot" | "futures", timeframe: row.timeframe }); }}>Build features</button>}{isAdmin && (row.market === "spot" || row.market === "futures") && <>{" "}<button className="button button-secondary" type="button" disabled={actionBusy || loading} onClick={() => setPendingDelete({ symbol: row.symbol, market: row.market as "spot" | "futures", timeframe: row.timeframe, scope: "features" })}>Delete features {row.symbol} {row.market} {row.timeframe}</button>{" "}<button className="button button-danger" type="button" disabled={actionBusy || loading} onClick={() => setPendingDelete({ symbol: row.symbol, market: row.market as "spot" | "futures", timeframe: row.timeframe, scope: "all" })}>Delete dataset {row.symbol} {row.market} {row.timeframe}</button></>}</td>}</tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading datasets…" : "No datasets match this filter."}</p>}
    </section>
    <section className="panel" aria-labelledby="master-jobs-title">
      <div className="panel-heading"><h2 id="master-jobs-title">Recent background jobs</h2></div>
      {jobRefreshError && <p className="api-error" role="alert">{jobRefreshError}</p>}
      {data?.jobs.length ? (
        <div className="table-scroll"><table>
          <thead><tr><th>Job</th><th>Dataset</th><th>Status</th><th>Progress</th><th>Created</th><th>Message</th>{canManageJobs && <th>Action</th>}</tr></thead>
          <tbody>{data.jobs.map((row, index) => (
            <tr key={row.id ?? row.job_id ?? index}>
              <td>{row.type}</td>
              <td>{[row.symbol, row.market, row.timeframe].filter(Boolean).join(" · ") || "—"}</td>
              <td><span className={row.status === "cancelling" ? "badge badge-warn" : "badge"}>{row.status}</span></td>
              <td>{typeof row.progress === "number" ? num(row.progress) + "%" : "—"}</td>
              <td>{jobDate(row.created_at)}</td>
              <td>{row.message ?? row.error ?? "—"}</td>
              {canManageJobs && <td>{row.id && row.can_cancel && (
                <button className="button button-secondary" type="button" disabled={actionBusy} onClick={() => setPendingCancel(row.id ?? null)}>
                  Cancel job {row.id}
                </button>
              )}</td>}
            </tr>
          ))}</tbody>
        </table></div>
      ) : <p className="empty-state">{loading ? "Loading jobs…" : "No recent background jobs returned."}</p>}
    </section>
    {pendingCancel && (
      <section className="panel" aria-labelledby="master-cancel-confirm-title">
        <h2 id="master-cancel-confirm-title">Cancel job {pendingCancel}?</h2>
        <p>The job stops between batches. Completed batches are kept.</p>
        <button className="button button-secondary" type="button" disabled={actionBusy} onClick={() => setPendingCancel(null)}>Keep running</button>{" "}
        <button className="button button-danger" type="button" disabled={actionBusy} onClick={() => void confirmCancel()}>Confirm cancellation</button>
      </section>
    )}
    {pendingAction && <section className="panel" aria-labelledby="master-action-confirm-title">
      <div className="panel-heading"><div><h2 id="master-action-confirm-title">Confirm {pendingAction.kind === "backfill" ? "backfill" : "feature build"}</h2><p>This starts a background job and may write to the database. Review the details before continuing.</p></div></div>
      <p><strong>Dataset:</strong> {[pendingAction.symbol, pendingAction.market, pendingAction.timeframe].join(" · ")}</p>
      {pendingAction.kind === "backfill" && <p><strong>History:</strong> {pendingAction.years} year(s); automatic feature build {pendingAction.auto_compute_features ? "enabled" : "disabled"}.</p>}
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={actionBusy} onClick={() => setPendingAction(null)}>Cancel</button><button className="button button-primary" type="button" disabled={actionBusy} onClick={() => void confirmPendingAction()}>{actionBusy ? "Starting job…" : pendingAction.kind === "backfill" ? "Start backfill" : "Start feature build"}</button></div>
    </section>}
    {pendingDelete && <section className="panel" aria-labelledby="master-delete-confirm-title">
      <div className="panel-heading"><div><h2 id="master-delete-confirm-title">Confirm delete ({pendingDelete.scope === "all" ? "candles + features" : "features only"})</h2><p>{pendingDelete.scope === "all" ? "This permanently removes stored candles and features for this dataset. Collectors or a backfill can rebuild them, but gaps may remain." : "Removes computed features only; candles are kept and features can be rebuilt."} The server refuses while an active paper deployment or a running job uses this dataset.</p></div></div>
      <p><strong>Dataset:</strong> {[pendingDelete.symbol, pendingDelete.market, pendingDelete.timeframe].join(" · ")}</p>
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={actionBusy} onClick={() => setPendingDelete(null)}>Cancel delete</button><button className="button button-danger" type="button" disabled={actionBusy} onClick={() => void confirmDelete()}>{actionBusy ? "Deleting…" : "Confirm delete"}</button></div>
    </section>}
  </div>;
}
