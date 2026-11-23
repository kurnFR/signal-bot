import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./AuthGate";
import { Activity, ArrowDown, ArrowUp, ArrowUpDown, BarChart3, RefreshCw, ShieldCheck, X } from "lucide-react";
import { ApiError } from "./lib/api";
import { PromotionStatusPanel } from "./PromotionStatusPanel";
import { fetchBackfilledSymbols } from "./lib/masterData";
import { trapFocus } from "./lib/dialog";
import { deployPaperStrategy, PaperTradingActionError } from "./lib/paperTrading";
import { BacktestExecutionError, executeBacktest, fetchBacktestHistory, fetchBacktestRunDetails, fetchBacktestStrategies, type BacktestExecutionInput, type BacktestExecutionResult, type BacktestRunDetails, type BacktestRunSummary, type BacktestStrategy, type BacktestStrategyParameter, type PromotionPolicy } from "./lib/backtestStudio";

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

export const SUPPORTED_PAPER_TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;
export type PaperTimeframe = (typeof SUPPORTED_PAPER_TIMEFRAMES)[number];

type SortDirection = "asc" | "desc";
type RunSortField = "id" | "symbol" | "market" | "dataSegment" | "totalTrades" | "winRatePct" | "profitFactor" | "expectancyR" | "maxDrawdownR" | "createdAt";
type TradeSortField = "direction" | "entryTimeWib" | "entryPrice" | "exitTimeWib" | "exitPrice" | "netReturnPct" | "rMultiple" | "exitReason" | "holdingBars";

interface TableSortHeaderProps<T extends string> {
  field: T;
  currentField: T;
  direction: SortDirection;
  label: string;
  onSort: (field: T) => void;
}

function TableSortHeader<T extends string>({
  field,
  currentField,
  direction,
  label,
  onSort,
}: TableSortHeaderProps<T>) {
  const isActive = field === currentField;
  return (
    <th
      scope="col"
      aria-sort={isActive ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        className={"table-sort-button" + (isActive ? " is-active" : "")}
        onClick={() => onSort(field)}
        title={`Sort by ${label} (${isActive && direction === "asc" ? "descending" : "ascending"})`}
        aria-label={`Sort by ${label}`}
      >
        <span>{label}</span>
        {isActive ? (
          direction === "asc" ? (
            <ArrowUp size={13} className="sort-icon-active" aria-hidden="true" />
          ) : (
            <ArrowDown size={13} className="sort-icon-active" aria-hidden="true" />
          )
        ) : (
          <ArrowUpDown size={12} className="sort-icon-idle" aria-hidden="true" />
        )}
      </button>
    </th>
  );
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
  const [sortField, setSortField] = useState<RunSortField>("id");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [tradeSortField, setTradeSortField] = useState<TradeSortField>("entryTimeWib");
  const [tradeSortDirection, setTradeSortDirection] = useState<SortDirection>("asc");
  const auth = useAuth();
  const canRunBacktest = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [availableSymbols, setAvailableSymbols] = useState<string[]>(["BTCUSDT"]);
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
  const [promotionPolicy, setPromotionPolicy] = useState<PromotionPolicy | null>(null);
  const [promotionSideEffectAcknowledged, setPromotionSideEffectAcknowledged] = useState(false);

  // Deploy to Paper Trading modal state
  const [deployRun, setDeployRun] = useState<BacktestRunDetails | null>(null);
  const [deployTimeframe, setDeployTimeframe] = useState<PaperTimeframe | "">("");
  const [deployCapital, setDeployCapital] = useState(5000);
  const [deployRisk, setDeployRisk] = useState(1.0);
  const [deployTelegram, setDeployTelegram] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const deployDialogRef = useRef<HTMLDialogElement | null>(null);
  const deployTriggerRef = useRef<HTMLButtonElement | null>(null);

  const extractStrategyOverrides = (run: BacktestRunDetails | null): Record<string, unknown> | undefined => {
    if (!run?.params) return undefined;
    const standardKeys = new Set([
      "symbol", "market", "timeframe", "data_segment",
      "initial_capital", "risk_per_trade_pct", "save_to_db",
      "htf_df", "pair_df", "funding_events",
    ]);
    const overrides: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(run.params)) {
      if (!standardKeys.has(key) && !key.endsWith("_df") && value !== undefined && value !== null) {
        overrides[key] = value;
      }
    }
    return Object.keys(overrides).length > 0 ? overrides : undefined;
  };

  const openDeployModal = (run: BacktestRunDetails, trigger?: HTMLButtonElement) => {
    deployTriggerRef.current = trigger ?? null;
    setDeployRun(run);
    const isSupported = (SUPPORTED_PAPER_TIMEFRAMES as readonly string[]).includes(run.timeframe);
    setDeployTimeframe(isSupported ? (run.timeframe as PaperTimeframe) : "");
    const initialCap = typeof run.params?.initial_capital === "number" && run.params.initial_capital >= 100
      ? run.params.initial_capital
      : 5000;
    const initialRisk = typeof run.params?.risk_per_trade_pct === "number" && run.params.risk_per_trade_pct >= 0.1 && run.params.risk_per_trade_pct <= 10.0
      ? run.params.risk_per_trade_pct
      : 1.0;
    setDeployCapital(initialCap);
    setDeployRisk(initialRisk);
    setDeployTelegram(false);
    setDeployError(null);
  };

  const closeDeployModal = () => {
    setDeployRun(null);
    setDeployTimeframe("");
    setDeployError(null);
    deployTriggerRef.current?.focus();
  };

  useEffect(() => {
    if (deployRun && deployDialogRef.current) {
      if (!deployDialogRef.current.open) {
        if (typeof deployDialogRef.current.showModal === "function") {
          deployDialogRef.current.showModal();
        } else {
          deployDialogRef.current.setAttribute("open", "");
        }
      }
    }
  }, [deployRun]);

  const handleExecuteDeploy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!deployRun || deployBusy) return;
    setDeployError(null);
    if (!deployTimeframe || !(SUPPORTED_PAPER_TIMEFRAMES as readonly string[]).includes(deployTimeframe)) {
      setDeployError("Please select a supported paper trading timeframe (15m, 1h, 4h, or 1d).");
      return;
    }
    if (deployCapital < 100) {
      setDeployError("Allocated capital must be at least $100.");
      return;
    }
    if (deployRisk < 0.1 || deployRisk > 10.0) {
      setDeployError("Risk per trade must be between 0.1% and 10.0%.");
      return;
    }
    setDeployBusy(true);
    try {
      const targetTimeframe = deployTimeframe as PaperTimeframe;
      const strategyOverrides = extractStrategyOverrides(deployRun);
      const res = await deployPaperStrategy({
        symbol: deployRun.symbol,
        market: (deployRun.market.toLowerCase() === "futures" ? "futures" : "spot") as "spot" | "futures",
        timeframe: targetTimeframe,
        strategy_name: deployRun.strategy,
        allocated_capital: deployCapital,
        risk_per_trade_pct: deployRisk,
        send_telegram: deployTelegram,
        params: strategyOverrides,
      });
      setRunNotice(
        res.message || `Strategy '${deployRun.strategy}' deployed to Live Paper Trading on ${deployRun.symbol} (${deployRun.market.toUpperCase()} · ${targetTimeframe}).`
      );
      closeDeployModal();
    } catch (cause) {
      setDeployError(cause instanceof PaperTradingActionError ? cause.message : "Failed to deploy strategy to Paper Trading.");
    } finally {
      setDeployBusy(false);
    }
  };

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
    const controller = new AbortController();
    fetchBackfilledSymbols(controller.signal).then((symbols) => {
      if (controller.signal.aborted) return;
      setAvailableSymbols(symbols);
    }).catch(() => {});
    return () => controller.abort();
  }, []);

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

  const strategyNames = useMemo(() => new Map(strategies.map((strategy) => [strategy.id, strategy.displayName])), [strategies]);

  const handleSort = (field: RunSortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      if (field === "symbol" || field === "market" || field === "dataSegment") {
        setSortDirection("asc");
      } else {
        setSortDirection("desc");
      }
    }
  };

  const handleTradeSort = (field: TradeSortField) => {
    if (tradeSortField === field) {
      setTradeSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setTradeSortField(field);
      setTradeSortDirection(field === "netReturnPct" || field === "rMultiple" ? "desc" : "asc");
    }
  };

  const sortedRuns = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const filtered = runs.filter((run) => !needle || [run.symbol, run.market, run.timeframe, run.strategy, run.dataSegment].some((value) => value.toLowerCase().includes(needle)));

    return [...filtered].sort((a, b) => {
      let aVal: unknown = a[sortField];
      let bVal: unknown = b[sortField];

      if (sortField === "createdAt") {
        aVal = a.createdAt ? Date.parse(a.createdAt) : 0;
        bVal = b.createdAt ? Date.parse(b.createdAt) : 0;
      } else if (sortField === "symbol") {
        const aName = strategyNames.get(a.strategy) ?? a.strategy;
        const bName = strategyNames.get(b.strategy) ?? b.strategy;
        aVal = `${a.symbol} ${aName}`.toLowerCase();
        bVal = `${b.symbol} ${bName}`.toLowerCase();
      } else if (sortField === "market") {
        aVal = `${a.market} ${a.timeframe}`.toLowerCase();
        bVal = `${b.market} ${b.timeframe}`.toLowerCase();
      }

      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      let cmp = 0;
      if (typeof aVal === "number" && typeof bVal === "number") {
        cmp = aVal - bVal;
      } else {
        cmp = String(aVal).localeCompare(String(bVal));
      }

      return sortDirection === "asc" ? cmp : -cmp;
    });
  }, [runs, filter, sortField, sortDirection, strategyNames]);

  const sortedTrades = useMemo(() => {
    return [...trades].sort((a, b) => {
      let aVal: unknown = a[tradeSortField];
      let bVal: unknown = b[tradeSortField];

      if (tradeSortField === "entryTimeWib" || tradeSortField === "exitTimeWib") {
        const aStr = a[tradeSortField];
        const bStr = b[tradeSortField];
        aVal = aStr ? Date.parse(aStr.replace(" ", "T")) : 0;
        bVal = bStr ? Date.parse(bStr.replace(" ", "T")) : 0;
      }

      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      let cmp = 0;
      if (typeof aVal === "number" && typeof bVal === "number") {
        cmp = aVal - bVal;
      } else {
        cmp = String(aVal).localeCompare(String(bVal));
      }

      return tradeSortDirection === "asc" ? cmp : -cmp;
    });
  }, [trades, tradeSortField, tradeSortDirection]);

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
    setRunError(null); setRunNotice(null); setExecutionResult(null); setPromotionSideEffectAcknowledged(false);
    setPendingRun({
      symbol: normalizedSymbol, market, timeframe, strategy: strategyId,
      data_segment: dataSegment, initial_capital: capital, risk_per_trade_pct: risk,
      save_to_db: true, params,
    });
  };

  const confirmRun = async () => {
    if (!pendingRun || !promotionPolicy || (promotionPolicy.enabled && !promotionSideEffectAcknowledged)) return;
    setRunBusy(true); setRunError(null); setRunNotice(null);
    try {
      const result = await executeBacktest(pendingRun);
      setExecutionResult(result);
      setPendingRun(null);
      setPromotionSideEffectAcknowledged(false);
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
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Backtest Studio</strong><p>Running a backtest performs a simulation and requests persistence of the result. Only admin/trader roles can run one, and every run requires explicit review and confirmation. Promotion policy and recent decisions are visible below in read-only form; policy editing, activation, and Telegram actions remain unavailable.</p></div><span className="panel-counter">CONFIRMED RUNS</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Backtest Studio</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {runError && <div className="api-error" role="alert"><div><strong>Backtest run failed</strong><p>{runError}</p></div><button className="button button-secondary" onClick={() => setRunError(null)}>Dismiss</button></div>}
    {runNotice && <p className="control-feedback control-success" role="status">{runNotice}</p>}
    {canRunBacktest ? <section className="panel" aria-labelledby="backtest-run-config-title">
      <div className="panel-heading"><div><h2 id="backtest-run-config-title">Run a backtest</h2><p>Configure a simulation using stored market data. Results are submitted for persistence in history.</p></div><span className="panel-counter">ADMIN / TRADER</span></div>
      <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); reviewRun(); }}>
        <label className="screener-control-field">
          Symbol
          <input
            role="textbox"
            list="backtest-symbols-list"
            aria-label="Backtest symbol"
            value={symbol}
            maxLength={30}
            required
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
          />
          <datalist id="backtest-symbols-list">
            {availableSymbols.map((sym) => (
              <option key={sym} value={sym} />
            ))}
          </datalist>
        </label>
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
      <div className="screener-control-footer"><button className="button button-primary" type="button" disabled={loading || runBusy || !strategies.length || !activeStrategy || !promotionPolicy} onClick={reviewRun}>Review backtest run</button></div>
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
      <div className="panel-heading"><div><h2 id="backtest-history-title">Saved backtest runs</h2><p>Click any column header to sort ascending or descending. Select a run to inspect its stored metrics and trades.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      <div className="backtest-filter"><label htmlFor="backtest-run-filter">Filter history</label><input id="backtest-run-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Symbol, strategy, market, timeframe…" /></div>
      {loading && !runs.length ? <p className="empty-state" role="status">Loading saved runs…</p> : sortedRuns.length ? <div className="table-scroll"><table><thead><tr>
        <TableSortHeader field="id" currentField={sortField} direction={sortDirection} label="Run" onSort={handleSort} />
        <TableSortHeader field="symbol" currentField={sortField} direction={sortDirection} label="Symbol / strategy" onSort={handleSort} />
        <TableSortHeader field="market" currentField={sortField} direction={sortDirection} label="Market" onSort={handleSort} />
        <TableSortHeader field="dataSegment" currentField={sortField} direction={sortDirection} label="Segment" onSort={handleSort} />
        <TableSortHeader field="totalTrades" currentField={sortField} direction={sortDirection} label="Trades" onSort={handleSort} />
        <TableSortHeader field="winRatePct" currentField={sortField} direction={sortDirection} label="Win rate" onSort={handleSort} />
        <TableSortHeader field="profitFactor" currentField={sortField} direction={sortDirection} label="Profit factor" onSort={handleSort} />
        <TableSortHeader field="expectancyR" currentField={sortField} direction={sortDirection} label="Expectancy" onSort={handleSort} />
        <TableSortHeader field="maxDrawdownR" currentField={sortField} direction={sortDirection} label="Max drawdown" onSort={handleSort} />
        <TableSortHeader field="createdAt" currentField={sortField} direction={sortDirection} label="Created" onSort={handleSort} />
      </tr></thead><tbody>{sortedRuns.map((run) => <tr key={run.id} className={selectedId === run.id ? "backtest-row-selected" : ""}><td><button className="backtest-run-select" aria-pressed={selectedId === run.id} onClick={() => setSelectedId(run.id)}>#{run.id}</button></td><td><strong>{run.symbol}</strong><div>{strategyNames.get(run.strategy) ?? run.strategy}</div><code>{run.strategy}</code></td><td>{run.market} · {run.timeframe}</td><td>{run.dataSegment}</td><td>{run.totalTrades}</td><td>{run.winRatePct == null ? "—" : `${pct.format(run.winRatePct)}%`}</td><td>{moneyish(run.profitFactor)}</td><td>{run.expectancyR == null ? "—" : `${numeric.format(run.expectancyR)} R`}</td><td>{run.maxDrawdownR == null ? "—" : `${numeric.format(run.maxDrawdownR)} R`}</td><td>{dateLabel(run.createdAt)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{runs.length ? "No saved runs match this filter." : "No saved backtest runs were returned."}</p>}
    </section>
    <section className="panel" aria-labelledby="backtest-run-details-title">
      <div className="panel-heading">
        <div>
          <h2 id="backtest-run-details-title">Run details and closed trades</h2>
          <p>Stored execution outcomes; values are displayed as returned by the API.</p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span className="panel-counter">{trades.length} TRADES</span>
          {canRunBacktest && details && (
            <button
              className="button button-primary button-sm"
              type="button"
              onClick={(e) => openDeployModal(details, e.currentTarget)}
            >
              Deploy to Paper Trading
            </button>
          )}
        </div>
      </div>
      {detailError && <p role="alert" className="error-banner">{detailError}</p>}
      {detailLoading ? <p className="empty-state" role="status">Loading selected run details…</p> : details ? <>
        <div className="paper-risk-grid"><div><span>Run</span><strong>#{details.id} · {details.symbol}</strong></div><div><span>Strategy</span><strong>{strategyNames.get(details.strategy) ?? details.strategy}</strong></div><div><span>Market / timeframe</span><strong>{details.market} · {details.timeframe}</strong></div><div><span>Data segment</span><strong>{details.dataSegment}</strong></div><div><span>Win rate</span><strong>{details.winRatePct == null ? "—" : `${pct.format(details.winRatePct)}%`}</strong></div><div><span>Profit factor</span><strong>{moneyish(details.profitFactor)}</strong></div><div><span>Expectancy</span><strong>{details.expectancyR == null ? "—" : `${numeric.format(details.expectancyR)} R`}</strong></div><div><span>Max drawdown</span><strong>{details.maxDrawdownR == null ? "—" : `${numeric.format(details.maxDrawdownR)} R`}</strong></div></div>
        {details.params && <details className="backtest-params"><summary>Stored run parameters</summary><pre>{JSON.stringify(details.params, null, 2)}</pre></details>}
        {sortedTrades.length ? <div className="table-scroll"><table><thead><tr>
          <TableSortHeader field="direction" currentField={tradeSortField} direction={tradeSortDirection} label="Direction" onSort={handleTradeSort} />
          <TableSortHeader field="entryTimeWib" currentField={tradeSortField} direction={tradeSortDirection} label="Entry time (WIB)" onSort={handleTradeSort} />
          <TableSortHeader field="entryPrice" currentField={tradeSortField} direction={tradeSortDirection} label="Entry" onSort={handleTradeSort} />
          <TableSortHeader field="exitTimeWib" currentField={tradeSortField} direction={tradeSortDirection} label="Exit time (WIB)" onSort={handleTradeSort} />
          <TableSortHeader field="exitPrice" currentField={tradeSortField} direction={tradeSortDirection} label="Exit" onSort={handleTradeSort} />
          <TableSortHeader field="netReturnPct" currentField={tradeSortField} direction={tradeSortDirection} label="Net return" onSort={handleTradeSort} />
          <TableSortHeader field="rMultiple" currentField={tradeSortField} direction={tradeSortDirection} label="R multiple" onSort={handleTradeSort} />
          <TableSortHeader field="exitReason" currentField={tradeSortField} direction={tradeSortDirection} label="Exit reason" onSort={handleTradeSort} />
          <TableSortHeader field="holdingBars" currentField={tradeSortField} direction={tradeSortDirection} label="Holding bars" onSort={handleTradeSort} />
        </tr></thead><tbody>{sortedTrades.map((trade, index) => <tr key={trade.entryTimeWib + index}><td>{trade.direction}</td><td>{trade.entryTimeWib}</td><td>{moneyish(trade.entryPrice)}</td><td>{trade.exitTimeWib}</td><td>{moneyish(trade.exitPrice)}</td><td className={trade.netReturnPct > 0 ? "paper-positive" : trade.netReturnPct < 0 ? "paper-negative" : ""}>{pct.format(trade.netReturnPct)}%</td><td>{trade.rMultiple == null ? "—" : `${numeric.format(trade.rMultiple)} R`}</td><td>{trade.exitReason}</td><td>{trade.holdingBars}</td></tr>)}</tbody></table></div> : <p className="empty-state">No closed trades were stored for this run.</p>}
      </> : <p className="empty-state">{selectedId == null ? "Select a saved run to inspect its details." : "Run details are not available."}</p>}
    </section>
    {pendingRun && <section className="panel" aria-labelledby="backtest-confirm-title">
      <div className="panel-heading"><div><h2 id="backtest-confirm-title">Confirm backtest run</h2><p>This simulation requests persistence in backtest history and does not execute live trades. The backend also evaluates its saved auto-promotion policy when a run completes.</p></div></div>
      {promotionPolicy?.enabled ? <div className="api-error" role="note"><div><strong>Auto-promotion is enabled</strong><p>This run may activate a paper strategy. Telegram alerts are {promotionPolicy.send_telegram ? "enabled and may send an external message" : "disabled"} in the saved policy. Acknowledge these possible side effects before running.</p></div></div> : <p className="empty-state">The saved policy currently reports auto-promotion disabled. Verify this policy remains unchanged before running; the backend is authoritative.</p>}
      <p><strong>Configuration:</strong> {[pendingRun.symbol, pendingRun.market, pendingRun.timeframe, pendingRun.strategy, pendingRun.data_segment].join(" · ")}</p>
      <p><strong>Capital / risk:</strong> {numeric.format(pendingRun.initial_capital)} / {pendingRun.risk_per_trade_pct}% per trade</p>
      {promotionPolicy?.enabled && <label className="screener-alert-toggle"><input aria-label="Acknowledge auto-promotion side effects" type="checkbox" checked={promotionSideEffectAcknowledged} onChange={(event) => setPromotionSideEffectAcknowledged(event.target.checked)} /><span><strong>I understand this backtest may activate a paper strategy and, if enabled, send a Telegram alert.</strong></span></label>}
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={runBusy} onClick={() => { setPendingRun(null); setPromotionSideEffectAcknowledged(false); }}>Cancel</button><button className="button button-primary" type="button" disabled={runBusy || !promotionPolicy || (promotionPolicy.enabled && !promotionSideEffectAcknowledged)} onClick={() => void confirmRun()}>{runBusy ? "Running simulation…" : "Run backtest"}</button></div>
    </section>}
    {deployRun && (
      <dialog
        ref={deployDialogRef}
        className="app-dialog"
        aria-labelledby="backtest-deploy-dialog-title"
        aria-describedby="backtest-deploy-dialog-desc"
        onCancel={(e) => { e.preventDefault(); closeDeployModal(); }}
        onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeDeployModal(); } else { trapFocus(e); } }}
      >
        <div className="panel-heading">
          <div>
            <h3 id="backtest-deploy-dialog-title">Deploy Tested Strategy to Paper Trading</h3>
            <p id="backtest-deploy-dialog-desc">
              Activate simulated execution for <strong>{deployRun.strategy}</strong> on <strong>{deployRun.symbol}</strong> ({deployRun.market.toUpperCase()}{deployTimeframe ? ` · ${deployTimeframe}` : ""}).
            </p>
          </div>
          <button type="button" aria-label="Close dialog" onClick={closeDeployModal} className="icon-button"><X size={16} /></button>
        </div>

        <form onSubmit={handleExecuteDeploy} style={{ marginTop: "14px" }}>
          {!(SUPPORTED_PAPER_TIMEFRAMES as readonly string[]).includes(deployRun.timeframe) && (
            <div className="api-error" role="status" style={{ marginBottom: "14px", background: "var(--surface-sunken)" }}>
              <div>
                <strong>Timeframe selection required</strong>
                <p>
                  Backtest was run on <code>{deployRun.timeframe}</code>. Live Paper Trading only supports <code>15m</code>, <code>1h</code>, <code>4h</code>, and <code>1d</code>. You must choose an explicit paper timeframe or run a backtest on a supported timeframe.
                </p>
              </div>
            </div>
          )}

          <div className="paper-risk-grid" style={{ marginBottom: "14px" }}>
            <div><span>Symbol</span><strong>{deployRun.symbol}</strong></div>
            <div><span>Market</span><strong>{deployRun.market.toUpperCase()}</strong></div>
            <div><span>Backtest Timeframe</span><strong>{deployRun.timeframe}</strong></div>
            <div><span>Strategy</span><strong>{strategyNames.get(deployRun.strategy) ?? deployRun.strategy}</strong></div>
            <div><span>Backtest Win Rate</span><strong>{deployRun.winRatePct == null ? "—" : `${pct.format(deployRun.winRatePct)}%`}</strong></div>
            <div><span>Target Paper Timeframe</span><strong>{deployTimeframe || "Explicit selection required"}</strong></div>
          </div>

          {(() => {
            const overrides = extractStrategyOverrides(deployRun);
            if (!overrides) return null;
            return (
              <div style={{ marginBottom: "14px", padding: "8px 12px", background: "var(--surface-sunken)", borderRadius: 6 }}>
                <span style={{ fontSize: "11px", fontWeight: 600, textTransform: "uppercase", color: "var(--color-text-secondary)" }}>
                  Carried Backtest Overrides ({Object.keys(overrides).length})
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "4px" }}>
                  {Object.entries(overrides).map(([k, v]) => (
                    <span key={k} className="status-pill" style={{ fontSize: "11px" }}>
                      <code>{k}</code>: {String(v)}
                    </span>
                  ))}
                </div>
              </div>
            );
          })()}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px", marginBottom: "12px" }}>
            <div className="screener-control-field">
              <label htmlFor="bt-deploy-timeframe">Paper Timeframe</label>
              <select
                id="bt-deploy-timeframe"
                value={deployTimeframe}
                disabled={deployBusy}
                required
                onChange={(e) => setDeployTimeframe(e.target.value as PaperTimeframe | "")}
              >
                <option value="" disabled>Select supported timeframe</option>
                {SUPPORTED_PAPER_TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>{tf}</option>
                ))}
              </select>
            </div>
            <div className="screener-control-field">
              <label htmlFor="bt-deploy-capital">Allocated Virtual Capital ($)</label>
              <input
                id="bt-deploy-capital"
                type="number"
                min={100}
                step={100}
                required
                value={deployCapital}
                disabled={deployBusy}
                onChange={(e) => setDeployCapital(Number(e.target.value))}
              />
            </div>
            <div className="screener-control-field">
              <label htmlFor="bt-deploy-risk">Risk per Trade (%)</label>
              <input
                id="bt-deploy-risk"
                type="number"
                min={0.1}
                max={10.0}
                step={0.1}
                required
                value={deployRisk}
                disabled={deployBusy}
                onChange={(e) => setDeployRisk(Number(e.target.value))}
              />
            </div>
          </div>

          <label className="screener-alert-toggle" style={{ margin: "14px 0" }}>
            <input
              type="checkbox"
              checked={deployTelegram}
              disabled={deployBusy}
              onChange={(e) => setDeployTelegram(e.target.checked)}
            />
            <span>
              <strong>Send deployment alert to Telegram</strong>
              <small>Sends a one-time deployment notification via configured bot.</small>
            </span>
          </label>

          {deployError && (
            <div className="api-error" role="alert" style={{ marginBottom: "12px" }}>
              <div><strong>Deployment failed</strong><p>{deployError}</p></div>
            </div>
          )}

          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "16px" }}>
            <button type="button" className="button button-secondary" onClick={closeDeployModal} disabled={deployBusy}>Cancel</button>
            <button type="submit" className="button button-primary" disabled={deployBusy}>
              {deployBusy ? "Deploying…" : "Deploy to Paper Trading"}
            </button>
          </div>
        </form>
      </dialog>
    )}
    <PromotionStatusPanel onPolicyLoaded={setPromotionPolicy} />
    <p className="page-description">Backtest execution is an explicit, role-gated POST action. Promotion policy and audit are read-only; editing, activation, and Telegram actions remain unavailable.</p>
  </div>;
}
