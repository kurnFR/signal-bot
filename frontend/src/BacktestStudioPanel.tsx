import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./AuthGate";
import { Activity, ArrowDown, ArrowUp, ArrowUpDown, BarChart3, BrainCircuit, RefreshCw, ShieldCheck, Sparkles, TrendingUp, X } from "lucide-react";
import { ApiError } from "./lib/api";
import { PromotionStatusPanel } from "./PromotionStatusPanel";
import { fetchBackfilledSymbols } from "./lib/masterData";
import { trapFocus } from "./lib/dialog";
import { deployPaperStrategy, fetchGeneratorModels, updatePaperConfig, PaperTradingActionError, type GeneratorModelSummary, parseTelegramDeliveryStatus } from "./lib/paperTrading";
import {
  BacktestExecutionError,
  executeBacktest,
  fetchBacktestHistory,
  fetchBacktestRunDetails,
  fetchBacktestStrategies,
  executeWalkForward,
  runAllStrategies,
  fetchAiInsight,
  type BacktestExecutionInput,
  type BacktestExecutionResult,
  type BacktestRunDetails,
  type BacktestRunSummary,
  type BacktestStrategy,
  type BacktestStrategyParameter,
  type PromotionPolicy,
  type WalkForwardResult,
  type RunAllResult,
  type AiInsightResult,
} from "./lib/backtestStudio";
import { EquityDrawdownChart } from "./components/EquityDrawdownChart";

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

interface BacktestStudioPanelProps {
  initialStrategyId?: string | null;
  onInitialStrategyApplied?: () => void;
  paperReady?: boolean;
  selectedEvidenceRunId?: number;
  onSelectPaperEvidence?: (run: BacktestRunDetails) => void;
}
export function BacktestStudioPanel({ initialStrategyId = null, onInitialStrategyApplied, paperReady = true, selectedEvidenceRunId, onSelectPaperEvidence }: BacktestStudioPanelProps = {}) {
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
  const [generatorModels, setGeneratorModels] = useState<GeneratorModelSummary[]>([]);
  const [selectedGeneratorModelId, setSelectedGeneratorModelId] = useState("");
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);
  const [deployWarning, setDeployWarning] = useState<string | null>(null);
  const [executionResult, setExecutionResult] = useState<BacktestExecutionResult | null>(null);
  const [promotionPolicy, setPromotionPolicy] = useState<PromotionPolicy | null>(null);
  const [benchmarkPromotionConsent, setBenchmarkPromotionConsent] = useState(false);

  // Deploy to Paper Trading modal state
  const [deployRun, setDeployRun] = useState<BacktestRunDetails | null>(null);
  const [deployTimeframe, setDeployTimeframe] = useState<PaperTimeframe | "">("");
  const [deployCapital, setDeployCapital] = useState(5000);
  const [deployRisk, setDeployRisk] = useState(1.0);
  const [deployTelegram, setDeployTelegram] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  // Walk-Forward Analysis state
  const [walkForwardOpen, setWalkForwardOpen] = useState(false);
  const [walkForwardBusy, setWalkForwardBusy] = useState(false);
  const [walkForwardFolds, setWalkForwardFolds] = useState(4);
  const [walkForwardRolling, setWalkForwardRolling] = useState(false);
  const [walkForwardResult, setWalkForwardResult] = useState<WalkForwardResult | null>(null);
  const [walkForwardError, setWalkForwardError] = useState<string | null>(null);

  // Run All Strategies state
  const [runAllOpen, setRunAllOpen] = useState(false);
  const [runAllBusy, setRunAllBusy] = useState(false);
  const [runAllResult, setRunAllResult] = useState<RunAllResult | null>(null);
  const [runAllError, setRunAllError] = useState<string | null>(null);

  // AI Insight state
  const [aiInsightOpen, setAiInsightOpen] = useState(false);
  const [aiInsightBusy, setAiInsightBusy] = useState(false);
  const [aiInsightResult, setAiInsightResult] = useState<AiInsightResult | null>(null);
  const [aiInsightError, setAiInsightError] = useState<string | null>(null);

  const handleOpenWalkForward = () => {
    setWalkForwardResult(null);
    setWalkForwardError(null);
    setWalkForwardOpen(true);
  };

  const handleExecuteWalkForward = async () => {
    if (!activeStrategy) return;
    setWalkForwardBusy(true);
    setWalkForwardError(null);
    try {
      const res = await executeWalkForward({
        symbol,
        market,
        timeframe,
        strategy: strategyId,
        folds: walkForwardFolds,
        rolling: walkForwardRolling,
        params: parameterValues,
      });
      setWalkForwardResult(res);
    } catch (err: any) {
      setWalkForwardError(err instanceof Error ? err.message : "Walk-forward analysis failed.");
    } finally {
      setWalkForwardBusy(false);
    }
  };

  const handleOpenRunAll = () => {
    setRunAllResult(null);
    setRunAllError(null);
    setBenchmarkPromotionConsent(false);
    setRunAllOpen(true);
  };

  const handleExecuteRunAll = async () => {
    if (runAllBusy) return;
    setRunAllBusy(true);
    setRunAllError(null);
    setRunAllResult(null);
    setBenchmarkPromotionConsent(false);
    try {
      const res = await runAllStrategies({
        symbol,
        market,
        timeframe,
        data_segment: dataSegment,
        initial_capital: Number(initialCapital) || 5000,
        risk_per_trade_pct: Number(riskPerTrade) || 1.0,
        allow_auto_promotion: paperReady && dataSegment === "train" && Boolean(promotionPolicy?.enabled) && benchmarkPromotionConsent,
      });
      setRunAllResult(res);
    } catch (err: any) {
      setRunAllError(err instanceof Error ? err.message : "Failed to benchmark all strategies.");
    } finally {
      setRunAllBusy(false);
    }
  };

  const handleOpenAiInsight = async (runDetails: BacktestRunDetails) => {
    setAiInsightOpen(true);
    setAiInsightBusy(true);
    setAiInsightError(null);
    setAiInsightResult(null);
    try {
      const res = await fetchAiInsight(runDetails.symbol, runDetails.market, runDetails.timeframe, runDetails.strategy);
      setAiInsightResult(res);
    } catch (err: any) {
      setAiInsightError(err instanceof Error ? err.message : "Failed to generate AI quantitative insight.");
    } finally {
      setAiInsightBusy(false);
    }
  };
  const deployDialogRef = useRef<HTMLDialogElement | null>(null);
  const deployTriggerRef = useRef<HTMLButtonElement | null>(null);

  const extractStrategyOverrides = (run: BacktestRunDetails | null): Record<string, unknown> | undefined => {
    if (!run?.params) return undefined;
    const standardKeys = new Set([
      "symbol", "market", "timeframe", "data_segment",
      "initial_capital", "risk_per_trade_pct", "save_to_db",
      "htf_df", "pair_df", "funding_events", "pair_symbol", "ml_generator_model_id",
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
    setRunNotice(null);
    setDeployWarning(null);
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
    if (!paperReady || !deployRun || deployBusy) return;
    setDeployError(null);
    if (!deployTimeframe || !(SUPPORTED_PAPER_TIMEFRAMES as readonly string[]).includes(deployTimeframe)) {
      setDeployError("Please select a supported paper trading timeframe (15m, 1h, 4h, or 1d).");
      return;
    }
    if (onSelectPaperEvidence && deployTimeframe !== deployRun.timeframe) {
      setDeployError("Run the backtest on the paper timeframe before deploying.");
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
      const config = {
        symbol: deployRun.symbol,
        market: (deployRun.market.toLowerCase() === "futures" ? "futures" : "spot") as "spot" | "futures",
        timeframe: targetTimeframe,
        strategy_name: deployRun.strategy,
        allocated_capital: deployCapital,
        risk_per_trade_pct: deployRisk,
        params: strategyOverrides,
      };
      if (deployRun.strategy === "ml_signal_generator_v1") {
        const modelId = deployRun.params?.ml_generator_model_id;
        if (typeof modelId !== "string" || !modelId) {
          setDeployError("Run the backtest with an eligible generator model before deploying.");
          return;
        }
        await updatePaperConfig({
          ...config,
          is_active: true,
          ml_generator_model_id: modelId,
          telegram_enabled: deployTelegram,
          report_enabled: true,
        });
        setRunNotice(`Paper Trading configuration saved for generator model ${modelId}.`);
        closeDeployModal();
        return;
      }
      const res = await deployPaperStrategy({ ...config, send_telegram: deployTelegram });
      const delivery = deployTelegram ? parseTelegramDeliveryStatus(res.telegramAlert ?? res.telegram) : null;
      const message = res.message || `Strategy '${deployRun.strategy}' deployed to Paper Trading.`;
      const evaluation = res.status === "partial" ? " Configuration was saved, but immediate evaluation failed." : "";
      const notification = delivery?.status === "success" ? " Telegram notification delivered."
        : delivery?.status === "failed" ? " Telegram notification failed."
        : delivery ? " Telegram delivery status is unconfirmed." : "";
      if (evaluation || (delivery && delivery.status !== "success")) {
        setDeployWarning(message + evaluation + notification);
      } else {
        setRunNotice(message + notification);
      }
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
        setSelectedId((current) => {
          const preferred = current ?? selectedEvidenceRunId;
          return nextRuns.find((run) => run.id === preferred)?.id ?? nextRuns[0]?.id ?? null;
        });
      }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt, selectedEvidenceRunId]);

  useEffect(() => {
    const controller = new AbortController();
    fetchBackfilledSymbols(controller.signal).then((symbols) => {
      if (controller.signal.aborted) return;
      setAvailableSymbols(symbols);
    }).catch(() => {});
    fetchGeneratorModels(controller.signal).then((models) => {
      if (controller.signal.aborted) return;
      setGeneratorModels(models);
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

  const matchingGeneratorModels = generatorModels.filter(
    (m) =>
      m.eligible_for_paper &&
      (!m.symbol || m.symbol === symbol.trim().toUpperCase()) &&
      (!m.market || m.market === market) &&
      (!m.timeframe || m.timeframe === timeframe)
  );

  useEffect(() => {
    if (strategyId === "ml_signal_generator_v1" && matchingGeneratorModels.length > 0) {
      if (!selectedGeneratorModelId || !matchingGeneratorModels.some((m) => m.model_id === selectedGeneratorModelId)) {
        setSelectedGeneratorModelId(matchingGeneratorModels[0].model_id);
      }
    }
  }, [strategyId, symbol, market, timeframe, matchingGeneratorModels, selectedGeneratorModelId]);

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

  const supportsPaperEvidence = details != null && details.totalTrades === trades.length &&
    (SUPPORTED_PAPER_TIMEFRAMES as readonly string[]).includes(details.timeframe) &&
    (details.strategy !== "ml_signal_generator_v1" || typeof details.params?.ml_generator_model_id === "string");
  const summary = details ?? (selectedId == null ? null : runs.find((run) => run.id === selectedId) ?? null);

  const reviewRun = () => {
    const normalizedSymbol = symbol.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{1,30}$/.test(normalizedSymbol)) { setRunError("Enter a valid symbol using letters, numbers, dot, underscore, or hyphen."); return; }
    const capital = Number(initialCapital);
    if (!Number.isFinite(capital) || capital < 100) { setRunError("Initial capital must be at least 100."); return; }
    const risk = Number(riskPerTrade);
    if (!Number.isFinite(risk) || risk < 0.1 || risk > 10) { setRunError("Risk per trade must be between 0.1% and 10%."); return; }
    if (!activeStrategy) { setRunError("Select a strategy before running a backtest."); return; }
    if (strategyId === "ml_signal_generator_v1" && !selectedGeneratorModelId) { setRunError("Select an eligible ML generator model before running this strategy."); return; }
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
      ...(strategyId === "ml_signal_generator_v1" ? { ml_generator_model_id: selectedGeneratorModelId } : {}),
    });
  };

  const confirmRun = async () => {
    if (!pendingRun || runBusy) return;
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
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Backtest Studio</strong><p>Run a strategy simulation, then review a saved result to prepare a paper deployment.</p></div><span className="panel-counter">CONFIRMED RUNS</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Backtest Studio</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {runError && <div className="api-error" role="alert"><div><strong>Backtest run failed</strong><p>{runError}</p></div><button className="button button-secondary" onClick={() => setRunError(null)}>Dismiss</button></div>}
    {runNotice && <p className="control-feedback control-success" role="status">{runNotice}</p>}
    {deployWarning && <p className="control-feedback control-warning" role="status">{deployWarning}</p>}
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
        {strategyId === "ml_signal_generator_v1" && <label className="screener-control-field">ML generator model<select aria-label="ML generator model" value={selectedGeneratorModelId} onChange={(event) => setSelectedGeneratorModelId(event.target.value)} required><option value="">Select an eligible generator model</option>{matchingGeneratorModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id} ({m.model_type})</option>)}</select>{matchingGeneratorModels.length === 0 && <span role="alert" style={{ fontSize: "0.8rem", color: "#e53e3e" }}>No eligible generator models available for {symbol || "symbol"} {market} {timeframe}.</span>}</label>}
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
      <div className="screener-control-footer" style={{ display: "flex", gap: "8px", flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button
          className="button button-secondary"
          type="button"
          disabled={loading || runBusy || !strategies.length || !activeStrategy}
          onClick={handleOpenWalkForward}
        >
          <TrendingUp size={14} /> Walk-Forward Analysis
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={loading || runBusy || !strategies.length}
          onClick={handleOpenRunAll}
        >
          <Sparkles size={14} /> Benchmark All Strategies
        </button>
        <button className="button button-primary" type="button" disabled={loading || runBusy || !strategies.length || !activeStrategy} onClick={reviewRun}>
          Review backtest run
        </button>
      </div>
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
          {details && (
            <button
              className="button button-secondary button-sm"
              type="button"
              disabled={aiInsightBusy}
              onClick={() => void handleOpenAiInsight(details)}
            >
              <BrainCircuit size={14} /> AI Insight
            </button>
          )}
          {canRunBacktest && details && onSelectPaperEvidence && supportsPaperEvidence && (
            <button className="button button-secondary button-sm" type="button" onClick={() => onSelectPaperEvidence(details)}>
              {details.id === selectedEvidenceRunId ? "Open paper readiness" : "Use run for paper readiness"}
            </button>
          )}
          {canRunBacktest && details && (!onSelectPaperEvidence || supportsPaperEvidence) && (
            <button
              className="button button-primary button-sm"
              disabled={!paperReady || (Boolean(onSelectPaperEvidence) && selectedEvidenceRunId !== details.id)}
              title="Select this run and complete paper readiness before deployment"
              type="button"
              onClick={(e) => openDeployModal(details, e.currentTarget)}
            >
              Deploy to Paper Trading
            </button>
          )}
        </div>
      </div>
      {canRunBacktest && details && onSelectPaperEvidence && !supportsPaperEvidence && (
        <p className="control-feedback control-warning">{details.totalTrades !== trades.length
          ? "This run has an incomplete trade ledger. Run the backtest again to produce complete evidence."
          : "For paper readiness, run a backtest on 15m, 1h, 4h, or 1d and select an eligible model if the strategy uses an ML generator."}</p>
      )}
      {detailError && <p role="alert" className="error-banner">{detailError}</p>}
      {detailLoading ? <p className="empty-state" role="status">Loading selected run details…</p> : details ? <>
        <div className="paper-risk-grid"><div><span>Run</span><strong>#{details.id} · {details.symbol}</strong></div><div><span>Strategy</span><strong>{strategyNames.get(details.strategy) ?? details.strategy}</strong></div><div><span>Market / timeframe</span><strong>{details.market} · {details.timeframe}</strong></div><div><span>Data segment</span><strong>{details.dataSegment}</strong></div><div><span>Win rate</span><strong>{details.winRatePct == null ? "—" : `${pct.format(details.winRatePct)}%`}</strong></div><div><span>Profit factor</span><strong>{moneyish(details.profitFactor)}</strong></div><div><span>Expectancy</span><strong>{details.expectancyR == null ? "—" : `${numeric.format(details.expectancyR)} R`}</strong></div><div><span>Max drawdown</span><strong>{details.maxDrawdownR == null ? "—" : `${numeric.format(details.maxDrawdownR)} R`}</strong></div></div>
        {sortedTrades.length > 0 && <EquityDrawdownChart trades={sortedTrades} />}
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
      <div className="panel-heading"><div><h2 id="backtest-confirm-title">Confirm backtest run</h2><p>This simulation saves research evidence in Backtest Studio. Paper activation is a separate step.</p></div></div>
      <p><strong>Configuration:</strong> {[pendingRun.symbol, pendingRun.market, pendingRun.timeframe, pendingRun.strategy, pendingRun.data_segment].join(" · ")}</p>
      <p><strong>Capital / risk:</strong> {numeric.format(pendingRun.initial_capital)} / {pendingRun.risk_per_trade_pct}% per trade</p>
      {pendingRun.ml_generator_model_id && <p><strong>Generator model:</strong> {pendingRun.ml_generator_model_id}</p>}
      <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={runBusy} onClick={() => setPendingRun(null)}>Cancel</button><button className="button button-primary" type="button" disabled={runBusy} onClick={() => void confirmRun()}>{runBusy ? "Running simulation…" : "Run backtest"}</button></div>
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
                disabled={deployBusy || Boolean(onSelectPaperEvidence)}
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
              <strong>Enable Telegram alerts</strong>
              <small>{deployRun.strategy === "ml_signal_generator_v1"
                ? "Send entry alerts when the generator opens paper positions."
                : "Send a deployment notification and subsequent paper entry alerts."}</small>
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
    {walkForwardOpen && (
      <dialog className="modal-dialog" open style={{ maxWidth: "680px", width: "95%" }}>
        <div className="panel-heading">
          <div>
            <h2>Walk-Forward Analysis</h2>
            <p>{symbol} · {market} · {timeframe} · {strategyId}</p>
          </div>
          <button type="button" className="icon-button" onClick={() => setWalkForwardOpen(false)} aria-label="Close dialog">
            <X size={16} />
          </button>
        </div>
        <div style={{ padding: "16px 0" }}>
          <div className="screener-control-grid">
            <label className="screener-control-field">
              Folds (K-fold)
              <input
                type="number"
                min={2}
                max={10}
                value={walkForwardFolds}
                disabled={walkForwardBusy}
                onChange={(e) => setWalkForwardFolds(Math.max(2, Math.min(10, Number(e.target.value))))}
              />
            </label>
            <label className="screener-alert-toggle" style={{ marginTop: "18px" }}>
              <input
                type="checkbox"
                checked={walkForwardRolling}
                disabled={walkForwardBusy}
                onChange={(e) => setWalkForwardRolling(e.target.checked)}
              />
              <span><strong>Rolling window mode</strong><small>Evaluates walk-forward efficiency over sequential rolling test windows.</small></span>
            </label>
          </div>

          <div style={{ margin: "16px 0", display: "flex", justifyContent: "flex-end", gap: "8px" }}>
            <button
              type="button"
              className="button button-primary"
              disabled={walkForwardBusy}
              onClick={handleExecuteWalkForward}
            >
              {walkForwardBusy ? "Running analysis…" : "Execute Walk-Forward Test"}
            </button>
          </div>

          {walkForwardError && <p className="control-feedback control-error" role="alert">{walkForwardError}</p>}

          {walkForwardResult && (
            <div className="panel-subsection" style={{ marginTop: "16px" }}>
              <div className="panel-heading">
                <div>
                  <h3>Analysis Outcome</h3>
                  <p>Walk-Forward Efficiency (WFE) evaluates robustness across out-of-sample folds.</p>
                </div>
                {walkForwardResult.walk_forward_efficiency != null && (
                  <span className={`status-pill ${walkForwardResult.walk_forward_efficiency >= 0.5 ? "status-positive" : "status-warning"}`}>
                    WFE: {(walkForwardResult.walk_forward_efficiency * 100).toFixed(1)}%
                  </span>
                )}
              </div>
              {Array.isArray(walkForwardResult.folds) && walkForwardResult.folds.length > 0 ? (
                <div className="table-scroll" style={{ maxHeight: "240px", overflowY: "auto" }}>
                  <table className="status-table">
                    <thead>
                      <tr><th>Fold</th><th>Train Trades</th><th>Train PF</th><th>Test Trades</th><th>Test PF</th><th>Test Expectancy</th></tr>
                    </thead>
                    <tbody>
                      {walkForwardResult.folds.map((f: any, idx: number) => (
                        <tr key={idx}>
                          <td>#{f.fold ?? idx + 1}</td>
                          <td>{f.train_metrics?.total_trades ?? f.train_trades ?? "—"}</td>
                          <td>{moneyish(f.train_metrics?.profit_factor ?? f.train_pf)}</td>
                          <td>{f.test_metrics?.total_trades ?? f.test_trades ?? "—"}</td>
                          <td className={(f.test_metrics?.profit_factor ?? f.test_pf ?? 0) >= 1 ? "paper-positive" : "paper-negative"}>
                            {moneyish(f.test_metrics?.profit_factor ?? f.test_pf)}
                          </td>
                          <td>{numeric.format(f.test_metrics?.expectancy_r ?? f.test_expectancy ?? 0)} R</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty-state">No fold details returned.</p>
              )}
            </div>
          )}
        </div>
      </dialog>
    )}
    {runAllOpen && (
      <dialog className="modal-dialog" open style={{ maxWidth: "780px", width: "95%" }}>
        <div className="panel-heading">
          <div>
            <h2>Benchmark All Strategies</h2>
            <p>Runs all registered strategies on {symbol} ({market} · {timeframe}) ranked by quantitative performance.</p>
          </div>
          <button type="button" className="icon-button" onClick={() => setRunAllOpen(false)} aria-label="Close dialog">
            <X size={16} />
          </button>
        </div>
        <div style={{ padding: "16px 0" }}>
          {paperReady && promotionPolicy?.enabled && dataSegment === "train" ? (
            <label className="screener-alert-toggle">
              <input type="checkbox" checked={benchmarkPromotionConsent} disabled={runAllBusy}
                onChange={(event) => setBenchmarkPromotionConsent(event.target.checked)} />
              <span>Allow auto-promotion for this benchmark under the saved policy. {promotionPolicy.send_telegram ? "The saved policy can send a Telegram deployment alert." : "Telegram alerts are off in the saved policy."}</span>
            </label>
          ) : <p className="page-description">This benchmark runs in research mode. Auto-promotion requires paper readiness, an enabled promotion policy, and the Train segment.</p>}
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "12px" }}>
            <button
              type="button"
              className="button button-primary"
              disabled={runAllBusy}
              onClick={handleExecuteRunAll}
            >
              {runAllBusy ? "Simulating all strategies…" : "Start Multi-Strategy Benchmark"}
            </button>
          </div>

          {runAllError && <p className="control-feedback control-error" role="alert">{runAllError}</p>}

          {runAllResult && (
            <div>
              {runAllResult.autoPromotion?.status && <p className="control-feedback" role="status">
                <strong>Auto-promotion</strong> · {runAllResult.autoPromotion.status.replaceAll("_", " ")}
                {runAllResult.autoPromotion.strategy ? ` · ${runAllResult.autoPromotion.strategy}` : ""}
              </p>}
              <div className="table-scroll" style={{ maxHeight: "320px", overflowY: "auto" }}>
                <table className="status-table">
                  <thead>
                    <tr><th>Rank</th><th>Strategy</th><th>Category</th><th>Trades</th><th>Win Rate</th><th>Profit Factor</th><th>Expectancy</th></tr>
                  </thead>
                  <tbody>
                    {runAllResult.allResults.map((item, idx) => (
                      <tr key={item.strategy} style={idx === 0 ? { backgroundColor: "rgba(16, 185, 129, 0.08)" } : undefined}>
                        <td><strong>#{idx + 1}</strong></td>
                        <td>
                          <strong>{item.displayName || item.strategy}</strong>
                          <div><code>{item.strategy}</code></div>
                        </td>
                        <td>{item.category}</td>
                        <td>{item.metrics?.total_trades ?? 0}</td>
                        <td>{item.metrics?.win_rate_pct != null ? `${pct.format(item.metrics.win_rate_pct)}%` : "—"}</td>
                        <td className={(item.metrics?.profit_factor ?? 0) >= 1 ? "paper-positive" : "paper-negative"}>
                          {moneyish(item.metrics?.profit_factor)}
                        </td>
                        <td>{item.metrics?.expectancy_r != null ? `${numeric.format(item.metrics.expectancy_r)} R` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </dialog>
    )}
    {aiInsightOpen && (
      <dialog className="modal-dialog" open style={{ maxWidth: "650px", width: "95%" }}>
        <div className="panel-heading">
          <div>
            <h2>AI Quantitative Insight</h2>
            <p>Institutional diagnosis &amp; regime assessment</p>
          </div>
          <button type="button" className="icon-button" onClick={() => setAiInsightOpen(false)} aria-label="Close dialog">
            <X size={16} />
          </button>
        </div>
        <div style={{ padding: "16px 0" }}>
          {aiInsightBusy ? (
            <p className="empty-state">Synthesizing institutional quantitative diagnosis…</p>
          ) : aiInsightError ? (
            <p className="control-feedback control-error" role="alert">{aiInsightError}</p>
          ) : aiInsightResult?.insight ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {aiInsightResult.insight.regime_diagnosis && (
                <div className="migration-notice">
                  <div className="notice-icon"><BrainCircuit size={18} /></div>
                  <div>
                    <h2>Market Regime Diagnosis</h2>
                    <p>{aiInsightResult.insight.regime_diagnosis}</p>
                  </div>
                </div>
              )}
              {aiInsightResult.insight.summary && (
                <div>
                  <strong>Strategy Evaluation:</strong>
                  <p className="page-description" style={{ margin: "4px 0" }}>{aiInsightResult.insight.summary}</p>
                </div>
              )}
              {aiInsightResult.insight.actionable_takeaway && (
                <div>
                  <strong>Actionable Quantitative Takeaway:</strong>
                  <p className="page-description" style={{ margin: "4px 0", color: "var(--color-accent)" }}>
                    {aiInsightResult.insight.actionable_takeaway}
                  </p>
                </div>
              )}
              {Array.isArray(aiInsightResult.insight.strengths) && aiInsightResult.insight.strengths.length > 0 && (
                <div>
                  <strong style={{ color: "var(--color-positive)" }}>Observed Strengths:</strong>
                  <ul style={{ margin: "4px 0", paddingLeft: "20px", fontSize: "12px" }}>
                    {aiInsightResult.insight.strengths.map((s, idx) => <li key={idx}>{s}</li>)}
                  </ul>
                </div>
              )}
              {Array.isArray(aiInsightResult.insight.risks) && aiInsightResult.insight.risks.length > 0 && (
                <div>
                  <strong style={{ color: "var(--color-negative)" }}>Identified Risks:</strong>
                  <ul style={{ margin: "4px 0", paddingLeft: "20px", fontSize: "12px" }}>
                    {aiInsightResult.insight.risks.map((r, idx) => <li key={idx}>{r}</li>)}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="empty-state">No insight available.</p>
          )}
        </div>
      </dialog>
    )}
    <PromotionStatusPanel onPolicyLoaded={setPromotionPolicy} />
  </div>;
}
