import { useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, CheckCircle, Plus, RefreshCw, ShieldCheck, WalletCards, X } from "lucide-react";
import { DeploymentParameters, type DeploymentOverrides } from "./components/DeploymentParameters";
import { TradingChart } from "./components/TradingChart";
import { PortfolioRiskDesk } from "./components/PortfolioRiskDesk";
import { ExecutionBlotter } from "./components/ExecutionBlotter";
import { useAuth } from "./AuthGate";
import { fetchBacktestStrategies } from "./lib/backtestStudio";
import { fetchBackfilledSymbols } from "./lib/masterData";
import { ApiError } from "./lib/api";
import { trapFocus } from "./lib/dialog";
import {
  batchUpdatePaperStrategy,
  cancelPaperOrder,
  closePaperPosition,
  deployPaperStrategy,
  fetchPaperTradingSnapshot,
  PaperTradingActionError,
  parseTelegramDeliveryStatus,
  resetPaperCircuitBreaker,
  syncPaperMarketTick,
  tripPaperCircuitBreaker,
  patchPaperDeployment,
  fetchPaperReportForPeriod,
  type PaperDeploymentSettings,
  type PaperMetrics,
  updatePaperConfig,
  fetchGeneratorModels,
  type GeneratorModelSummary,
  updatePaperReportPreference,
  archivePaperConfig,
  purgePaperConfig,
  type PaperConfig,
  type PaperOrder,
  type PaperPosition,
  type PaperTradingSnapshot,
} from "./lib/paperTrading";

const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
function dateLabel(value: string | number | null | undefined) {
  if (value == null || value === "") return "—";
  const parsed = typeof value === "number" ? value : Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : String(value);
}
function pnlClass(value: number | null | undefined) {
  return value == null || value === 0 ? "" : value > 0 ? "paper-positive" : "paper-negative";
}

type SortDirection = "asc" | "desc";
type StrategySummarySortField = "strategy" | "pairCount" | "totalTrades" | "wins" | "losses" | "winRatePct" | "cumulativeR" | "expectancyR" | "netPnl";
type PairDetailSortField = "symbol" | "marketTimeframe" | "totalTrades" | "wins" | "losses" | "winRatePct" | "cumulativeR" | "expectancyR" | "netPnl" | "realizedEquity";

function compareSortValues(a: string | number | null | undefined, b: string | number | null | undefined, direction: SortDirection) {
  const factor = direction === "asc" ? 1 : -1;
  const aMissing = a == null || (typeof a === "number" && !Number.isFinite(a));
  const bMissing = b == null || (typeof b === "number" && !Number.isFinite(b));
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  if (typeof a === "number" && typeof b === "number") {
    return (a - b) * factor;
  }
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" }) * factor;
}

function SortableHeader<T extends string>({
  field,
  currentField,
  direction,
  label,
  onSort,
}: {
  field: T;
  currentField: T;
  direction: SortDirection;
  label: string;
  onSort: (field: T) => void;
}) {
  const isActive = field === currentField;
  return (
    <th scope="col" aria-sort={isActive ? (direction === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        className={"table-sort-button" + (isActive ? " is-active" : "")}
        onClick={() => onSort(field)}
        aria-label={`Sort by ${label}`}
      >
        <span>{label}</span>
        {isActive ? (
          direction === "asc" ? <ArrowUp size={13} className="sort-icon-active" aria-hidden="true" /> : <ArrowDown size={13} className="sort-icon-active" aria-hidden="true" />
        ) : (
          <ArrowUpDown size={12} className="sort-icon-idle" aria-hidden="true" />
        )}
      </button>
    </th>
  );
}

interface PaperTradingPanelProps {
  paperReady?: boolean;
  onDeployPreparedStrategy?: () => void;
  initialStrategyId?: string | null;
  onInitialStrategyApplied?: () => void;
}

export function PaperTradingPanel({ initialStrategyId, onInitialStrategyApplied, paperReady = true, onDeployPreparedStrategy }: PaperTradingPanelProps = {}) {
  const auth = useAuth();
  const canTrade = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());

  const [data, setData] = useState<PaperTradingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [actionWarning, setActionWarning] = useState<string | null>(null);

  // PT-07: Cancel paper order
  const [selectedOrderForCancel, setSelectedOrderForCancel] = useState<PaperOrder | null>(null);
  const [cancellingOrderId, setCancellingOrderId] = useState<number | null>(null);
  const cancelOrderDialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelOrderConfirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const cancelOrderTriggerRef = useRef<HTMLElement | null>(null);

  // PT-08: Manually close position
  const [selectedPositionForClose, setSelectedPositionForClose] = useState<PaperPosition | null>(null);
  const [closingPositionId, setClosingPositionId] = useState<number | null>(null);
  const [chartPosition, setChartPosition] = useState<PaperPosition | null>(null);
  const closePositionDialogRef = useRef<HTMLDialogElement | null>(null);
  const closePositionConfirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const closePositionTriggerRef = useRef<HTMLElement | null>(null);

  // PT-09: Deploy strategy modal
  const [showDeployModal, setShowDeployModal] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const deployDialogRef = useRef<HTMLDialogElement | null>(null);
  const deploySymbolInputRef = useRef<HTMLInputElement | null>(null);
  const deployTriggerRef = useRef<HTMLElement | null>(null);
  const [parameterDraft, setParameterDraft] = useState<DeploymentOverrides | null>(null);
  const [editingConfig, setEditingConfig] = useState<PaperConfig | null>(null);
  const [pendingArchive, setPendingArchive] = useState<PaperConfig | null>(null);
  const [pendingPurge, setPendingPurge] = useState<PaperConfig | null>(null);
  const [reportDays, setReportDays] = useState(0);
  const [periodResult, setPeriodResult] = useState<{ days: number; metrics?: PaperMetrics; failed?: true } | null>(null);
  const [reportMarket, setReportMarket] = useState("");
  const [reportTimeframe, setReportTimeframe] = useState("");
  const [reportSymbol, setReportSymbol] = useState("");
  const [reportStrategy, setReportStrategy] = useState("");
  const [selectedStrategyKey, setSelectedStrategyKey] = useState<string | null>(null);
  const [strategySortField, setStrategySortField] = useState<StrategySummarySortField>("netPnl");
  const [strategySortDir, setStrategySortDir] = useState<SortDirection>("desc");
  const [pairSortField, setPairSortField] = useState<PairDetailSortField>("netPnl");
  const [pairSortDir, setPairSortDir] = useState<SortDirection>("desc");
  const [savingPreference, setSavingPreference] = useState(false);
  const [togglingConfigId, setTogglingConfigId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [batchingStrategy, setBatchingStrategy] = useState<string | null>(null);

  const [availableSymbols, setAvailableSymbols] = useState<string[]>(["BTCUSDT"]);
  const [deploySymbol, setDeploySymbol] = useState("BTCUSDT");
  const [deployMarket, setDeployMarket] = useState<"spot" | "futures">("spot");
  const [deployTimeframe, setDeployTimeframe] = useState<"15m" | "1h" | "4h" | "1d">("1d");
  const [catalog, setCatalog] = useState<string[]>([]);
  const [catalogError, setCatalogError] = useState(false);
  const [deployStrategy, setDeployStrategy] = useState("confluence_ensemble_v1");
  const [deployCapital, setDeployCapital] = useState(5000);
  const [deployRisk, setDeployRisk] = useState(1.0);
  const [deployTelegram, setDeployTelegram] = useState(false);
  const [generatorModels, setGeneratorModels] = useState<GeneratorModelSummary[]>([]);
  const [selectedGeneratorModelId, setSelectedGeneratorModelId] = useState("");

  const matchingGeneratorModels = generatorModels.filter(
    (m) =>
      m.eligible_for_paper &&
      (!m.symbol || m.symbol === deploySymbol.trim().toUpperCase()) &&
      (!m.market || m.market === deployMarket) &&
      (!m.timeframe || m.timeframe === deployTimeframe)
  );

  useEffect(() => {
    if (deployStrategy === "ml_signal_generator_v1" && matchingGeneratorModels.length > 0) {
      if (!selectedGeneratorModelId || !matchingGeneratorModels.some((m) => m.model_id === selectedGeneratorModelId)) {
        setSelectedGeneratorModelId(matchingGeneratorModels[0].model_id);
      }
    }
  }, [deployStrategy, deploySymbol, deployMarket, deployTimeframe, matchingGeneratorModels, selectedGeneratorModelId]);

  // PT-10: Trip / reset circuit breaker
  const [breakerAction, setBreakerAction] = useState<"trip" | "reset" | null>(null);
  const [breakerBusy, setBreakerBusy] = useState(false);
  const breakerDialogRef = useRef<HTMLDialogElement | null>(null);
  const breakerConfirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const breakerTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchPaperTradingSnapshot(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setError("Your session has expired. Sign in again to view paper trading data.");
        } else if (cause instanceof ApiError && cause.status === 403) {
          setError("Your account cannot view paper trading data.");
        } else {
          setError("Paper trading data could not be loaded. Check API and database health, then retry.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    if (canTrade && paperReady && initialStrategyId) {
      setDeployStrategy(initialStrategyId);
      setShowDeployModal(true);
      onInitialStrategyApplied?.();
    }
  }, [canTrade, paperReady, initialStrategyId, onInitialStrategyApplied]);

  useEffect(() => {
    if (!showDeployModal) return;
    const controller = new AbortController();
    setCatalog([]);
    setCatalogError(false);
    fetchBacktestStrategies(controller.signal).then(({ strategies }) => {
      if (controller.signal.aborted) return;
      const ids = strategies.map((strategy) => strategy.id);
      setCatalog(ids);
      setDeployStrategy((selected) => ids.includes(selected) ? selected : ids[0] ?? "");
    }).catch(() => {
      if (!controller.signal.aborted) setCatalogError(true);
    });
    fetchBackfilledSymbols(controller.signal).then((symbols) => {
      if (controller.signal.aborted) return;
      setAvailableSymbols(symbols);
    }).catch(() => {});
    fetchGeneratorModels(controller.signal).then((models) => {
      if (controller.signal.aborted) return;
      setGeneratorModels(models);
    }).catch(() => {});
    return () => controller.abort();
  }, [showDeployModal]);

  // Modal lifecycles
  useEffect(() => {
    if (selectedOrderForCancel && cancelOrderDialogRef.current) {
      if (!cancelOrderDialogRef.current.open) {
        if (typeof cancelOrderDialogRef.current.showModal === "function") {
          cancelOrderDialogRef.current.showModal();
        } else {
          cancelOrderDialogRef.current.setAttribute("open", "");
        }
      }
      cancelOrderConfirmButtonRef.current?.focus();
    }
  }, [selectedOrderForCancel]);

  useEffect(() => {
    if (selectedPositionForClose && closePositionDialogRef.current) {
      if (!closePositionDialogRef.current.open) {
        if (typeof closePositionDialogRef.current.showModal === "function") {
          closePositionDialogRef.current.showModal();
        } else {
          closePositionDialogRef.current.setAttribute("open", "");
        }
      }
      closePositionConfirmButtonRef.current?.focus();
    }
  }, [selectedPositionForClose]);

  useEffect(() => {
    if (showDeployModal && deployDialogRef.current) {
      if (!deployDialogRef.current.open) {
        if (typeof deployDialogRef.current.showModal === "function") {
          deployDialogRef.current.showModal();
        } else {
          deployDialogRef.current.setAttribute("open", "");
        }
      }
      deploySymbolInputRef.current?.focus();
    }
  }, [showDeployModal]);

  useEffect(() => {
    if (breakerAction && breakerDialogRef.current) {
      if (!breakerDialogRef.current.open) {
        if (typeof breakerDialogRef.current.showModal === "function") {
          breakerDialogRef.current.showModal();
        } else {
          breakerDialogRef.current.setAttribute("open", "");
        }
      }
      breakerConfirmButtonRef.current?.focus();
    }
  }, [breakerAction]);

  // Dialog dismissal helpers
  const closeCancelOrderModal = () => {
    if (cancelOrderDialogRef.current?.open) {
      if (typeof cancelOrderDialogRef.current.close === "function") {
        cancelOrderDialogRef.current.close();
      } else {
        cancelOrderDialogRef.current.removeAttribute("open");
      }
    }
    setSelectedOrderForCancel(null);
    cancelOrderTriggerRef.current?.focus();
  };

  const closeClosePositionModal = () => {
    if (closePositionDialogRef.current?.open) {
      if (typeof closePositionDialogRef.current.close === "function") {
        closePositionDialogRef.current.close();
      } else {
        closePositionDialogRef.current.removeAttribute("open");
      }
    }
    setSelectedPositionForClose(null);
    closePositionTriggerRef.current?.focus();
  };

  const closeDeployModal = () => {
    if (deployDialogRef.current?.open) {
      if (typeof deployDialogRef.current.close === "function") {
        deployDialogRef.current.close();
      } else {
        deployDialogRef.current.removeAttribute("open");
      }
    }
    setShowDeployModal(false);
    setDeployError(null);
    deployTriggerRef.current?.focus();
  };

  const closeBreakerModal = () => {
    if (breakerDialogRef.current?.open) {
      if (typeof breakerDialogRef.current.close === "function") {
        breakerDialogRef.current.close();
      } else {
        breakerDialogRef.current.removeAttribute("open");
      }
    }
    setBreakerAction(null);
    breakerTriggerRef.current?.focus();
  };

  // PT-07: Cancel order handler
  const handleExecuteCancelOrder = async () => {
    if (!selectedOrderForCancel || cancellingOrderId !== null) return;
    const orderId = selectedOrderForCancel.id;
    setCancellingOrderId(orderId);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      await cancelPaperOrder(orderId);
      setActionSuccess(`Paper order #${orderId} was cancelled successfully.`);
      closeCancelOrderModal();
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : `Unable to cancel order #${orderId}.`);
    } finally {
      setCancellingOrderId(null);
    }
  };

  // PT-08: Close position handler
  const handleExecuteClosePosition = async () => {
    if (!selectedPositionForClose || closingPositionId !== null) return;
    const posId = selectedPositionForClose.id;
    setClosingPositionId(posId);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      await closePaperPosition(posId);
      setActionSuccess(`Paper position #${posId} (${selectedPositionForClose.symbol}) was closed at market price.`);
      closeClosePositionModal();
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : `Unable to close position #${posId}.`);
    } finally {
      setClosingPositionId(null);
    }
  };

  // PT-09: Deploy strategy handler
  const handleExecuteDeploy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!paperReady || deployBusy || !catalog.includes(deployStrategy)) return;
    setDeployError(null);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);

    const sym = deploySymbol.trim().toUpperCase();
    if (!sym) {
      setDeployError("Symbol is required.");
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

    if (deployStrategy === "ml_signal_generator_v1") {
      if (!selectedGeneratorModelId) {
        setDeployError("Select an eligible ML generator model for this deployment.");
        return;
      }
      setDeployBusy(true);
      try {
        await updatePaperConfig({
          symbol: sym,
          market: deployMarket,
          timeframe: deployTimeframe,
          strategy_name: deployStrategy,
          is_active: true,
          allocated_capital: deployCapital,
          risk_per_trade_pct: deployRisk,
          ml_generator_model_id: selectedGeneratorModelId,
          telegram_enabled: deployTelegram,
          report_enabled: true,
        });
        setActionSuccess(`Strategy 'ml_signal_generator_v1' deployed to Paper Trading with model ${selectedGeneratorModelId}.`);
        closeDeployModal();
        setAttempt((v) => v + 1);
      } catch (cause) {
        setDeployError(cause instanceof PaperTradingActionError ? cause.message : "Strategy deployment failed.");
      } finally {
        setDeployBusy(false);
      }
      return;
    }

    setDeployBusy(true);
    try {
      const res = await deployPaperStrategy({
        symbol: sym,
        market: deployMarket,
        timeframe: deployTimeframe,
        strategy_name: deployStrategy,
        allocated_capital: deployCapital,
        risk_per_trade_pct: deployRisk,
        send_telegram: deployTelegram,
      });

      const isEvaluationPartial = res.status === "partial";
      const baseDeployMsg = res.message || `Strategy '${deployStrategy}' deployed to Live Paper Trading.`;

      if (deployTelegram) {
        const rawAlert = res.telegramAlert ?? res.telegram;
        const delivery = parseTelegramDeliveryStatus(rawAlert);

        if (delivery.status === "success") {
          if (isEvaluationPartial) {
            setActionWarning(
              `Strategy '${deployStrategy}' configuration was saved and Telegram notification delivered, but immediate evaluation failed.`
            );
          } else {
            setActionSuccess(
              `${baseDeployMsg} Telegram notification delivered successfully.`
            );
          }
        } else if (delivery.status === "failed") {
          const errDetail = delivery.error ? `: ${delivery.error}` : "";
          if (isEvaluationPartial) {
            setActionWarning(
              `Strategy '${deployStrategy}' configuration was saved, but immediate evaluation failed and Telegram notification failed${errDetail}.`
            );
          } else {
            setActionWarning(
              `Strategy '${deployStrategy}' deployed to Paper Trading, but Telegram notification failed${errDetail}.`
            );
          }
        } else {
          // delivery.status === "unconfirmed"
          if (isEvaluationPartial) {
            setActionWarning(
              `Strategy '${deployStrategy}' configuration was saved, but immediate evaluation failed and Telegram delivery status is unconfirmed.`
            );
          } else {
            setActionWarning(
              `Strategy '${deployStrategy}' deployed to Paper Trading, but Telegram delivery status is unconfirmed.`
            );
          }
        }
      } else {
        if (isEvaluationPartial) {
          setActionWarning(
            res.message
              ? `${res.message} Configuration was saved, but immediate evaluation failed.`
              : `Strategy '${deployStrategy}' configuration was saved, but immediate evaluation failed.`
          );
        } else {
          setActionSuccess(baseDeployMsg);
        }
      }
      closeDeployModal();
      setAttempt((v) => v + 1);
    } catch (cause) {
      setDeployError(cause instanceof PaperTradingActionError ? cause.message : "Strategy deployment failed.");
    } finally {
      setDeployBusy(false);
    }
  };

  const saveDeploymentSettings = async (
    config: PaperConfig,
    settings: PaperDeploymentSettings,
    message: string,
  ) => {
    if (!canTrade || savingPreference || togglingConfigId !== null || (settings.is_active === true && !paperReady)) return false;
    setTogglingConfigId(config.id);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      await patchPaperDeployment(config.id, settings);
      setActionSuccess(message);
      setAttempt((current) => current + 1);
      return true;
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Could not update the deployment.");
      return false;
    } finally {
      setTogglingConfigId(null);
    }
  };

  // PT-10: Circuit breaker handler
  const handleExecuteBreakerAction = async () => {
    if (!breakerAction || breakerBusy) return;
    setBreakerBusy(true);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      if (breakerAction === "trip") {
        await tripPaperCircuitBreaker("Manual emergency stop via Web Dashboard");
        setActionSuccess("Emergency stop activated. All new paper entries are halted.");
      } else {
        await resetPaperCircuitBreaker();
        setActionSuccess("Circuit breaker reset. New paper trade entries are re-enabled.");
      }
      closeBreakerModal();
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Circuit breaker operation failed.");
    } finally {
      setBreakerBusy(false);
    }
  };

  const archiveDeployment = async (config: PaperConfig) => {
    setTogglingConfigId(config.id);
    setActionError(null);
    setActionSuccess(null);
    try {
      await archivePaperConfig(config.id);
      setPendingArchive(null);
      setActionSuccess("Deployment archived. Existing positions and trade history are preserved.");
      setAttempt((current) => current + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Could not archive the deployment.");
    } finally {
      setTogglingConfigId(null);
    }
  };

  const purgeDeployment = async (config: PaperConfig) => {
    setTogglingConfigId(config.id);
    setActionError(null);
    setActionSuccess(null);
    try {
      await purgePaperConfig(config.id, !config.archived_at);
      setPendingPurge(null);
      setActionSuccess("Deployment permanently deleted.");
      setAttempt((current) => current + 1);
    } catch (cause) {
      setPendingPurge(null);
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Could not delete the deployment.");
    } finally {
      setTogglingConfigId(null);
    }
  };

  const saveDeployment = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editingConfig || togglingConfigId !== null) return;
    const { allocated_capital, risk_per_trade_pct } = editingConfig;
    if (await saveDeploymentSettings(editingConfig, { allocated_capital, risk_per_trade_pct, ...(parameterDraft !== null ? { params: parameterDraft } : {}) }, "Deployment settings saved.")) {
      setEditingConfig(null);
    }
  };

  useEffect(() => {
    if (!reportDays) return;
    const controller = new AbortController();
    setPeriodResult(null);
    fetchPaperReportForPeriod(reportDays, controller.signal)
      .then((metrics) => {
        if (!controller.signal.aborted) setPeriodResult({ days: reportDays, metrics });
      })
      .catch(() => {
        if (!controller.signal.aborted) setPeriodResult({ days: reportDays, failed: true });
      });
    return () => controller.abort();
  }, [reportDays, attempt]);

  const allMetrics = reportDays
    ? (periodResult?.days === reportDays ? periodResult.metrics : undefined)
    : data?.metrics;
  const configs = data?.configs ?? [];
  const symbols = [...new Set([
    ...configs.map((config) => config.symbol),
    ...(data?.metrics.byStrategy.flatMap((row) => row.symbol ? [row.symbol] : []) ?? []),
  ])].sort();
  const strategies = [...new Set([
    ...configs.filter((config) => !reportSymbol || config.symbol === reportSymbol).map((config) => config.strategy_name),
    ...(data?.metrics.byStrategy.filter((row) => !reportSymbol || row.symbol === reportSymbol).map((row) => row.strategy) ?? []),
  ])].sort();
  const scopedConfigs = configs.filter((config) =>
    (!reportSymbol || config.symbol === reportSymbol) && (!reportStrategy || config.strategy_name === reportStrategy),
  );
  const preferenceScopeSelected = Boolean(reportSymbol || reportStrategy);
  const filtered = preferenceScopeSelected || Boolean(reportMarket || reportTimeframe);
  const isReportMarketAndTimeframe = (market?: string, timeframe?: string) =>
    (!reportMarket || market === reportMarket) && (!reportTimeframe || timeframe === reportTimeframe);
  const reportRows = allMetrics?.byStrategy ?? [];
  const closedRows = reportRows.filter((row) =>
    (!reportSymbol || row.symbol === reportSymbol) && (!reportStrategy || row.strategy === reportStrategy) && isReportMarketAndTimeframe(row.market, row.timeframe),
  );
  const rows = [...closedRows];
  const configById = new Map(configs.map((config) => [config.id, config]));
  const shown = new Set(closedRows.map((row) => JSON.stringify([row.configId, row.symbol, row.strategy, row.market, row.timeframe])));
  for (const config of scopedConfigs) {
    if (!isReportMarketAndTimeframe(config.market, config.timeframe) || config.archived_at) continue;
    const key = JSON.stringify([config.id, config.symbol, config.strategy_name, config.market, config.timeframe]);
    if (!config.report_enabled || shown.has(key)) continue;
    shown.add(key);
    rows.push({
      symbol: config.symbol, strategy: config.strategy_name, configId: config.id, market: config.market, timeframe: config.timeframe, totalTrades: 0, wins: 0, losses: 0,
      netPnl: 0, cumulativeR: 0, winRatePct: 0, expectancyR: 0,
    });
  }
  rows.sort((a, b) => (a.symbol ?? "").localeCompare(b.symbol ?? "") || a.strategy.localeCompare(b.strategy));
  const total = rows.reduce(
    (sum, row) => ({
      totalTrades: sum.totalTrades + row.totalTrades,
      wins: sum.wins + row.wins,
      losses: sum.losses + row.losses,
      netPnl: sum.netPnl + row.netPnl,
      cumulativeR: sum.cumulativeR + row.cumulativeR,
    }),
    { totalTrades: 0, wins: 0, losses: 0, netPnl: 0, cumulativeR: 0 },
  );
  const m = allMetrics && filtered ? {
    ...total,
    winRatePct: total.totalTrades ? total.wins / total.totalTrades * 100 : 0,
    expectancyR: total.totalTrades ? total.cumulativeR / total.totalTrades : 0,
    byStrategy: rows,
  } : allMetrics && { ...allMetrics, byStrategy: rows };

  const strategyGroupMap = new Map<string, {
    key: string;
    strategy: string;
    pairCount: number;
    totalTrades: number;
    wins: number;
    losses: number;
    cumulativeR: number;
    expectancyR: number;
    winRatePct: number;
    netPnl: number;
    pairs: typeof rows;
  }>();
  for (const row of m?.byStrategy ?? []) {
    const key = row.strategy;
    let group = strategyGroupMap.get(key);
    if (!group) {
      group = {
        key,
        strategy: row.strategy,
        pairCount: 0,
        totalTrades: 0,
        wins: 0,
        losses: 0,
        cumulativeR: 0,
        expectancyR: 0,
        winRatePct: 0,
        netPnl: 0,
        pairs: [],
      };
      strategyGroupMap.set(key, group);
    }
    group.pairs.push(row);
    group.pairCount = group.pairs.length;
    group.totalTrades += row.totalTrades;
    group.wins += row.wins;
    group.losses += row.losses;
    group.cumulativeR += row.cumulativeR;
    group.netPnl += row.netPnl;
  }
  const strategyGroups = Array.from(strategyGroupMap.values()).map((group) => ({
    ...group,
    winRatePct: group.totalTrades ? Math.round((group.wins / group.totalTrades) * 10000) / 100 : 0,
    cumulativeR: Math.round(group.cumulativeR * 10000) / 10000,
    expectancyR: group.totalTrades ? Math.round((group.cumulativeR / group.totalTrades) * 10000) / 10000 : 0,
  }));

  const handleStrategySort = (field: StrategySummarySortField) => {
    if (strategySortField === field) {
      setStrategySortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setStrategySortField(field);
      setStrategySortDir(field === "strategy" ? "asc" : "desc");
    }
  };
  const handlePairSort = (field: PairDetailSortField) => {
    if (pairSortField === field) {
      setPairSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setPairSortField(field);
      setPairSortDir(field === "symbol" || field === "marketTimeframe" ? "asc" : "desc");
    }
  };

  const sortedStrategyGroups = [...strategyGroups].sort((a, b) =>
    compareSortValues(a[strategySortField], b[strategySortField], strategySortDir)
    || a.strategy.localeCompare(b.strategy),
  );
  const selectedStrategyGroup = selectedStrategyKey
    ? strategyGroups.find((group) => group.key === selectedStrategyKey) ?? null
    : null;
  const sortedSelectedPairs = selectedStrategyGroup
    ? [...selectedStrategyGroup.pairs].sort((a, b) => {
        const valueFor = (row: (typeof selectedStrategyGroup.pairs)[number]): string | number | null | undefined => {
          if (pairSortField === "marketTimeframe") return `${row.market ?? ""} ${row.timeframe ?? ""}`.trim();
          if (pairSortField === "realizedEquity") return configById.get(row.configId ?? 0)?.realized_equity ?? null;
          return row[pairSortField];
        };
        return compareSortValues(valueFor(a), valueFor(b), pairSortDir)
          || (a.symbol ?? "").localeCompare(b.symbol ?? "");
      })
    : [];
  const savePreference = async (field: "report_enabled" | "telegram_enabled", enabled: boolean) => {
    if (!canTrade || !filtered || !scopedConfigs.length || savingPreference || togglingConfigId !== null) return;
    setSavingPreference(true);
    setActionError(null);
    setActionSuccess(null);
    try {
      const updated = await updatePaperReportPreference({
        symbol: reportSymbol || undefined,
        strategy_name: reportStrategy || undefined,
      }, field, enabled);
      setActionSuccess(`${field === "report_enabled" ? "Reporting" : "Telegram trade alerts"} ${enabled ? "enabled" : "disabled"} · ${updated} configurations changed.`);
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Failed to save preference.");
    } finally {
      setSavingPreference(false);
    }
  };

  const handleSyncMarketTick = async () => {
    if (!canTrade || syncing) return;
    setSyncing(true);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      await syncPaperMarketTick();
      setActionSuccess("Paper trading market tick synced and evaluated.");
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Failed to sync paper market tick.");
    } finally {
      setSyncing(false);
    }
  };

  const handleBatchStrategyAction = async (strategyName: string, action: "activate_all" | "pause_all") => {
    if ((action === "activate_all" && !paperReady) || !canTrade || batchingStrategy !== null || togglingConfigId !== null || savingPreference) return;
    setBatchingStrategy(strategyName);
    setActionError(null);
    setActionSuccess(null);
    setActionWarning(null);
    try {
      const res = await batchUpdatePaperStrategy(strategyName, action);
      setActionSuccess(
        `${action === "pause_all" ? "Paused" : "Resumed"} ${res.updated} pair(s) for strategy ${strategyName}.`,
      );
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Batch strategy update failed.");
    } finally {
      setBatchingStrategy(null);
    }
  };

  const strategyBatchGroups = Object.values(
    configs.reduce<Record<string, { strategy: string; total: number; active: number; paused: number }>>((acc, config) => {
      if (config.archived_at) return acc;
      const entry = acc[config.strategy_name] ?? { strategy: config.strategy_name, total: 0, active: 0, paused: 0 };
      entry.total += 1;
      if (config.is_active) entry.active += 1;
      else entry.paused += 1;
      acc[config.strategy_name] = entry;
      return acc;
    }, {}),
  ).sort((a, b) => a.strategy.localeCompare(b.strategy));
  const breaker = data?.breaker;
  const metric = (label: string, value: string, note: string, icon: ReactNode, className = "") => (
    <article className="metric-card" key={label}>
      <div className="metric-top"><span>{label}</span><span className="metric-icon">{icon}</span></div>
      <div className={"metric-value " + className}>{value}</div>
      <div className="metric-foot"><span>{note}</span></div>
    </article>
  );

  return (
    <div className="paper-trading-view" aria-busy={loading}>
      <section className="panel paper-safety-banner">
        <ShieldCheck size={19} />
        <div>
          <strong>{canTrade ? "Simulated Paper Trading Operations" : "Read-only Paper Trading Monitor"}</strong>
          <p>
            {canTrade
              ? "Simulated paper execution engine. Actions here execute against virtual capital only and cannot place live exchange orders."
              : "Review simulated paper trading orders, open positions, realized ledger history, and entry risk circuit breakers. Actions are view-only for this account."}
          </p>
        </div>
        <span className="panel-counter">{canTrade ? "PAPER TRADING" : "READ ONLY"}</span>
      </section>

      {error && (
        <div className="api-error" role="alert">
          <div><strong>Unable to load Paper Trading</strong><p>{error}</p></div>
          <button className="button button-secondary" onClick={() => setAttempt((v) => v + 1)}><RefreshCw size={15} /> Retry</button>
        </div>
      )}

      {actionError && (
        <div className="api-error" role="alert">
          <div><strong>Action Failed</strong><p>{actionError}</p></div>
          <button className="button button-secondary" onClick={() => setActionError(null)}>Dismiss</button>
        </div>
      )}

      {actionSuccess && (
        <p className="control-feedback control-success" role="status">
          <CheckCircle size={15} /> {actionSuccess}
        </p>
      )}

      {actionWarning && (
        <p className="control-feedback control-warning" role="status">
          <AlertTriangle size={15} /> {actionWarning}
        </p>
      )}

      <section className="panel" aria-labelledby="paper-report-filters">
        <h2 id="paper-report-filters">Performance report filters</h2>
        <div className="paper-risk-grid">
          <label className="screener-control-field">Pair
            <select value={reportSymbol} disabled={savingPreference} onChange={(event) => {
              setReportSymbol(event.target.value);
              setReportStrategy("");
            }}>
              <option value="">All pairs</option>
              {symbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
            </select>
          </label>
          <label className="screener-control-field">Strategy
            <select value={reportStrategy} disabled={savingPreference} onChange={(event) => setReportStrategy(event.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((strategy) => <option key={strategy} value={strategy}>{strategy}</option>)}
            </select>
          </label>
          <label className="screener-control-field">Report period
            <select value={reportDays} onChange={(event) => setReportDays(Number(event.target.value))}>
              <option value={0}>All time</option><option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
            </select>
          </label>
          <label className="screener-control-field">Report market
            <select value={reportMarket} onChange={(event) => setReportMarket(event.target.value)}>
              <option value="">All markets</option><option value="spot">Spot</option><option value="futures">Futures</option>
            </select>
          </label>
          <label className="screener-control-field">Report timeframe
            <select value={reportTimeframe} onChange={(event) => setReportTimeframe(event.target.value)}>
              <option value="">All timeframes</option>
              {["15m", "1h", "4h", "1d"].map((timeframe) => <option key={timeframe}>{timeframe}</option>)}
            </select>
          </label>
        </div>
        {reportDays > 0 && !allMetrics && <p role="status">
          {periodResult?.failed ? "Period totals could not be loaded." : "Loading period totals…"}
          {periodResult?.failed && <button type="button" className="button button-secondary" onClick={() => setAttempt((current) => current + 1)}>Retry</button>}
        </p>}
        {preferenceScopeSelected && <p>{scopedConfigs.filter((c) => c.report_enabled).length} of {scopedConfigs.length} configurations included in reports · {scopedConfigs.filter((c) => c.telegram_enabled).length} with Telegram trade alerts</p>}
        {canTrade && preferenceScopeSelected && scopedConfigs.length > 0 && <div className="screener-control-footer">
          {([
            ["report_enabled", true, "Enable reporting"],
            ["report_enabled", false, "Disable reporting"],
            ["telegram_enabled", true, "Enable Telegram trade alerts"],
            ["telegram_enabled", false, "Disable Telegram trade alerts"],
          ] as const).map(([field, enabled, label]) => (
            <button key={label} type="button" className="button button-secondary button-sm"
              disabled={loading || savingPreference || togglingConfigId !== null || scopedConfigs.every((c) => c[field] === enabled)}
              onClick={() => void savePreference(field, enabled)}>{label}</button>
          ))}
        </div>}
        {canTrade && <p>Preferences apply to existing configurations in the selected scope, across markets and timeframes.</p>}
      </section>

      <section className="metrics-grid" aria-label="Paper trading performance">
        {metric("Net realized PnL", m ? money.format(m.netPnl) : loading ? "Loading…" : "—", "Report-enabled strategies", <Activity size={17} />, pnlClass(m?.netPnl))}
        {metric("Win rate", m ? `${number.format(m.winRatePct)}%` : loading ? "Loading…" : "—", m ? `${m.wins} wins · ${m.losses} losses` : "Closed paper trades", <Activity size={17} />)}
        {metric("Expectancy", m ? `${number.format(m.expectancyR)} R` : loading ? "Loading…" : "—", "Average R per closed trade", <Activity size={17} />, pnlClass(m?.expectancyR))}
        {metric("Open positions", data ? String(data.positions.length) : loading ? "Loading…" : "—", `${data?.configs.filter((item) => item.is_active).length ?? "—"} active configs`, <WalletCards size={17} />)}
      </section>

      <section className="panel" aria-labelledby="paper-strategy-performance-title">
        <div className="panel-heading">
          <div><h2 id="paper-strategy-performance-title">Strategy performance</h2><p>Closed-trade aggregates grouped by strategy. Click a strategy row for per-pair summary, or click any column header to sort.</p></div>
          <span className="panel-counter">{strategyGroups.length} STRATEGIES · {m?.byStrategy.length ?? 0} PAIRS</span>
        </div>
        {loading && !data ? <p className="empty-state">Loading strategy performance…</p> : strategyGroups.length ? (
          <>
            <div className="status-table-wrap">
              <table className="status-table" aria-label="Strategy performance summary">
                <thead>
                  <tr>
                    <SortableHeader field="strategy" currentField={strategySortField} direction={strategySortDir} label="Strategy" onSort={handleStrategySort} />
                    <SortableHeader field="pairCount" currentField={strategySortField} direction={strategySortDir} label="Pairs" onSort={handleStrategySort} />
                    <SortableHeader field="totalTrades" currentField={strategySortField} direction={strategySortDir} label="Closed trades" onSort={handleStrategySort} />
                    <SortableHeader field="wins" currentField={strategySortField} direction={strategySortDir} label="Wins" onSort={handleStrategySort} />
                    <SortableHeader field="losses" currentField={strategySortField} direction={strategySortDir} label="Losses" onSort={handleStrategySort} />
                    <SortableHeader field="winRatePct" currentField={strategySortField} direction={strategySortDir} label="Win rate" onSort={handleStrategySort} />
                    <SortableHeader field="cumulativeR" currentField={strategySortField} direction={strategySortDir} label="Cumulative R" onSort={handleStrategySort} />
                    <SortableHeader field="expectancyR" currentField={strategySortField} direction={strategySortDir} label="Expectancy" onSort={handleStrategySort} />
                    <SortableHeader field="netPnl" currentField={strategySortField} direction={strategySortDir} label="Net PnL" onSort={handleStrategySort} />
                  </tr>
                </thead>
                <tbody>
                  {sortedStrategyGroups.map((group) => {
                    const isSelected = selectedStrategyGroup?.key === group.key;
                    return (
                      <tr
                        key={group.key}
                        onClick={() => setSelectedStrategyKey(isSelected ? null : group.key)}
                        style={{ cursor: "pointer", background: isSelected ? "color-mix(in srgb, var(--color-accent) 10%, transparent)" : undefined }}
                      >
                        <td>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedStrategyKey(isSelected ? null : group.key);
                            }}
                            aria-expanded={isSelected}
                            style={{ background: "none", border: 0, padding: 0, color: "inherit", font: "inherit", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "6px" }}
                          >
                            <strong>{group.strategy}</strong>
                            <span style={{ opacity: 0.65, fontSize: "0.75rem" }}>{isSelected ? "▾" : "▸"}</span>
                          </button>
                        </td>
                        <td>{group.pairCount}</td>
                        <td>{number.format(group.totalTrades)}</td>
                        <td>{group.wins}</td>
                        <td>{group.losses}</td>
                        <td>{group.totalTrades ? `${number.format(group.winRatePct)}%` : "—"}</td>
                        <td className={pnlClass(group.cumulativeR)}>{number.format(group.cumulativeR)} R</td>
                        <td className={pnlClass(group.expectancyR)}>{number.format(group.expectancyR)} R</td>
                        <td className={pnlClass(group.netPnl)}>{money.format(group.netPnl)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {selectedStrategyGroup && (
              <div style={{ marginTop: "18px", paddingTop: "16px", borderTop: "1px solid var(--color-border)" }} aria-label={`${selectedStrategyGroup.strategy} per-pair detail`}>
                <div className="panel-heading" style={{ paddingBottom: "10px", marginBottom: "10px" }}>
                  <div>
                    <h3 style={{ margin: 0, fontSize: "13px" }}>{selectedStrategyGroup.strategy} — Per-Pair Summary</h3>
                    <p>{selectedStrategyGroup.pairCount} pair{selectedStrategyGroup.pairCount === 1 ? "" : "s"} · click any column header to sort.</p>
                  </div>
                  <button type="button" className="button button-secondary" onClick={() => setSelectedStrategyKey(null)}>
                    Close
                  </button>
                </div>
                <div className="status-table-wrap">
                  <table className="status-table" aria-label="Strategy performance by pair">
                    <thead>
                      <tr>
                        <SortableHeader field="symbol" currentField={pairSortField} direction={pairSortDir} label="Pair" onSort={handlePairSort} />
                        <SortableHeader field="marketTimeframe" currentField={pairSortField} direction={pairSortDir} label="Market / timeframe" onSort={handlePairSort} />
                        <SortableHeader field="totalTrades" currentField={pairSortField} direction={pairSortDir} label="Closed trades" onSort={handlePairSort} />
                        <SortableHeader field="wins" currentField={pairSortField} direction={pairSortDir} label="Wins" onSort={handlePairSort} />
                        <SortableHeader field="losses" currentField={pairSortField} direction={pairSortDir} label="Losses" onSort={handlePairSort} />
                        <SortableHeader field="winRatePct" currentField={pairSortField} direction={pairSortDir} label="Win rate" onSort={handlePairSort} />
                        <SortableHeader field="cumulativeR" currentField={pairSortField} direction={pairSortDir} label="Cumulative R" onSort={handlePairSort} />
                        <SortableHeader field="expectancyR" currentField={pairSortField} direction={pairSortDir} label="Expectancy" onSort={handlePairSort} />
                        <SortableHeader field="netPnl" currentField={pairSortField} direction={pairSortDir} label="Net PnL" onSort={handlePairSort} />
                        <SortableHeader field="realizedEquity" currentField={pairSortField} direction={pairSortDir} label="Realized equity (all time)" onSort={handlePairSort} />
                      </tr>
                    </thead>
                    <tbody>
                      {sortedSelectedPairs.map((row, index) => {
                        const eq = configById.get(row.configId ?? 0)?.realized_equity ?? null;
                        return (
                          <tr key={`${row.symbol}-${row.strategy}-${row.configId}-${index}`}>
                            <td><strong>{row.symbol ?? "—"}</strong></td>
                            <td>{row.market && row.timeframe ? `${row.market} · ${row.timeframe}` : "—"}</td>
                            <td>{number.format(row.totalTrades)}</td>
                            <td>{row.wins}</td>
                            <td>{row.losses}</td>
                            <td>{row.totalTrades ? `${number.format(row.winRatePct)}%` : "—"}</td>
                            <td className={pnlClass(row.cumulativeR)}>{number.format(row.cumulativeR)} R</td>
                            <td className={pnlClass(row.expectancyR)}>{number.format(row.expectancyR)} R</td>
                            <td className={pnlClass(row.netPnl)}>{money.format(row.netPnl)}</td>
                            <td>{eq == null ? "—" : money.format(eq)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : <p className="empty-state">No strategy performance rows returned.</p>}
      </section>

      {/* PT-10: Risk guard status and circuit breaker controls */}
      <section className="panel" aria-labelledby="paper-risk-title">
        <div className="panel-heading">
          <div><h2 id="paper-risk-title">Risk guard status</h2><p>Real-time entry-safety checks and emergency circuit breaker controls.</p></div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <span className={"status-pill " + (breaker?.can_open_new_position ? "status-positive" : "status-warning")}>
              {breaker?.can_open_new_position ? "Entries allowed" : breaker ? "Entries blocked" : "Status unavailable"}
            </span>
            {canTrade && breaker && (
              breaker.emergency_stop_active ? (
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={(e) => {
                    breakerTriggerRef.current = e.currentTarget;
                    setBreakerAction("reset");
                  }}
                  disabled={breakerBusy}
                >
                  Reset Circuit Breaker
                </button>
              ) : (
                <button
                  type="button"
                  className="button button-danger-subtle"
                  onClick={(e) => {
                    breakerTriggerRef.current = e.currentTarget;
                    setBreakerAction("trip");
                  }}
                  disabled={breakerBusy}
                >
                  Emergency Stop
                </button>
              )
            )}
          </div>
        </div>
        {breaker ? (
          <>
            <div className="paper-risk-grid">
              <div><span>Breaker state</span><strong>{breaker.status}</strong></div>
              <div><span>Emergency stop</span><strong>{breaker.emergency_stop_active ? "Active" : "Inactive"}</strong></div>
              <div><span>Open positions</span><strong>{breaker.active_positions} / {breaker.max_concurrent_positions}</strong></div>
              <div><span>Today realized PnL</span><strong className={pnlClass(breaker.today_realized_pnl_usd)}>{breaker.today_realized_pnl_usd == null ? "Unavailable" : money.format(breaker.today_realized_pnl_usd)}</strong></div>
              <div><span>Open notional</span><strong>{breaker.open_notional_usd == null ? "Unavailable" : money.format(breaker.open_notional_usd)}</strong></div>
              <div><span>Max daily loss</span><strong>{money.format(breaker.max_daily_loss_usd)}</strong></div>
            </div>
            <p className={"paper-breaker-message " + (breaker.can_open_new_position ? "" : "paper-breaker-warning")}>
              {!breaker.can_open_new_position && <AlertTriangle size={15} />}{breaker.message}
            </p>
          </>
        ) : <p className="empty-state">{loading ? "Loading risk guard…" : "Risk guard status unavailable."}</p>}
      </section>

      {/* PT-08: Open positions with manual exit action */}
      <section className="panel" aria-labelledby="paper-positions-title">
        <div className="panel-heading">
          <div><h2 id="paper-positions-title">Open positions</h2><p>Current mark, risk levels, unrealized performance, and simulated exit actions.</p></div>
          <span className="panel-counter">{data?.positions.length ?? "—"} POSITIONS</span>
        </div>
        {loading && !data ? <p className="empty-state" role="status">Loading open positions…</p> : data?.positions.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Symbol / strategy</th><th>Side</th><th>Entry</th><th>Mark</th><th>Stop / target</th><th>Booked funding</th><th>Unrealized R</th><th>Updated</th><th>Action</th></tr></thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.id}>
                    <td><strong>{p.symbol}</strong><small>{p.strategy_name} · {p.market} · {p.timeframe}</small></td>
                    <td>{p.direction}</td>
                    <td>{number.format(p.entry_price)}</td>
                    <td>{number.format(p.current_price)}</td>
                    <td>{p.stop_loss == null ? "—" : number.format(p.stop_loss)} / {p.take_profit == null ? "—" : number.format(p.take_profit)}</td>
                    <td>{p.funding_pnl == null ? "—" : money.format(p.funding_pnl)}</td>
                    <td className={pnlClass(p.unrealized_r)}>{p.unrealized_r == null ? "—" : `${number.format(p.unrealized_r)} R`}</td>
                    <td>{dateLabel(p.last_update_time)}</td>
                    <td>
                      <div style={{ display: "flex", gap: "6px" }}>
                        <button
                          type="button"
                          className="button button-secondary button-xs"
                          aria-label={`View chart for position #${p.id}`}
                          onClick={() => setChartPosition(p)}
                        >
                          Chart
                        </button>
                        {canTrade ? (
                          <button
                            type="button"
                            className="button button-danger-subtle button-xs"
                            aria-label={`Close position #${p.id} for ${p.symbol}`}
                            onClick={(e) => {
                              closePositionTriggerRef.current = e.currentTarget;
                              setSelectedPositionForClose(p);
                            }}
                            disabled={closingPositionId === p.id}
                          >
                            {closingPositionId === p.id ? "Closing…" : "Close"}
                          </button>
                        ) : <span className="text-muted">—</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="empty-state">No open paper positions.</p>}
        {chartPosition && (
          <dialog className="modal-dialog" open style={{ maxWidth: "800px", width: "95%" }}>
            <div className="panel-heading">
              <div>
                <h2>{chartPosition.symbol} Position Chart</h2>
                <p>{chartPosition.strategy_name} · {chartPosition.market} · {chartPosition.timeframe} · Entry: {number.format(chartPosition.entry_price)}</p>
              </div>
              <button type="button" className="icon-button" onClick={() => setChartPosition(null)} aria-label="Close chart">
                <X size={16} />
              </button>
            </div>
            <div style={{ padding: "16px 0" }}>
              <TradingChart
                symbol={chartPosition.symbol}
                market={chartPosition.market as any}
                timeframe={chartPosition.timeframe}
                entryPrice={chartPosition.entry_price}
                stopLoss={chartPosition.stop_loss}
                takeProfit={chartPosition.take_profit}
                markPrice={chartPosition.current_price}
                fundingRate={chartPosition.funding_pnl}
              />
            </div>
          </dialog>
        )}
      </section>

      {/* PT-09: Strategy configurations with deploy strategy action */}
      <section className="panel" aria-labelledby="paper-config-title">
        <div className="panel-heading">
          <div><h2 id="paper-config-title">Strategy configurations</h2><p>Existing activation, capital, Telegram, and reporting preferences.</p></div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
            <span className="panel-counter">{data?.configs.length ?? "—"} CONFIGS</span>
            {canTrade && (
              <>
                <button
                  type="button"
                  className="button button-secondary button-sm"
                  onClick={() => void handleSyncMarketTick()}
                  disabled={syncing || loading}
                >
                  <RefreshCw size={14} /> {syncing ? "Syncing…" : "Sync now"}
                </button>
                <button
                  type="button"
                  className="button button-primary button-sm"
                  disabled={!paperReady}
                  onClick={(e) => {
                    if (onDeployPreparedStrategy) {
                      onDeployPreparedStrategy();
                      return;
                    }
                    deployTriggerRef.current = e.currentTarget;
                    setShowDeployModal(true);
                  }}
                >
                  <Plus size={14} /> Deploy Strategy
                </button>
              </>
            )}
          </div>
        </div>
        {canTrade && strategyBatchGroups.length > 0 && (
          <div className="paper-strategy-batch-bar" style={{ display: "flex", flexWrap: "wrap", gap: "8px", padding: "10px 0 12px", borderBottom: "1px solid var(--color-border)", marginBottom: "12px" }}>
            {strategyBatchGroups.map((group) => {
              const busy = batchingStrategy === group.strategy;
              return (
                <div
                  key={group.strategy}
                  style={{ display: "inline-flex", alignItems: "center", gap: "8px", padding: "6px 10px", border: "1px solid var(--color-border)", borderRadius: "8px", background: "var(--color-elevated)", fontSize: "12px" }}
                >
                  <strong>{group.strategy}</strong>
                  <span className="text-muted">{group.active} active · {group.paused} paused</span>
                  <button
                    type="button"
                    className="button button-secondary button-xs"
                    aria-label={`Pause all ${group.strategy} pairs`}
                    disabled={busy || batchingStrategy !== null || togglingConfigId !== null || savingPreference || loading || group.active === 0}
                    onClick={() => void handleBatchStrategyAction(group.strategy, "pause_all")}
                  >
                    {busy ? "Updating…" : "Pause all"}
                  </button>
                  <button
                    type="button"
                    className="button button-secondary button-xs"
                    aria-label={`Resume all ${group.strategy} pairs`}
                    disabled={busy || batchingStrategy !== null || togglingConfigId !== null || savingPreference || loading || group.paused === 0 || !paperReady}
                    onClick={() => void handleBatchStrategyAction(group.strategy, "activate_all")}
                  >
                    Resume all
                  </button>
                </div>
              );
            })}
          </div>
        )}
        {data?.configs.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Symbol / strategy</th><th>Market</th><th>State</th><th>Capital</th><th>Realized equity</th><th>Risk / trade</th><th>Telegram</th><th>Report</th>{canTrade && <th>Actions</th>}</tr></thead>
              <tbody>
                {data.configs.map((c) => (
                  <tr key={c.id}>
                    <td><strong>{c.symbol}</strong><small>{c.strategy_name} · {c.timeframe}</small></td>
                    <td>{c.market}</td>
                    <td><span className={"status-pill " + (c.is_active ? "status-positive" : "status-neutral")}>{c.archived_at ? "Archived" : c.is_active ? "Active" : "Paused"}</span></td>
                    <td>{money.format(c.allocated_capital)}</td>
                    <td>{c.realized_equity == null ? "—" : money.format(c.realized_equity)}</td>
                    <td>{number.format(c.risk_per_trade_pct)}%</td>
                    <td>{c.telegram_enabled ? "Enabled" : "Disabled"}</td>
                    <td>{c.report_enabled ? "Included" : "Hidden"}</td>
                    {canTrade && (
                      <td>
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                          <button
                            type="button"
                            className={`button ${c.is_active ? "button-secondary" : "button-primary"} button-sm`}
                            disabled={savingPreference || togglingConfigId !== null || loading || (!c.is_active && !paperReady)}
                            onClick={() => void saveDeploymentSettings(c, { is_active: !c.is_active }, c.is_active ? "Deployment paused." : "Deployment activated.")}
                            title={c.is_active ? "Pause paper trading for this strategy" : "Activate paper trading for this strategy"}
                          >
                            {togglingConfigId === c.id ? "…" : c.archived_at ? "Restore and activate" : c.is_active ? "Pause" : "Activate"}
                          </button>
                          <button
                            type="button"
                            className={`button ${c.telegram_enabled ? "button-primary" : "button-secondary"} button-sm`}
                            disabled={savingPreference || togglingConfigId !== null || loading}
                            onClick={() => void saveDeploymentSettings(c, { telegram_enabled: !c.telegram_enabled }, c.telegram_enabled ? "Telegram alerts disabled." : "Telegram alerts enabled.")}
                            title={c.telegram_enabled ? "Disable Telegram alerts for this strategy" : "Enable Telegram alerts for this strategy"}
                          >
                            Telegram {c.telegram_enabled ? "On" : "Off"}
                          </button>
                          <button
                            type="button"
                            className={`button ${c.report_enabled ? "button-primary" : "button-secondary"} button-sm`}
                            disabled={savingPreference || togglingConfigId !== null || loading}
                            onClick={() => void saveDeploymentSettings(c, { report_enabled: !c.report_enabled }, c.report_enabled ? "Performance report hidden." : "Performance report included.")}
                            title={c.report_enabled ? "Hide from performance reports" : "Include in performance reports"}
                          >
                            Report {c.report_enabled ? "On" : "Off"}
                          </button>
                          {!c.archived_at && <>
                            <button className="button button-secondary button-sm" type="button" disabled={loading || savingPreference || togglingConfigId !== null} onClick={() => { setParameterDraft(null); setEditingConfig(c); }}>Edit</button>
                            <button className="button button-secondary button-sm" type="button" disabled={loading || savingPreference || togglingConfigId !== null} onClick={() => setPendingArchive(c)}>Archive</button>
                          </>}
                          <button className="button button-secondary button-sm" type="button" disabled={c.is_active || loading || savingPreference || togglingConfigId !== null} title={c.is_active ? "Pause this deployment first" : undefined} onClick={() => setPendingPurge(c)}>Delete permanently</button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="empty-state">{loading ? "Loading strategy configurations…" : "No paper strategy configurations found."}</p>}
      </section>

      {editingConfig && <section className="panel" aria-label="Edit paper deployment">
        <h2>Edit {editingConfig.symbol} · {editingConfig.strategy_name}</h2>
        <form onSubmit={(event) => void saveDeployment(event)}>
          <div className="paper-risk-grid">
            <label className="screener-control-field">Allocated capital
              <input type="number" min={100} step="0.01" required value={editingConfig.allocated_capital} onChange={(event) => setEditingConfig({ ...editingConfig, allocated_capital: event.target.valueAsNumber })} />
            </label>
            <label className="screener-control-field">Risk per trade (%)
              <input type="number" min={0.1} max={10} step="0.01" required value={editingConfig.risk_per_trade_pct} onChange={(event) => setEditingConfig({ ...editingConfig, risk_per_trade_pct: event.target.valueAsNumber })} />
            </label>
          </div>
          <DeploymentParameters key={editingConfig.id} strategy={editingConfig.strategy_name}
            value={parameterDraft ?? editingConfig.strategy_params ?? {}}
            onChange={setParameterDraft} disabled={togglingConfigId !== null} />
          <button className="button button-secondary" type="button" disabled={togglingConfigId !== null} onClick={() => setEditingConfig(null)}>Cancel edit</button>
          <button className="button button-primary" type="submit" disabled={togglingConfigId !== null}>Save deployment</button>
        </form>
      </section>}
      {pendingArchive && <section className="panel" aria-label="Confirm archive">
        <h2>Archive {pendingArchive.symbol} · {pendingArchive.strategy_name}?</h2>
        <p>New entries stop and pending orders are cancelled. Open positions continue to be managed and closed trades remain in history.</p>
        <button className="button button-secondary" type="button" disabled={togglingConfigId !== null} onClick={() => setPendingArchive(null)}>Cancel archive</button>
        <button className="button button-primary" type="button" disabled={togglingConfigId !== null} onClick={() => void archiveDeployment(pendingArchive)}>Confirm archive</button>
      </section>}

      {pendingPurge && <section className="panel" aria-label="Confirm permanent delete">
        <h2>Permanently delete {pendingPurge.symbol} · {pendingPurge.strategy_name}?</h2>
        <p>This cannot be undone. Permanent deletion is available for deployments with no position, trade, or order history. Use Archive to preserve a deployment and its history.</p>
        <button className="button button-secondary" type="button" disabled={togglingConfigId !== null} onClick={() => setPendingPurge(null)}>Cancel delete</button>
        <button className="button button-danger" type="button" disabled={togglingConfigId !== null} onClick={() => void purgeDeployment(pendingPurge)}>Confirm permanent delete</button>
      </section>}

      {/* Portfolio Risk & Exposure Desk */}
      <PortfolioRiskDesk
        positions={data?.positions ?? []}
        trades={data?.trades ?? []}
        circuitBreakerTripped={data?.breaker?.emergency_stop_active ?? false}
      />

      {/* PT-07: Recent orders with cancel order action */}
      <section className="panel" aria-labelledby="paper-orders-title">
        <div className="panel-heading">
          <div><h2 id="paper-orders-title">Recent orders</h2><p>Latest durable paper-order ledger entries and pending order cancellation.</p></div>
          <span className="panel-counter">{data?.orders.length ?? "—"} ORDERS</span>
        </div>
        {data?.orders.length ? (
          <ExecutionBlotter
            orders={data.orders}
            canTrade={canTrade}
            onCancelOrder={(o) => {
              setSelectedOrderForCancel(o);
            }}
            cancellingOrderId={cancellingOrderId}
          />
        ) : <p className="empty-state">{loading ? "Loading recent orders…" : "No recent orders."}</p>}
      </section>

      <section className="panel" aria-labelledby="paper-trades-title">
        <div className="panel-heading">
          <div><h2 id="paper-trades-title">Recent closed trades</h2><p>Ledger values use the backend’s canonical net PnL and R-multiple calculations.</p></div>
          <span className="panel-counter">{data?.trades.length ?? "—"} TRADES</span>
        </div>
        {data?.trades.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Symbol / strategy</th><th>Direction</th><th>Entry → exit</th><th>Funding PnL</th><th>Net PnL</th><th>Return</th><th>R multiple</th><th>Exit reason</th></tr></thead>
              <tbody>
                {data.trades.map((t) => (
                  <tr key={t.id}>
                    <td><strong>{t.symbol}</strong><small>{t.strategy_name} · {t.market} · {t.timeframe}</small></td>
                    <td>{t.direction}</td>
                    <td>{number.format(t.entry_price)} → {number.format(t.exit_price)}</td>
                    <td>{t.funding_pnl == null ? "—" : money.format(t.funding_pnl)}</td>
                    <td className={pnlClass(t.net_pnl)}>{money.format(t.net_pnl)}</td>
                    <td className={pnlClass(t.net_return_pct)}>{number.format(t.net_return_pct)}%</td>
                    <td className={pnlClass(t.r_multiple)}>{number.format(t.r_multiple)} R</td>
                    <td>{t.exit_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="empty-state">{loading ? "Loading closed trades…" : "No closed paper trades."}</p>}
      </section>

      <div className="paper-refresh-row">
        <span>Refreshes on demand; simulation operations update on execution.</span>
        <button className="button button-secondary" onClick={() => setAttempt((v) => v + 1)} disabled={loading}><RefreshCw size={15} /> Refresh snapshot</button>
      </div>

      {/* PT-07 Modal: Cancel Order Confirmation */}
      {selectedOrderForCancel && (
        <dialog
          ref={cancelOrderDialogRef}
          className="app-dialog"
          aria-labelledby="cancel-order-dialog-title"
          aria-describedby="cancel-order-dialog-desc"
          onCancel={(e) => { e.preventDefault(); closeCancelOrderModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeCancelOrderModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="cancel-order-dialog-title">Cancel Pending Paper Order #{selectedOrderForCancel.id}</h3>
              <p id="cancel-order-dialog-desc">Confirm cancellation of this pending simulated order before execution occurs.</p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeCancelOrderModal} className="icon-button"><X size={16} /></button>
          </div>
          <div className="paper-risk-grid" style={{ margin: "14px 0" }}>
            <div><span>Symbol</span><strong>{selectedOrderForCancel.symbol}</strong></div>
            <div><span>Direction</span><strong>{selectedOrderForCancel.direction}</strong></div>
            <div><span>Market / Timeframe</span><strong>{selectedOrderForCancel.market.toUpperCase()} / {selectedOrderForCancel.timeframe}</strong></div>
            <div><span>Strategy</span><strong>{selectedOrderForCancel.strategy_name}</strong></div>
            <div><span>Requested Entry</span><strong>{selectedOrderForCancel.requested_entry_price != null ? `$${number.format(selectedOrderForCancel.requested_entry_price)}` : "Market"}</strong></div>
            <div><span>Order Status</span><strong>{selectedOrderForCancel.status}</strong></div>
          </div>
          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "16px" }}>
            <button type="button" className="button button-secondary" onClick={closeCancelOrderModal} disabled={cancellingOrderId !== null}>Keep Order</button>
            <button
              ref={cancelOrderConfirmButtonRef}
              type="button"
              className="button button-danger"
              onClick={handleExecuteCancelOrder}
              disabled={cancellingOrderId !== null}
            >
              {cancellingOrderId !== null ? "Cancelling…" : "Confirm Cancel Order"}
            </button>
          </div>
        </dialog>
      )}

      {/* PT-08 Modal: Close Position Confirmation */}
      {selectedPositionForClose && (
        <dialog
          ref={closePositionDialogRef}
          className="app-dialog"
          aria-labelledby="close-position-dialog-title"
          aria-describedby="close-position-dialog-desc"
          onCancel={(e) => { e.preventDefault(); closeClosePositionModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeClosePositionModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="close-position-dialog-title">Manually Exit Paper Position #{selectedPositionForClose.id}</h3>
              <p id="close-position-dialog-desc">This triggers a simulated market exit for the selected open paper position.</p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeClosePositionModal} className="icon-button"><X size={16} /></button>
          </div>
          <div className="paper-risk-grid" style={{ margin: "14px 0" }}>
            <div><span>Symbol</span><strong>{selectedPositionForClose.symbol}</strong></div>
            <div><span>Side</span><strong>{selectedPositionForClose.direction}</strong></div>
            <div><span>Market / TF</span><strong>{selectedPositionForClose.market.toUpperCase()} / {selectedPositionForClose.timeframe}</strong></div>
            <div><span>Strategy</span><strong>{selectedPositionForClose.strategy_name}</strong></div>
            <div><span>Entry Price</span><strong>${number.format(selectedPositionForClose.entry_price)}</strong></div>
            <div><span>Current Mark</span><strong>${number.format(selectedPositionForClose.current_price)}</strong></div>
            <div><span>Unrealized R</span><strong className={pnlClass(selectedPositionForClose.unrealized_r)}>{selectedPositionForClose.unrealized_r != null ? `${number.format(selectedPositionForClose.unrealized_r)} R` : "—"}</strong></div>
            <div><span>Allocated Capital</span><strong>${number.format(selectedPositionForClose.allocated_capital)}</strong></div>
          </div>
          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "16px" }}>
            <button type="button" className="button button-secondary" onClick={closeClosePositionModal} disabled={closingPositionId !== null}>Cancel</button>
            <button
              ref={closePositionConfirmButtonRef}
              type="button"
              className="button button-danger"
              onClick={handleExecuteClosePosition}
              disabled={closingPositionId !== null}
            >
              {closingPositionId !== null ? "Exiting…" : "Exit Position at Market"}
            </button>
          </div>
        </dialog>
      )}

      {/* PT-09 Modal: Deploy Strategy to Paper Trading */}
      {showDeployModal && (
        <dialog
          ref={deployDialogRef}
          className="app-dialog"
          aria-labelledby="deploy-strategy-dialog-title"
          aria-describedby="deploy-strategy-dialog-desc"
          onCancel={(e) => { e.preventDefault(); closeDeployModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeDeployModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="deploy-strategy-dialog-title">Deploy Strategy to Paper Trading</h3>
              <p id="deploy-strategy-dialog-desc">Configure and activate a simulated execution bot with dedicated virtual capital.</p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeDeployModal} className="icon-button"><X size={16} /></button>
          </div>

          <form onSubmit={handleExecuteDeploy} style={{ marginTop: "14px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <div className="screener-control-field">
                <label htmlFor="paper-deploy-symbol">Symbol</label>
                <input
                  ref={deploySymbolInputRef}
                  id="paper-deploy-symbol"
                  type="text"
                  role="combobox"
                  aria-expanded="false"
                  aria-autocomplete="list"
                  list="paper-deploy-symbols-list"
                  required
                  placeholder="e.g. BTCUSDT"
                  value={deploySymbol}
                  disabled={deployBusy}
                  onChange={(e) => setDeploySymbol(e.target.value.toUpperCase())}
                />
                <datalist id="paper-deploy-symbols-list">
                  {availableSymbols.map((sym) => (
                    <option key={sym} value={sym} />
                  ))}
                </datalist>
              </div>
              <div className="screener-control-field">
                <label htmlFor="paper-deploy-market">Market</label>
                <select
                  id="paper-deploy-market"
                  value={deployMarket}
                  disabled={deployBusy}
                  onChange={(e) => setDeployMarket(e.target.value as "spot" | "futures")}
                >
                  <option value="spot">Spot</option>
                  <option value="futures">Futures</option>
                </select>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <div className="screener-control-field">
                <label htmlFor="paper-deploy-timeframe">Timeframe</label>
                <select
                  id="paper-deploy-timeframe"
                  value={deployTimeframe}
                  disabled={deployBusy}
                  onChange={(e) => setDeployTimeframe(e.target.value as "15m" | "1h" | "4h" | "1d")}
                >
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                  <option value="1d">1d</option>
                </select>
              </div>
              <div className="screener-control-field">
                <label htmlFor="paper-deploy-strategy">Strategy</label>
                <select
                  id="paper-deploy-strategy"
                  value={deployStrategy}
                  disabled={deployBusy}
                  onChange={(e) => setDeployStrategy(e.target.value)}
                >
                  {!catalog.length && <option value="">Select a registered strategy</option>}
                  {catalog.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                {catalogError && <p role="alert">Cannot load the strategy catalog. Reopen the form to retry.</p>}
              </div>
            </div>

            {deployStrategy === "ml_signal_generator_v1" && (
              <div className="screener-control-field" style={{ marginBottom: "12px" }}>
                <label htmlFor="paper-deploy-generator-model">Generator Model Artifact</label>
                <select
                  id="paper-deploy-generator-model"
                  value={selectedGeneratorModelId}
                  disabled={deployBusy}
                  onChange={(e) => setSelectedGeneratorModelId(e.target.value)}
                >
                  <option value="">Select an eligible generator model</option>
                  {matchingGeneratorModels.map((m) => (
                    <option key={m.model_id} value={m.model_id}>
                      {m.model_id} ({m.model_type})
                    </option>
                  ))}
                </select>
                {matchingGeneratorModels.length === 0 && (
                  <p role="alert" style={{ fontSize: "0.8rem", color: "#e53e3e", marginTop: "4px" }}>
                    No eligible generator models available for {deploySymbol || "symbol"} {deployMarket} {deployTimeframe}.
                  </p>
                )}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <div className="screener-control-field">
                <label htmlFor="paper-deploy-capital">Allocated Virtual Capital ($)</label>
                <input
                  id="paper-deploy-capital"
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
                <label htmlFor="paper-deploy-risk">Risk Per Trade (%)</label>
                <input
                  id="paper-deploy-risk"
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

            <div style={{ marginBottom: "16px" }}>
              <label style={{ display: "flex", gap: "8px", alignItems: "center", cursor: "pointer", fontSize: "0.85rem" }}>
                <input
                  type="checkbox"
                  checked={deployTelegram}
                  disabled={deployBusy}
                  onChange={(e) => setDeployTelegram(e.target.checked)}
                />
                <span>Broadcast signal alerts to Telegram (optional)</span>
              </label>
            </div>

            {deployError && (
              <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
                <div><p>{deployError}</p></div>
              </div>
            )}

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button type="button" className="button button-secondary" onClick={closeDeployModal} disabled={deployBusy}>Cancel</button>
              <button
                type="submit"
                className="button button-primary"
                disabled={
                  deployBusy ||
                  !catalog.includes(deployStrategy) ||
                  (deployStrategy === "ml_signal_generator_v1" && !selectedGeneratorModelId)
                }
              >
                {deployBusy ? "Deploying…" : "Deploy to Paper Trading"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {/* PT-10 Modal: Circuit Breaker Trip / Reset Confirmation */}
      {breakerAction && (
        <dialog
          ref={breakerDialogRef}
          className="app-dialog"
          aria-labelledby="breaker-dialog-title"
          aria-describedby="breaker-dialog-desc"
          onCancel={(e) => { e.preventDefault(); closeBreakerModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeBreakerModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="breaker-dialog-title">
                {breakerAction === "trip" ? "Confirm Emergency Stop" : "Confirm Circuit Breaker Reset"}
              </h3>
              <p id="breaker-dialog-desc">
                {breakerAction === "trip"
                  ? "Are you sure you want to trigger the EMERGENCY STOP circuit breaker? This will immediately halt all new paper position openings across all strategies."
                  : "Are you sure you want to reset the circuit breaker? This will re-enable new simulated trade entries if risk limits permit."}
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeBreakerModal} className="icon-button"><X size={16} /></button>
          </div>
          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end", marginTop: "16px" }}>
            <button type="button" className="button button-secondary" onClick={closeBreakerModal} disabled={breakerBusy}>Cancel</button>
            <button
              ref={breakerConfirmButtonRef}
              type="button"
              className={breakerAction === "trip" ? "button button-danger" : "button button-primary"}
              onClick={handleExecuteBreakerAction}
              disabled={breakerBusy}
            >
              {breakerBusy ? "Processing…" : breakerAction === "trip" ? "Trigger Emergency Stop" : "Reset Breaker"}
            </button>
          </div>
        </dialog>
      )}
    </div>
  );
}
