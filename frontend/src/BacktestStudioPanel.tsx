import { useEffect, useMemo, useState } from "react";
import { useAuth } from "./AuthGate";
import { Activity, BarChart3, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { BacktestExecutionError, executeBacktest, fetchBacktestHistory, fetchBacktestRunDetails, fetchBacktestStrategies, type BacktestExecutionInput, type BacktestExecutionResult, type BacktestRunDetails, type BacktestRunSummary, type BacktestStrategy, type BacktestStrategyParameter } from "./lib/backtestStudio";

const pct = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const numeric = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
function moneyish(value: number | null | undefined) { return value == null || !Number.isFinite(value) ? "—" : numeric.format(value); }
function dateLabel(value: string | null | undefined) {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view backtest results.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view backtest results.";
  return "Backtest data could not be loaded. Check API/database health, then retry.";
}
interface BacktestStudioPanelProps { initialStrategyId?: string | null; onInitialStrategyApplied?: () => void; }
export function BacktestStudioPanel({ initialStrategyId = null, onInitialStrategyApplied }: BacktestStudioPanelProps = {}) {
  const [strategies, setStrategies] = useState<BacktestStrategy[]>([]);
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [details, setDetails] = useState<BacktestRunDetails | null>(null);
  const [trades, setTrades] = useState<Awaited<ReturnType<typeof fetchBacktestRunDetails>>["trades"]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");
  const auth = useAuth();
  const canRunBacktest = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [market, setMarket] = useState<"spot" | "futures">("spot");
  const [timeframe, setTimeframe] = useState("1d");
  const [strategyId, setStrategyId] = useState("");
  const [dataSegment, setDataSegment] = useState<"full" | "train" | "holdout">("holdout");
  const [initialCapital, setInitialCapital] = useState("5000");
  const [riskPerTrade, setRiskPerTrade] = useState("1");
  const [parameterValues, setParameterValues] = useState<Record<string, string | number | boolean>>({});
  const [pendingRun, setPendingRun] = useState<BacktestExecutionInput | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);
  const [executionResult, setExecutionResult] = useState<BacktestExecutionResult | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([fetchBacktestStrategies(controller.signal), fetchBacktestHistory(controller.signal)])
      .then(([strategyResult, historyResult]) => {
        if (controller.signal.aborted) return;
        setStrategies(Array.isArray(strategyResult.strategies) ? strategyResult.strategies : []);
        const nextRuns = Array.isArray(historyResult.runs) ? historyResult.runs : [];
        setRuns(nextRuns);
        setSelectedId((current) => current != null && nextRuns.some((run) => run.id === current) ? current : nextRuns[0]?.id ?? null);
      }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (selectedId == null) { setDetails(null); setTrades([]); return; }
    const controller = new AbortController();
    setDetailLoading(true); setDetailError(null);
    fetchBacktestRunDetails(selectedId, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setDetails(result.run); setTrades(Array.isArray(result.trades) ? result.trades : []);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDetailError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, attempt]);
  useEffect(() => {
    if (strategies.length && !strategies.some((strategy) => strategy.id === strategyId)) setStrategyId(strategies[0].id);
  }, [strategies, strategyId]);
  useEffect(() => {
    if (!initialStrategyId || !strategies.some((strategy) => strategy.id === initialStrategyId)) return;
    setStrategyId(initialStrategyId);
    onInitialStrategyApplied?.();
  }, [initialStrategyId, strategies, onInitialStrategyApplied]);
  const activeStrategy = strategies.find((strategy) => strategy.id === strategyId) ?? null;
  const editableParams = useMemo(() => activeStrategy ? [...activeStrategy.params, ...activeStrategy.sharedParams] : [], [activeStrategy]);
  useEffect(() => {
    if (!activeStrategy) return;
    setParameterValues(Object.fromEntries(editableParams.map((parameter) => [parameter.key, parameter.default ?? (parameter.type === "bool" ? false : "")])));
  }, [activeStrategy, editableParams]);
  const filteredRuns = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return runs.filter((run) => !needle || [run.symbol, run.market, run.timeframe, run.strategy, run.dataSegment].some((value) => value.toLowerCase().includes(needle)));
  }, [runs, filter]);
  const strategyNames = useMemo(() => new Map(strategies.map((strategy) => [strategy.id, strategy.displayName])), [strategies]);
  const summary = details ?? (selectedId == null ? null : runs.find((run) => run.id === selectedId) ?? null);
  const reviewRun = () => {
    const normalizedSymbol = symbol.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{1,30}$/.test(normalizedSymbol)) { setRunError("Enter a valid symbol using letters, numbers, dot, underscore, or hyphen."); return; }
    const capital = Number(initialCapital);
    if (!Number.isFinite(capital) || capital < 100) { setRunError("Initial capital must be at least 100."); return; }
    const risk = Number(riskPerTrade);
    if (!Number.isFinite(risk) || risk < 0.1 || risk > 10) { setRunError("Risk per trade must be between 0.1% and 10%."); return; }
    if (!activeStrategy) { setRunError("Select a strategy before running a backtest."); return; }
    const params: Record<string, string | number | boolean> = {};
    for (const parameter of editableParams) {
      const raw = parameterValues[parameter.key] ?? parameter.default ?? (parameter.type === "bool" ? false : "");
      if (parameter.type === "bool") params[parameter.key] = Boolean(raw);
      else if (parameter.type === "string") {
        const value = String(raw).trim();
        if (!value) { setRunError(`${parameter.label || parameter.key} cannot be empty.`); return; }
        params[parameter.key] = value;
      } else {
        if (String(raw).trim() === "") { setRunError(`${parameter.label || parameter.key} must be a number.`); return; }
        const value = Number(raw);
        if (!Number.isFinite(value) || (parameter.min != null && value < parameter.min) || (parameter.max != null && value > parameter.max)) {
          setRunError(`${parameter.label || parameter.key} is outside its allowed range.`); return;
        }
        params[parameter.key] = value;
      }
    }
    setRunError(null); setRunNotice(null); setExecutionResult(null);
    setPendingRun({
      symbol: normalizedSymbol, market, timeframe, strategy: strategyId,
      data_segment: dataSegment, initial_capital: capital, risk_per_trade_pct: risk,
      save_to_db: true, params,
    });
  };
  const confirmRun = async () => {
    if (!pendingRun) return;
    setRunBusy(true); setRunError(null); setRunNotice(null);
    try {
      const result = await executeBacktest(pendingRun);
      setExecutionResult(result);
      setPendingRun(null);
      setRunNotice(result.runId == null
        ? "Backtest completed, but persistence was not confirmed (runId is null). The metrics below are from this response and may not appear in saved history."
        : `Backtest completed and saved as run #${result.runId}.`);
      if (result.runId != null) {
        setSelectedId(result.runId);
        setAttempt((value) => value + 1);
      }
    } catch (cause) {
      if (cause instanceof BacktestExecutionError && cause.status === 403) setRunError("Your account cannot run backtests. Only admin or trader roles can perform this action.");
      else setRunError(cause instanceof Error ? cause.message : "Backtest failed. Please retry.");
    } finally { setRunBusy(false); }
  };
  return <div className="backtest-studio-view" aria-busy={loading || detailLoading || runBusy}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Backtest Studio</strong><p>Running a backtest performs a simulation and requests persistence of the result. Only admin/trader roles can run one, and every run requires explicit review and confirmation. Auto-promotion policy and Telegram settings remain unavailable here.</p></div><span className="panel-counter">CONFIRMED RUNS</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Backtest Studio</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {runError && <div className="api-error" role="alert"><div><strong>Backtest run failed</strong><p>{runError}</p></div><button className="button button-secondary" onClick={() => setRunError(null)}>Dismiss</button></div>}
    {runNotice && <p className="control-feedback control-success" role="status">{runNotice}</p>}
    {canRunBacktest ? <section className="panel" aria-labelledby="backtest-run-config-title">
      <div className="panel-heading"><div><h2 id="backtest-run-config-title">Run a backtest</h2><p>Configure a simulation using stored market data. Results are submitted for persistence in history.</p></div><span className="panel-counter">ADMIN / TRADER</span></div>
      <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); reviewRun(); }}>
        <label className="screener-control-field">Symbol<input aria-label="Backtest symbol" value={symbol} maxLength={30} required onChange={(event) => setSymbol(event.target.value)} /></label>
        <label className="screener-control-field">Market<select aria-label="Backtest market" value={market} onChange={(event) => setMarket(event.target.value as "spot" | "futures")}><option value="spot">Spot</option><option value="futures">Futures</option></select></label>
        <label className="screener-control-field">Timeframe<select aria-label="Backtest timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label className="screener-control-field">Strategy<select aria-label="Backtest strategy" value={strategyId} onChange={(event) => setStrategyId(event.target.value)} required>{strategies.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.displayName}</option>)}</select></label>
        <label className="screener-control-field">Data segment<select aria-label="Backtest data segment" value={dataSegment} onChange={(event) => setDataSegment(event.target.value as "full" | "train" | "holdout")}><option value="holdout">Holdout</option><option value="train">Train</option><option value="full">Full dataset</option></select></label>
        <label className="screener-control-field">Initial capital<input aria-label="Initial capital" type="number" min="100" step="100" value={initialCapital} required onChange={(event) => setInitialCapital(event.target.value)} /></label>
        <label className="screener-control-field">Risk per trade (%)<input aria-label="Risk per trade" type="number" min="0.1" max="10" step="0.1" value={riskPerTrade} required onChange={(event) => setRiskPerTrade(event.target.value)} /></label>
      </form>
      {activeStrategy && <div className="panel-subsection"><div className="panel-heading"><div><h3>Strategy parameters</h3><p>{activeStrategy.description}</p></div>{activeStrategy.validated && <span className="panel-counter">HOLDOUT VALIDATED</span>}</div>
        {editableParams.length ? <div className="screener-control-grid">{editableParams.map((parameter: BacktestStrategyParameter) => {
          const value = parameterValues[parameter.key] ?? parameter.default ?? (parameter.type === "bool" ? false : "");
          if (parameter.type === "bool") return <label className="screener-alert-toggle" key={parameter.key}><input aria-label={parameter.label} type="checkbox" checked={Boolean(value)} onChange={(event) => setParameterValues((current) => ({ ...current, [parameter.key]: event.target.checked }))} /><span><strong>{parameter.label}</strong></span></label>;
          if (parameter.type === "string") return <label className="screener-control-field" key={parameter.key}>{parameter.label}<input aria-label={parameter.label} value={String(value)} onChange={(event) => setParameterValues((current) => ({ ...current, [parameter.key]: event.target.value }))} /></label>;
          return <label className="screener-control-field" key={parameter.key}>{parameter.label}<input aria-label={parameter.label} type="number" min={parameter.min} max={parameter.max} step={parameter.step ?? 1} value={String(value)} onChange={(event) => setParameterValues((current) => ({ ...current, [parameter.key]: event.target.value }))} /></label>;
        })}</div> : <p className="empty-state">No adjustable parameters for this strategy.</p>}
      </div>}
      <div className="screener-control-footer"><button className="button button-primary" type="button" disabled={loading || runBusy || !strategies.length} onClick={reviewRun}>Review backtest run</button></div>
    </section> : <p className="empty-state">Backtest execution is restricted to admin and trader roles. This session can review saved results only.</p>}
    {executionResult && <section className="panel" aria-labelledby="backtest-latest-result-title">
      <div className="panel-heading"><div><h2 id="backtest-latest-result-title">Latest execution result</h2><p>{executionResult.symbol} · {executionResult.market} · {executionResult.timeframe} · {executionResult.dataSegment}</p></div><span className="panel-counter">{executionResult.runId == null ? "NOT CONFIRMED SAVED" : `RUN #${executionResult.runId}`}</span></div>
      <div className="metrics-grid">
        <article className="metric-card"><div className="metric-top"><span>Trades</span></div><div className="metric-value">{executionResult.metrics.total_trades}</div></article>
        <article className="metric-card"><div className="metric-top"><span>Win rate</span></div><div className="metric-value">{executionResult.metrics.win_rate_pct == null ? "—" : `${pct.format(executionResult.metrics.win_rate_pct)}%`}</div></article>
        <article className="metric-card"><div className="metric-top"><span>Profit factor</span></div><div className="metric-value">{moneyish(executionResult.metrics.profit_factor)}</div></article>
        <article className="metric-card"><div className="metric-top"><span>Expectancy</span></div><div className="metric-value">{executionResult.metrics.expectancy_r == null ? "—" : `${numeric.format(executionResult.metrics.expectancy_r)} R`}</div></article>
        <article className="metric-card"><div className="metric-top"><span>Max drawdown</span></div><div className="metric-value">{executionResult.metrics.max_drawdown_r == null ? "—" : `${numeric.format(executionResult.metrics.max_drawdown_r)} R`}</div></article>
        <article className="metric-card"><div className="metric-top"><span>Candles used</span></div><div className="metric-value">{executionResult.totalCandles}</div></article>
      </div>
    </section>}
    <section className="metrics-grid" aria-label="Backtest history summary">
      <article className="metric-card"><div className="metric-top"><span>Saved runs loaded</span><span className="metric-icon"><BarChart3 size={17} /></span></div><div className="metric-value">{loading && !runs.length ? "Loading…" : runs.length}</div><div className="metric-foot"><span>Latest 100 records maximum</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Strategies available</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !strategies.length ? "Loading…" : strategies.length}</div><div className="metric-foot"><span>Registry metadata only</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Selected run trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{summary ? summary.totalTrades : detailLoading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Stored run summary</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Selected expectancy</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{summary?.expectancyR == null ? "—" : `${numeric.format(summary.expectancyR)} R`}</div><div className="metric-foot"><span>Average R per trade</span></div></article>
    </section>
    <section className="panel" aria-labelledby="backtest-history-title">
      <div className="panel-heading"><div><h2 id="backtest-history-title">Saved backtest runs</h2><p>Newest first. Select a run to inspect its stored metrics and trades.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      <div className="backtest-filter"><label htmlFor="backtest-run-filter">Filter history</label><input id="backtest-run-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, strategy, market, timeframe…" /></div>
      {loading && !runs.length ? <p className="empty-state" role="status">Loading saved runs…</p> : filteredRuns.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Run</th><th scope="col">Symbol / strategy</th><th scope="col">Market</th><th scope="col">Segment</th><th scope="col">Trades</th><th scope="col">Win rate</th><th scope="col">Profit factor</th><th scope="col">Expectancy</th><th scope="col">Max drawdown</th><th scope="col">Created</th></tr></thead><tbody>{filteredRuns.map((run) => <tr key={run.id} className={selectedId === run.id ? "backtest-row-selected" : ""}><td><button className="backtest-run-select" aria-pressed={selectedId === run.id} onClick={() => setSelectedId(run.id)}>#{run.id}</button></td><td><strong>{run.symbol}</strong><div>{strategyNames.get(run.strategy) ?? run.strategy}</div><code>{run.strategy}</code></td><td>{run.market} · {run.timeframe}</td><td>{run.dataSegment}</td><td>{run.totalTrades}</td><td>{run.winRatePct == null ? "—" : `${pct.format(run.winRatePct)}%`}</td><td>{moneyish(run.profitFactor)}</td><td>{run.expectancyR == null ? "—" : `${numeric.format(run.expectancyR)} R`}</td><td>{run.maxDrawdownR == null ? "—" : `${numeric.format(run.maxDrawdownR)} R`}</td><td>{dateLabel(run.createdAt)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{runs.length ? "No saved runs match this filter." : "No saved backtest runs were returned."}</p>}
    </section>
    <section className="panel" aria-labelledby="backtest-run-details-title">
      <div className="panel-heading"><div><h2 id="backtest-run-details-title">Run details and closed trades</h2><p>Stored execution outcomes; values are displayed as returned by the API.</p></div><span className="panel-counter">{trades.length} TRADES</span></div>
      {detailError && <p role="alert" className="error-banner">{detailError}</p>}
      {detailLoading ? <p className="empty-state" role="status">Loading selected run details…</p> : details ? <>
        <div className="paper-risk-grid"><div><span>Run</span><strong>#{details.id} · {details.symbol}</strong></div><div><span>Strategy</span><strong>{strategyNames.get(details.strategy) ?? details.strategy}</strong></div><div><span>Market / timeframe</span><strong>{details.market} · {details.timeframe}</strong></div><div><span>Data segment</span><strong>{details.dataSegment}</strong></div><div><span>Win rate</span><strong>{details.winRatePct == null ? "—" : `${pct.format(details.winRatePct)}%`}</strong></div><div><span>Profit factor</span><strong>{moneyish(details.profitFactor)}</strong></div><div><span>Expectancy</span><strong>{details.expectancyR == null ? "—" : `${numeric.format(details.expectancyR)} R`}</strong></div><div><span>Max drawdown</span><strong>{details.maxDrawdownR == null ? "—" : `${numeric.format(details.maxDrawdownR)} R`}</strong></div></div>
        {details.params && <details className="backtest-params"><summary>Stored run parameters</summary><pre>{JSON.stringify(details.params, null, 2)}</pre></details>}
        {trades.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Direction</th><th scope="col">Entry time (WIB)</th><th scope="col">Entry</th><th scope="col">Exit time (WIB)</th><th scope="col">Exit</th><th scope="col">Net return</th><th scope="col">R multiple</th><th scope="col">Exit reason</th><th scope="col">Holding bars</th></tr></thead><tbody>{trades.map((trade, index) => <tr key={trade.entryTimeWib + index}><td>{trade.direction}</td><td>{trade.entryTimeWib}</td><td>{moneyish(trade.entryPrice)}</td><td>{trade.exitTimeWib}</td><td>{moneyish(trade.exitPrice)}</td><td className={trade.netReturnPct > 0 ? "paper-positive" : trade.netReturnPct < 0 ? "paper-negative" : ""}>{pct.format(trade.netReturnPct)}%</td><td>{trade.rMultiple == null ? "—" : `${numeric.format(trade.rMultiple)} R`}</td><td>{trade.exitReason}</td><td>{trade.holdingBars}</td></tr>)}</tbody></table></div> : <p className="empty-state">No closed trades were stored for this run.</p>}
      </> : <p className="empty-state">{selectedId == null ? "Select a saved run to inspect its details." : "Run details are not available."}</p>}
    </section>
    {pendingRun && <section className="panel" aria-labelledby="backtest-confirm-title">
      <div className="panel-heading"><div><h2 id="backtest-confirm-title">Confirm backtest run</h2><p>This simulation may take time and requests that results be saved to backtest history. It does not execute live trades.</p></div></div>
      <p><strong>Configuration:</strong> {[pendingRun.symbol, pendingRun.market, pendingRun.timeframe, pendingRun.strategy, pendingRun.data_segment].join(" · ")}</p>
      <p><strong>Capital / risk:</strong> {numeric.format(pendingRun.initial_capital)} / {pendingRun.risk_per_trade_pct}% per trade</p>
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={runBusy} onClick={() => setPendingRun(null)}>Cancel</button><button className="button button-primary" type="button" disabled={runBusy} onClick={() => void confirmRun()}>{runBusy ? "Running simulation…" : "Run backtest"}</button></div>
    </section>}
    <p className="page-description">Backtest execution is an explicit, role-gated POST action. Auto-promotion policy, deployment, and Telegram actions remain unavailable.</p>
  </div>;
}
