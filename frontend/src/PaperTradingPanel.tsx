import { useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, CheckCircle, Plus, RefreshCw, ShieldCheck, WalletCards, X } from "lucide-react";
import { useAuth } from "./AuthGate";
import { fetchBacktestStrategies } from "./lib/backtestStudio";
import { fetchBackfilledSymbols } from "./lib/masterData";
import { ApiError } from "./lib/api";
import { trapFocus } from "./lib/dialog";
import {
  cancelPaperOrder,
  closePaperPosition,
  deployPaperStrategy,
  fetchPaperTradingSnapshot,
  PaperTradingActionError,
  parseTelegramDeliveryStatus,
  resetPaperCircuitBreaker,
  tripPaperCircuitBreaker,
  updatePaperConfig,
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

export function PaperTradingPanel() {
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
  const [togglingConfigId, setTogglingConfigId] = useState<number | null>(null);

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
    if (!showDeployModal) return;
    const controller = new AbortController();
    setCatalog([]);
    setCatalogError(false);
    fetchBacktestStrategies(controller.signal).then(({ strategies }) => {
      if (controller.signal.aborted) return;
      const ids = strategies.map((strategy) => strategy.id).filter((id) => id !== "ml_signal_generator_v1");
      setCatalog(ids);
      setDeployStrategy((selected) => ids.includes(selected) ? selected : ids[0] ?? "");
    }).catch(() => {
      if (!controller.signal.aborted) setCatalogError(true);
    });
    fetchBackfilledSymbols(controller.signal).then((symbols) => {
      if (controller.signal.aborted) return;
      setAvailableSymbols(symbols);
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
    if (deployBusy || !catalog.includes(deployStrategy)) return;
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

  const handleToggleConfigActive = async (c: PaperConfig) => {
    if (!canTrade || togglingConfigId !== null) return;
    setTogglingConfigId(c.id);
    setActionError(null);
    setActionWarning(null);
    try {
      await updatePaperConfig({
        symbol: c.symbol,
        market: c.market,
        timeframe: c.timeframe,
        strategy_name: c.strategy_name,
        is_active: !c.is_active,
        allocated_capital: c.allocated_capital,
        risk_per_trade_pct: c.risk_per_trade_pct,
        ml_model_id: c.ml_model_id,
        ml_generator_model_id: c.ml_generator_model_id,
        telegram_enabled: c.telegram_enabled,
        report_enabled: c.report_enabled,
      });
      setActionSuccess(`Paper trading for ${c.symbol} ${c.strategy_name} ${!c.is_active ? "activated" : "paused"}.`);
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Failed to update strategy configuration.");
    } finally {
      setTogglingConfigId(null);
    }
  };

  const handleToggleConfigTelegram = async (c: PaperConfig) => {
    if (!canTrade || togglingConfigId !== null) return;
    setTogglingConfigId(c.id);
    setActionError(null);
    setActionWarning(null);
    try {
      await updatePaperConfig({
        symbol: c.symbol,
        market: c.market,
        timeframe: c.timeframe,
        strategy_name: c.strategy_name,
        is_active: c.is_active,
        allocated_capital: c.allocated_capital,
        risk_per_trade_pct: c.risk_per_trade_pct,
        ml_model_id: c.ml_model_id,
        ml_generator_model_id: c.ml_generator_model_id,
        telegram_enabled: !c.telegram_enabled,
        report_enabled: c.report_enabled,
      });
      setActionSuccess(`Telegram alerts ${!c.telegram_enabled ? "enabled" : "disabled"} for ${c.strategy_name} (${c.symbol}).`);
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Failed to update Telegram preference.");
    } finally {
      setTogglingConfigId(null);
    }
  };

  const handleToggleConfigReport = async (c: PaperConfig) => {
    if (!canTrade || togglingConfigId !== null) return;
    setTogglingConfigId(c.id);
    setActionError(null);
    setActionWarning(null);
    try {
      await updatePaperConfig({
        symbol: c.symbol,
        market: c.market,
        timeframe: c.timeframe,
        strategy_name: c.strategy_name,
        is_active: c.is_active,
        allocated_capital: c.allocated_capital,
        risk_per_trade_pct: c.risk_per_trade_pct,
        ml_model_id: c.ml_model_id,
        ml_generator_model_id: c.ml_generator_model_id,
        telegram_enabled: c.telegram_enabled,
        report_enabled: !c.report_enabled,
      });
      setActionSuccess(`Performance report ${!c.report_enabled ? "included" : "hidden"} for ${c.strategy_name} (${c.symbol}).`);
      setAttempt((v) => v + 1);
    } catch (cause) {
      setActionError(cause instanceof PaperTradingActionError ? cause.message : "Failed to update report visibility.");
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

  const m = data?.metrics;
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

      <section className="metrics-grid" aria-label="Paper trading performance">
        {metric("Net realized PnL", m ? money.format(m.netPnl) : loading ? "Loading…" : "—", "Report-enabled strategies", <Activity size={17} />, pnlClass(m?.netPnl))}
        {metric("Win rate", m ? `${number.format(m.winRatePct)}%` : loading ? "Loading…" : "—", m ? `${m.wins} wins · ${m.losses} losses` : "Closed paper trades", <Activity size={17} />)}
        {metric("Expectancy", m ? `${number.format(m.expectancyR)} R` : loading ? "Loading…" : "—", "Average R per closed trade", <Activity size={17} />, pnlClass(m?.expectancyR))}
        {metric("Open positions", data ? String(data.positions.length) : loading ? "Loading…" : "—", `${data?.configs.filter((item) => item.is_active).length ?? "—"} active configs`, <WalletCards size={17} />)}
      </section>

      <section className="panel" aria-labelledby="paper-strategy-performance-title">
        <div className="panel-heading">
          <div><h2 id="paper-strategy-performance-title">Strategy performance</h2><p>Closed-trade aggregates from the paper ledger, filtered by report visibility.</p></div>
          <span className="panel-counter">{m?.byStrategy.length ?? 0} STRATEGIES</span>
        </div>
        {loading && !data ? <p className="empty-state">Loading strategy performance…</p> : m?.byStrategy.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Strategy</th><th>Closed trades</th><th>Wins / losses</th><th>Win rate</th><th>Cumulative R</th><th>Expectancy</th><th>Net PnL</th></tr></thead>
              <tbody>
                {m.byStrategy.map((row) => (
                  <tr key={row.strategy}>
                    <td><strong>{row.strategy}</strong></td>
                    <td>{number.format(row.totalTrades)}</td>
                    <td>{row.wins} / {row.losses}</td>
                    <td>{number.format(row.winRatePct)}%</td>
                    <td className={pnlClass(row.cumulativeR)}>{number.format(row.cumulativeR)} R</td>
                    <td className={pnlClass(row.expectancyR)}>{number.format(row.expectancyR)} R</td>
                    <td className={pnlClass(row.netPnl)}>{money.format(row.netPnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="empty-state">No open paper positions.</p>}
      </section>

      {/* PT-09: Strategy configurations with deploy strategy action */}
      <section className="panel" aria-labelledby="paper-config-title">
        <div className="panel-heading">
          <div><h2 id="paper-config-title">Strategy configurations</h2><p>Existing activation, capital, Telegram, and reporting preferences.</p></div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <span className="panel-counter">{data?.configs.length ?? "—"} CONFIGS</span>
            {canTrade && (
              <button
                type="button"
                className="button button-primary button-sm"
                onClick={(e) => {
                  deployTriggerRef.current = e.currentTarget;
                  setShowDeployModal(true);
                }}
              >
                <Plus size={14} /> Deploy Strategy
              </button>
            )}
          </div>
        </div>
        {data?.configs.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Symbol / strategy</th><th>Market</th><th>State</th><th>Capital</th><th>Risk / trade</th><th>Telegram</th><th>Report</th>{canTrade && <th>Actions</th>}</tr></thead>
              <tbody>
                {data.configs.map((c) => (
                  <tr key={c.id}>
                    <td><strong>{c.symbol}</strong><small>{c.strategy_name} · {c.timeframe}</small></td>
                    <td>{c.market}</td>
                    <td><span className={"status-pill " + (c.is_active ? "status-positive" : "status-neutral")}>{c.is_active ? "Active" : "Inactive"}</span></td>
                    <td>{money.format(c.allocated_capital)}</td>
                    <td>{number.format(c.risk_per_trade_pct)}%</td>
                    <td>{c.telegram_enabled ? "Enabled" : "Disabled"}</td>
                    <td>{c.report_enabled ? "Included" : "Hidden"}</td>
                    {canTrade && (
                      <td>
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                          <button
                            type="button"
                            className={`button ${c.is_active ? "button-secondary" : "button-primary"} button-sm`}
                            disabled={togglingConfigId === c.id}
                            onClick={() => handleToggleConfigActive(c)}
                            title={c.is_active ? "Pause paper trading for this strategy" : "Activate paper trading for this strategy"}
                          >
                            {togglingConfigId === c.id ? "…" : c.is_active ? "Pause" : "Activate"}
                          </button>
                          <button
                            type="button"
                            className={`button ${c.telegram_enabled ? "button-primary" : "button-secondary"} button-sm`}
                            disabled={togglingConfigId === c.id}
                            onClick={() => handleToggleConfigTelegram(c)}
                            title={c.telegram_enabled ? "Disable Telegram alerts for this strategy" : "Enable Telegram alerts for this strategy"}
                          >
                            Telegram {c.telegram_enabled ? "On" : "Off"}
                          </button>
                          <button
                            type="button"
                            className={`button ${c.report_enabled ? "button-primary" : "button-secondary"} button-sm`}
                            disabled={togglingConfigId === c.id}
                            onClick={() => handleToggleConfigReport(c)}
                            title={c.report_enabled ? "Hide from performance reports" : "Include in performance reports"}
                          >
                            Report {c.report_enabled ? "On" : "Off"}
                          </button>
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

      {/* PT-07: Recent orders with cancel order action */}
      <section className="panel" aria-labelledby="paper-orders-title">
        <div className="panel-heading">
          <div><h2 id="paper-orders-title">Recent orders</h2><p>Latest durable paper-order ledger entries and pending order cancellation.</p></div>
          <span className="panel-counter">{data?.orders.length ?? "—"} ORDERS</span>
        </div>
        {data?.orders.length ? (
          <div className="status-table-wrap">
            <table className="status-table">
              <thead><tr><th>Symbol</th><th>Direction</th><th>Status</th><th>Fill / requested</th><th>Created</th><th>Reason</th><th>Action</th></tr></thead>
              <tbody>
                {data.orders.map((o) => (
                  <tr key={o.id}>
                    <td><strong>{o.symbol}</strong><small>{o.market} · {o.timeframe}</small></td>
                    <td>{o.direction}</td>
                    <td><span className={"status-pill " + (o.status === "FILLED" ? "status-positive" : o.status === "PENDING" ? "status-warning" : "status-neutral")}>{o.status}</span></td>
                    <td>{o.fill_price != null ? number.format(o.fill_price) : o.requested_entry_price == null ? "Unset" : number.format(o.requested_entry_price)}</td>
                    <td>{dateLabel(o.created_at)}</td>
                    <td>{o.state_reason || "—"}</td>
                    <td>
                      {canTrade && o.status === "PENDING" ? (
                        <button
                          type="button"
                          className="button button-danger-subtle button-xs"
                          aria-label={`Cancel pending order #${o.id} for ${o.symbol}`}
                          onClick={(e) => {
                            cancelOrderTriggerRef.current = e.currentTarget;
                            setSelectedOrderForCancel(o);
                          }}
                          disabled={cancellingOrderId === o.id}
                        >
                          {cancellingOrderId === o.id ? "Cancelling…" : "Cancel"}
                        </button>
                      ) : <span className="text-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
              <button type="submit" className="button button-primary" disabled={deployBusy || !catalog.includes(deployStrategy)}>
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
