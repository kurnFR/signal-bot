import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, BrainCircuit, CheckCircle, Plus, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useAuth } from "./AuthGate";
import { ApiError } from "./lib/api";
import { trapFocus } from "./lib/dialog";
import { MLGeneratorTrainingPanel } from "./MLGeneratorTrainingPanel";
import {
  deployMLModelToPaper,
  fetchMLExperimentDetails,
  fetchMLExperiments,
  fetchMLModels,
  MLStudioActionError,
  runMLExperiment,
  type MLExperimentDetails,
  type MLExperimentSummary,
  type MLModelManifest,
} from "./lib/mlStudio";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
const percentFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
function num(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "—"; }
function percent(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${percentFormat.format(value)}%` : "—"; }
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view ML research data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view ML research data.";
  return "ML research data could not be loaded. Check API and artifact-directory health, then retry.";
}

const SUPPORTED_BASE_STRATEGIES = [
  "trend_ema_v1",
  "rsi_momentum_v1",
  "bollinger_trend_v1",
  "macd_cross_v1",
  "confluence_ensemble_v1",
  "vwap_reversion_v1",
  "atr_breakout_v1",
] as const;

export function MLStudioPanel() {
  const auth = useAuth();
  const canTrade = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());

  const [experiments, setExperiments] = useState<MLExperimentSummary[]>([]);
  const [models, setModels] = useState<MLModelManifest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState<MLExperimentDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");

  // Feedback notifications
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // ML-03: Launch tournament modal state
  const [tournamentOpen, setTournamentOpen] = useState(false);
  const [tournamentBusy, setTournamentBusy] = useState(false);
  const [tournamentError, setTournamentError] = useState<string | null>(null);
  const [tSymbol, setTSymbol] = useState("BTCUSDT");
  const [tMarket, setTMarket] = useState<"spot" | "futures">("spot");
  const [tTimeframe, setTTimeframe] = useState<"15m" | "1h" | "4h" | "1d">("1h");
  const [tStrategy, setTStrategy] = useState<string>("trend_ema_v1");
  const [tModelType, setTModelType] = useState("all");
  const [tTargetType, setTTargetType] = useState("binary_positive_r");
  const [tMinTrades, setTMinTrades] = useState(5);
  const tournamentDialogRef = useRef<HTMLDialogElement | null>(null);
  const tournamentTriggerRef = useRef<HTMLButtonElement | null>(null);

  // ML-04: Deploy to paper modal state
  const [deployModel, setDeployModel] = useState<MLModelManifest | null>(null);
  const [deployBusy, setDeployBusy] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deployCapital, setDeployCapital] = useState(10000);
  const [deployRisk, setDeployRisk] = useState(1.0);
  const [deployTelegram, setDeployTelegram] = useState(false);
  const deployDialogRef = useRef<HTMLDialogElement | null>(null);
  const deployTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([fetchMLExperiments(controller.signal), fetchMLModels(controller.signal)])
      .then(([experimentResult, modelResult]) => {
        if (controller.signal.aborted) return;
        const nextExperiments = Array.isArray(experimentResult.experiments) ? experimentResult.experiments : [];
        setExperiments(nextExperiments);
        setModels(Array.isArray(modelResult.models) ? modelResult.models : []);
        setSelectedId((current) => current && nextExperiments.some((item) => item.experiment_id === current) ? current : nextExperiments[0]?.experiment_id ?? null);
      }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    if (!selectedId) { setDetails(null); return; }
    const controller = new AbortController();
    setDetailLoading(true); setDetailError(null);
    fetchMLExperimentDetails(selectedId, controller.signal).then((result) => {
      if (!controller.signal.aborted) setDetails(result);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDetailError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, attempt]);

  // Modal lifecycle: tournament
  useEffect(() => {
    const dialog = tournamentDialogRef.current;
    if (tournamentOpen && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [tournamentOpen]);

  // Modal lifecycle: deploy
  useEffect(() => {
    const dialog = deployDialogRef.current;
    if (deployModel && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [deployModel]);

  const openTournamentModal = () => {
    setTournamentError(null);
    setTournamentOpen(true);
  };

  const closeTournamentModal = () => {
    const dialog = tournamentDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }
    setTournamentOpen(false);
    setTournamentError(null);
    tournamentTriggerRef.current?.focus();
  };

  const openDeployModal = (model: MLModelManifest) => {
    setDeployModel(model);
    setDeployError(null);
    setDeployCapital(10000);
    setDeployRisk(1.0);
    setDeployTelegram(false);
  };

  const closeDeployModal = () => {
    const dialog = deployDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }
    setDeployModel(null);
    setDeployError(null);
    deployTriggerRef.current?.focus();
  };

  // ML-03: Execute tournament handler
  const handleExecuteTournament = async (e: React.FormEvent) => {
    e.preventDefault();
    if (tournamentBusy) return;
    setTournamentError(null);
    setActionError(null);
    setActionSuccess(null);

    const cleanSymbol = tSymbol.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    if (!cleanSymbol) {
      setTournamentError("Symbol is required (alphanumeric only).");
      return;
    }
    if (tMinTrades < 1 || tMinTrades > 100) {
      setTournamentError("Minimum validation trades must be between 1 and 100.");
      return;
    }

    setTournamentBusy(true);
    try {
      const res = await runMLExperiment({
        symbol: cleanSymbol,
        market: tMarket,
        timeframe: tTimeframe,
        strategy_name: tStrategy,
        model_type: tModelType,
        target_type: tTargetType,
        min_validation_trades: tMinTrades,
      });
      const winner = (res.result?.winner as Record<string, unknown> | undefined)?.model_type;
      const winnerText = typeof winner === "string" ? ` Winner: ${winner}.` : "";
      setActionSuccess(`Tournament completed successfully (ID: ${res.experiment_id}).${winnerText}`);
      closeTournamentModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setTournamentError(cause instanceof MLStudioActionError ? cause.message : "Tournament experiment failed.");
    } finally {
      setTournamentBusy(false);
    }
  };

  // ML-04: Execute deploy handler
  const handleExecuteDeploy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!deployModel || deployBusy) return;
    setDeployError(null);
    setActionError(null);
    setActionSuccess(null);

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
      const res = await deployMLModelToPaper({
        model_id: deployModel.model_id,
        allocated_capital: deployCapital,
        risk_per_trade_pct: deployRisk,
        send_telegram: deployTelegram,
      });
      setActionSuccess(res.message || `ML model '${deployModel.model_id}' deployed to Live Paper Trading.`);
      closeDeployModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setDeployError(cause instanceof MLStudioActionError ? cause.message : "Failed to deploy ML model to Paper Trading.");
    } finally {
      setDeployBusy(false);
    }
  };

  const filteredExperiments = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return experiments.filter((item) => !needle || [item.experiment_id, item.symbol, item.market, item.timeframe, item.strategy, item.winner_model].some((val) => String(val ?? "").toLowerCase().includes(needle)));
  }, [experiments, filter]);
  const eligibleModels = models.filter((model) => model.eligibility?.eligible === true && model.eligible_for_paper === true && model.has_weights === true).length;

  return (
    <div className="ml-studio-view" aria-busy={loading || detailLoading}>
      <section className="panel paper-safety-banner">
        <ShieldCheck size={19} />
        <div>
          <strong>ML Research Studio</strong>
          <p>Train independent signal generators, run model tournaments, and inspect saved ML results. Eligible models can be deployed to Live Paper Trading. Paper activation and Telegram alerts remain separate, explicit controls in Paper Trading.</p>
        </div>
        <span className="panel-counter">RESEARCH</span>
      </section>

      {actionSuccess && (
        <div className="api-success" role="status" aria-live="polite" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <CheckCircle size={17} />
            <span>{actionSuccess}</span>
          </div>
          <button type="button" className="icon-button" aria-label="Dismiss message" onClick={() => setActionSuccess(null)}>
            <X size={15} />
          </button>
        </div>
      )}

      {actionError && (
        <div className="api-error" role="alert" aria-live="assertive" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <AlertTriangle size={17} />
            <span>{actionError}</span>
          </div>
          <button type="button" className="icon-button" aria-label="Dismiss error" onClick={() => setActionError(null)}>
            <X size={15} />
          </button>
        </div>
      )}

      <MLGeneratorTrainingPanel onCompleted={() => setAttempt((n) => n + 1)} />

      {error && (
        <div className="api-error" role="alert">
          <div>
            <strong>Unable to load ML Studio</strong>
            <p>{error}</p>
          </div>
          <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      )}

      <section className="metrics-grid" aria-label="ML research summary">
        <article className="metric-card"><div className="metric-top"><span>Saved experiments</span><span className="metric-icon"><BrainCircuit size={17} /></span></div><div className="metric-value">{loading && !experiments.length ? "Loading…" : experiments.length}</div><div className="metric-foot"><span>Persisted result manifests</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Model artifacts</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !models.length ? "Loading…" : models.length}</div><div className="metric-foot"><span>Model manifests found</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Passing eligibility checks</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{eligibleModels}</div><div className="metric-foot"><span>Manifest, metrics, and weights required</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Selected ML test trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{selectedId ? num(experiments.find((item) => item.experiment_id === selectedId)?.ml_test_trades) : "—"}</div><div className="metric-foot"><span>Stored untouched-test result</span></div></article>
      </section>

      {/* Completed Experiments & Tournament Trigger */}
      <section className="panel" aria-labelledby="ml-experiments-title">
        <div className="panel-heading">
          <div>
            <h2 id="ml-experiments-title">Completed experiments</h2>
            <p>Saved tournaments only. Metrics are displayed as returned by the API.</p>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            {canTrade ? (
              <button
                ref={tournamentTriggerRef}
                type="button"
                className="button button-primary"
                onClick={openTournamentModal}
              >
                <Plus size={15} /> Launch tournament
              </button>
            ) : null}
            <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}>
              <RefreshCw size={15} /> Refresh
            </button>
          </div>
        </div>

        <div className="backtest-filter">
          <label htmlFor="ml-experiment-filter">Filter experiments</label>
          <input
            id="ml-experiment-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Experiment, symbol, strategy, model…"
          />
        </div>

        {loading && !experiments.length ? (
          <p className="empty-state" role="status">Loading saved experiments…</p>
        ) : filteredExperiments.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Experiment</th>
                  <th scope="col">Market</th>
                  <th scope="col">Base strategy</th>
                  <th scope="col">Winner</th>
                  <th scope="col">Threshold</th>
                  <th scope="col">Baseline test R</th>
                  <th scope="col">ML test R</th>
                  <th scope="col">ML win rate</th>
                  <th scope="col">Trades</th>
                  <th scope="col">Dataset rows</th>
                </tr>
              </thead>
              <tbody>
                {filteredExperiments.map((item) => (
                  <tr key={item.experiment_id} className={selectedId === item.experiment_id ? "backtest-row-selected" : ""}>
                    <td>
                      <button className="backtest-run-select" aria-pressed={selectedId === item.experiment_id} onClick={() => setSelectedId(item.experiment_id)}>
                        {item.experiment_id}
                      </button>
                      <div>{item.symbol} · {item.timeframe}</div>
                    </td>
                    <td>{item.market}</td>
                    <td>{item.strategy}</td>
                    <td>{item.winner_model}</td>
                    <td>{percent(typeof item.threshold === "number" ? item.threshold * 100 : null)}</td>
                    <td>{num(item.baseline_test_r)}</td>
                    <td>{num(item.ml_test_r)}</td>
                    <td>{percent(item.ml_test_win_rate)}</td>
                    <td>{num(item.ml_test_trades)}</td>
                    <td>{num(item.dataset_rows)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">{experiments.length ? "No experiments match this filter." : "No saved ML experiments were returned."}</p>
        )}
      </section>

      {/* Saved Model Artifacts & ML-04 Deploy to Paper */}
      <section className="panel" aria-labelledby="ml-models-title">
        <div className="panel-heading">
          <div>
            <h2 id="ml-models-title">Saved model artifacts</h2>
            <p>Eligibility is based on stored test metrics, the manifest flag, and weight-file presence.</p>
          </div>
          <span className="panel-counter">{models.length} MODELS</span>
        </div>
        {models.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Model ID</th>
                  <th scope="col">Model type</th>
                  <th scope="col">Base strategy</th>
                  <th scope="col">Threshold</th>
                  <th scope="col">Weights</th>
                  <th scope="col">Manifest flag</th>
                  <th scope="col">Eligibility</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => {
                  const isEligible = model.eligibility?.eligible === true && model.eligible_for_paper === true && model.has_weights === true;
                  return (
                    <tr key={model.model_id}>
                      <td>
                        <strong>{model.model_id}</strong>
                        <div>{model.experiment_id ?? "Experiment ID unavailable"}</div>
                      </td>
                      <td>{model.model_type ?? "—"}</td>
                      <td>{model.base_strategy ?? "—"}</td>
                      <td>{typeof model.threshold === "number" ? percent(model.threshold * 100) : "—"}</td>
                      <td>{model.has_weights ? "Present" : "Missing"}</td>
                      <td>{model.eligible_for_paper === true ? "Eligible flag set" : "Research only"}</td>
                      <td>
                        <span className={model.eligibility?.eligible ? "status-pill status-positive" : "status-pill status-warning"}>
                          {model.eligibility?.eligible ? "Passes current gate" : "Not eligible"}
                        </span>
                        {model.eligibility?.reasons?.length ? (
                          <details className="backtest-params">
                            <summary>Reasons</summary>
                            <ul>
                              {model.eligibility.reasons.map((reason) => (
                                <li key={reason}>{reason}</li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </td>
                      <td>
                        {canTrade ? (
                          <button
                            type="button"
                            className="button button-secondary button-small"
                            disabled={!isEligible}
                            title={isEligible ? "Deploy this ML model as a signal filter in Live Paper Trading" : "Model does not meet paper deployment criteria"}
                            onClick={(e) => {
                              deployTriggerRef.current = e.currentTarget;
                              openDeployModal(model);
                            }}
                          >
                            Deploy to paper
                          </button>
                        ) : (
                          <span className="text-muted text-sm">View only</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">{loading ? "Loading model manifests…" : "No saved model manifests were returned."}</p>
        )}
      </section>

      {/* Selected Experiment Details */}
      <section className="panel" aria-labelledby="ml-details-title">
        <div className="panel-heading">
          <div>
            <h2 id="ml-details-title">Experiment details</h2>
            <p>Raw persisted experiment contract for traceability; no metrics are recalculated.</p>
          </div>
        </div>
        {detailError && <p role="alert" className="error-banner">{detailError}</p>}
        {detailLoading ? (
          <p className="empty-state" role="status">Loading experiment details…</p>
        ) : details ? (
          <details className="backtest-params" open>
            <summary>{selectedId}</summary>
            <pre>{JSON.stringify(details, null, 2)}</pre>
          </details>
        ) : (
          <p className="empty-state">{selectedId ? "Experiment details are not available." : "Select a completed experiment to inspect its saved result."}</p>
        )}
      </section>

      <p className="page-description">
        Scope note: generator training saves a research artifact only. Paper strategy activation and per-strategy Telegram opt-in are separate actions; this panel never activates either.
      </p>

      {/* ML-03 Modal: Launch Tournament */}
      {tournamentOpen && (
        <dialog
          ref={tournamentDialogRef}
          className="app-dialog"
          aria-labelledby="tournament-modal-title"
          aria-describedby="tournament-modal-desc"
          onCancel={(e) => { e.preventDefault(); closeTournamentModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeTournamentModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="tournament-modal-title">Launch ML Tournament</h3>
              <p id="tournament-modal-desc">
                Train and compare multiple ML models on closed candles. The best model will be saved as an experiment artifact.
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeTournamentModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <form onSubmit={handleExecuteTournament}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }}>
              <div>
                <label htmlFor="t-symbol" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Symbol</label>
                <input
                  id="t-symbol"
                  type="text"
                  required
                  value={tSymbol}
                  onChange={(e) => setTSymbol(e.target.value.toUpperCase())}
                  placeholder="BTCUSDT"
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>

              <div>
                <label htmlFor="t-market" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Market</label>
                <select
                  id="t-market"
                  value={tMarket}
                  onChange={(e) => setTMarket(e.target.value as "spot" | "futures")}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                >
                  <option value="spot">Spot</option>
                  <option value="futures">Futures</option>
                </select>
              </div>

              <div>
                <label htmlFor="t-timeframe" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Timeframe</label>
                <select
                  id="t-timeframe"
                  value={tTimeframe}
                  onChange={(e) => setTTimeframe(e.target.value as "15m" | "1h" | "4h" | "1d")}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                >
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                  <option value="1d">1d</option>
                </select>
              </div>

              <div>
                <label htmlFor="t-strategy" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Base Strategy</label>
                <select
                  id="t-strategy"
                  value={tStrategy}
                  onChange={(e) => setTStrategy(e.target.value)}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                >
                  {SUPPORTED_BASE_STRATEGIES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="t-model-type" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Model Type</label>
                <select
                  id="t-model-type"
                  value={tModelType}
                  onChange={(e) => setTModelType(e.target.value)}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                >
                  <option value="all">All (Tournament)</option>
                  <option value="hist_gradient_boosting">Hist Gradient Boosting</option>
                  <option value="random_forest">Random Forest</option>
                  <option value="logistic_regression">Logistic Regression</option>
                </select>
              </div>

              <div>
                <label htmlFor="t-target-type" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Target Type</label>
                <select
                  id="t-target-type"
                  value={tTargetType}
                  onChange={(e) => setTTargetType(e.target.value)}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                >
                  <option value="binary_positive_r">Binary Positive R</option>
                  <option value="multi_class">Multi Class</option>
                </select>
              </div>

              <div>
                <label htmlFor="t-min-trades" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Min Validation Trades</label>
                <input
                  id="t-min-trades"
                  type="number"
                  min={1}
                  max={100}
                  value={tMinTrades}
                  onChange={(e) => setTMinTrades(parseInt(e.target.value, 10) || 5)}
                  disabled={tournamentBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>
            </div>

            {tournamentError && (
              <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
                <div><p>{tournamentError}</p></div>
              </div>
            )}

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button type="button" className="button button-secondary" onClick={closeTournamentModal} disabled={tournamentBusy}>
                Cancel
              </button>
              <button type="submit" className="button button-primary" disabled={tournamentBusy}>
                {tournamentBusy ? "Running Tournament…" : "Launch Tournament"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {/* ML-04 Modal: Deploy ML Filter to Paper Trading */}
      {deployModel && (
        <dialog
          ref={deployDialogRef}
          className="app-dialog"
          aria-labelledby="deploy-modal-title"
          aria-describedby="deploy-modal-desc"
          onCancel={(e) => { e.preventDefault(); closeDeployModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeDeployModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="deploy-modal-title">Deploy ML Model to Paper Trading</h3>
              <p id="deploy-modal-desc">
                Deploy model <strong>{deployModel.model_id}</strong> as an active AI confidence filter for simulated trades.
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeDeployModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <div style={{ background: "var(--color-elevated)", padding: "12px", borderRadius: "8px", marginBottom: "16px", fontSize: "0.85rem" }}>
            <div><strong>Base strategy:</strong> {deployModel.base_strategy ?? "—"}</div>
            <div><strong>Model type:</strong> {deployModel.model_type ?? "—"}</div>
            <div><strong>Threshold:</strong> {typeof deployModel.threshold === "number" ? percent(deployModel.threshold * 100) : "—"}</div>
          </div>

          <form onSubmit={handleExecuteDeploy}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }}>
              <div>
                <label htmlFor="d-capital" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Allocated Capital (USD)</label>
                <input
                  id="d-capital"
                  type="number"
                  min={100}
                  step={100}
                  required
                  value={deployCapital}
                  onChange={(e) => setDeployCapital(parseFloat(e.target.value) || 0)}
                  disabled={deployBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>

              <div>
                <label htmlFor="d-risk" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Risk Per Trade (%)</label>
                <input
                  id="d-risk"
                  type="number"
                  min={0.1}
                  max={10.0}
                  step={0.1}
                  required
                  value={deployRisk}
                  onChange={(e) => setDeployRisk(parseFloat(e.target.value) || 0)}
                  disabled={deployBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
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
              <p style={{ margin: "4px 0 0 24px", color: "var(--color-muted)", fontSize: "0.75rem" }}>
                Telegram broadcasts require bot credentials and user opt-in.
              </p>
            </div>

            {deployError && (
              <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
                <div><p>{deployError}</p></div>
              </div>
            )}

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button type="button" className="button button-secondary" onClick={closeDeployModal} disabled={deployBusy}>
                Cancel
              </button>
              <button type="submit" className="button button-primary" disabled={deployBusy}>
                {deployBusy ? "Deploying…" : "Deploy to Paper Trading"}
              </button>
            </div>
          </form>
        </dialog>
      )}
    </div>
  );
}
