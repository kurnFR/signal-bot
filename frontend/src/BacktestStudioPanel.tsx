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
export function BacktestStudioPanel() {
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
  return <div className="backtest-studio-view" aria-busy={loading || detailLoading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only Backtest Studio</strong><p>Review saved backtest runs, performance metrics, strategy metadata, and closed trades. Running backtests, changing parameters, auto-promotion policy, and Telegram settings are not available in this view.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Backtest Studio</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
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
    <p className="page-description">Scope note: only GET endpoints are called. Backtest execution, policy edits, promotion, deployment, and Telegram actions remain unavailable.</p>
  </div>;
}
