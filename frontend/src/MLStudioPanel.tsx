import { useEffect, useMemo, useState } from "react";
import { Activity, BrainCircuit, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchMLExperimentDetails, fetchMLExperiments, fetchMLModels, type MLExperimentDetails, type MLExperimentSummary, type MLModelManifest } from "./lib/mlStudio";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
const percentFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
function num(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? numberFormat.format(value) : "—"; }
function percent(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? `${percentFormat.format(value)}%` : "—"; }
function dateLabel(value: unknown) {
  if (typeof value !== "string" || !value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view ML research data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view ML research data.";
  return "ML research data could not be loaded. Check API and artifact-directory health, then retry.";
}
function detailValue(value: unknown) {
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value ?? "—");
  return JSON.stringify(value);
}
export function MLStudioPanel() {
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

  const filteredExperiments = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return experiments.filter((item) => !needle || [item.experiment_id, item.symbol, item.market, item.timeframe, item.strategy, item.winner_model].some((value) => String(value ?? "").toLowerCase().includes(needle)));
  }, [experiments, filter]);
  const eligibleModels = models.filter((model) => model.eligibility?.eligible === true && model.eligible_for_paper === true && model.has_weights === true).length;

  return <div className="ml-studio-view" aria-busy={loading || detailLoading}>
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Read-only ML Studio</strong><p>Inspect saved tournament results, model manifests, artifact availability, and eligibility diagnostics. Launching experiments, deploying models to paper trading, changing strategy configuration, and sending Telegram alerts are not available here.</p></div><span className="panel-counter">READ ONLY</span></section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load ML Studio</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="ML research summary">
      <article className="metric-card"><div className="metric-top"><span>Saved experiments</span><span className="metric-icon"><BrainCircuit size={17} /></span></div><div className="metric-value">{loading && !experiments.length ? "Loading…" : experiments.length}</div><div className="metric-foot"><span>Persisted result manifests</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Model artifacts</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !models.length ? "Loading…" : models.length}</div><div className="metric-foot"><span>Model manifests found</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Passing eligibility checks</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{eligibleModels}</div><div className="metric-foot"><span>Manifest, metrics, and weights required</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Selected ML test trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{selectedId ? num(experiments.find((item) => item.experiment_id === selectedId)?.ml_test_trades) : "—"}</div><div className="metric-foot"><span>Stored untouched-test result</span></div></article>
    </section>
    <section className="panel" aria-labelledby="ml-experiments-title">
      <div className="panel-heading"><div><h2 id="ml-experiments-title">Completed experiments</h2><p>Saved tournaments only. Metrics are displayed as returned by the API.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      <div className="backtest-filter"><label htmlFor="ml-experiment-filter">Filter experiments</label><input id="ml-experiment-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Experiment, symbol, strategy, model…" /></div>
      {loading && !experiments.length ? <p className="empty-state" role="status">Loading saved experiments…</p> : filteredExperiments.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Experiment</th><th scope="col">Market</th><th scope="col">Base strategy</th><th scope="col">Winner</th><th scope="col">Threshold</th><th scope="col">Baseline test R</th><th scope="col">ML test R</th><th scope="col">ML win rate</th><th scope="col">Trades</th><th scope="col">Dataset rows</th></tr></thead><tbody>{filteredExperiments.map((item) => <tr key={item.experiment_id} className={selectedId === item.experiment_id ? "backtest-row-selected" : ""}><td><button className="backtest-run-select" aria-pressed={selectedId === item.experiment_id} onClick={() => setSelectedId(item.experiment_id)}>{item.experiment_id}</button><div>{item.symbol} · {item.timeframe}</div></td><td>{item.market}</td><td>{item.strategy}</td><td>{item.winner_model}</td><td>{percent(typeof item.threshold === "number" ? item.threshold * 100 : null)}</td><td>{num(item.baseline_test_r)}</td><td>{num(item.ml_test_r)}</td><td>{percent(item.ml_test_win_rate)}</td><td>{num(item.ml_test_trades)}</td><td>{num(item.dataset_rows)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{experiments.length ? "No experiments match this filter." : "No saved ML experiments were returned."}</p>}
    </section>
    <section className="panel" aria-labelledby="ml-models-title">
      <div className="panel-heading"><div><h2 id="ml-models-title">Saved model artifacts</h2><p>Eligibility is based on stored test metrics, the manifest flag, and weight-file presence.</p></div><span className="panel-counter">{models.length} MODELS</span></div>
      {models.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Model ID</th><th scope="col">Model type</th><th scope="col">Base strategy</th><th scope="col">Threshold</th><th scope="col">Weights</th><th scope="col">Manifest flag</th><th scope="col">Eligibility</th></tr></thead><tbody>{models.map((model) => <tr key={model.model_id}><td><strong>{model.model_id}</strong><div>{model.experiment_id ?? "Experiment ID unavailable"}</div></td><td>{model.model_type ?? "—"}</td><td>{model.base_strategy ?? "—"}</td><td>{typeof model.threshold === "number" ? percent(model.threshold * 100) : "—"}</td><td>{model.has_weights ? "Present" : "Missing"}</td><td>{model.eligible_for_paper === true ? "Eligible flag set" : "Research only"}</td><td><span className={model.eligibility?.eligible ? "status-pill status-positive" : "status-pill status-warning"}>{model.eligibility?.eligible ? "Passes current gate" : "Not eligible"}</span>{model.eligibility?.reasons?.length ? <details className="backtest-params"><summary>Reasons</summary><ul>{model.eligibility.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></details> : null}</td></tr>)}</tbody></table></div> : <p className="empty-state">{loading ? "Loading model manifests…" : "No saved model manifests were returned."}</p>}
    </section>
    <section className="panel" aria-labelledby="ml-details-title">
      <div className="panel-heading"><div><h2 id="ml-details-title">Experiment details</h2><p>Raw persisted experiment contract for traceability; no metrics are recalculated.</p></div></div>
      {detailError && <p role="alert" className="error-banner">{detailError}</p>}
      {detailLoading ? <p className="empty-state" role="status">Loading experiment details…</p> : details ? <details className="backtest-params" open><summary>{selectedId}</summary><pre>{JSON.stringify(details, null, 2)}</pre></details> : <p className="empty-state">{selectedId ? "Experiment details are not available." : "Select a completed experiment to inspect its saved result."}</p>}
    </section>
    <p className="page-description">Scope note: only GET endpoints are called. Experiment execution, model deployment, paper configuration, and Telegram actions remain unavailable.</p>
  </div>;
}
