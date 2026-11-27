import { useCallback, useEffect, useState, type FormEvent } from "react";
import { RefreshCw, Repeat } from "lucide-react";
import {
  fetchRetrainingStatus, registerRetrainingModel, removeRetrainingModel, setRetrainingEnabled,
  setRetrainingModelEnabled, triggerModelRetraining, triggerRetraining, type RetrainingStatus,
} from "../lib/mlRetraining";

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"];

/** Continuous ML retraining: global switch, per-model pause/remove, register, manual run. */
export function RetrainingSection() {
  const [status, setStatus] = useState<RetrainingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: "", market: "spot" as "spot" | "futures", timeframe: "1h", strategy_name: "trend_ema_v1", model_type: "hist_gradient_boosting" });

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await fetchRetrainingStatus(signal);
      if (!signal?.aborted) { setStatus(next); setError(null); }
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Failed to load retraining status");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(null); setNotice(null);
    try { await action(); setNotice(success); setRemoving(null); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Action failed"); }
    finally { setBusy(false); }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!form.symbol.trim()) { setError("Symbol is required"); return; }
    void run(() => registerRetrainingModel(form), "Model registered for continuous retraining.");
  };

  return (
    <section className="panel" aria-labelledby="ml-retraining-title">
      <div className="panel-heading">
        <div>
          <h2 id="ml-retraining-title"><Repeat size={17} className="inline-icon" /> Continuous Retraining</h2>
          <p className="panel-subtitle">
            Registered models are retrained on a schedule. Automatic promotion is not implemented: retraining never deploys a model by itself.
          </p>
        </div>
        <button type="button" className="button button-secondary" onClick={() => void load()} disabled={busy}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      </div>

      {error && <div className="api-error" role="alert">{error}</div>}
      {notice && <p className="empty-state" role="status">{notice}</p>}

      {status && (
        <>
          <p>
            Subsystem: <strong>{status.enabled ? "Enabled" : "Disabled"}</strong> · every {status.retrain_interval_hours}h · min {status.min_new_candles} new candles · max drift PSI {status.max_drift_psi}
          </p>
          <div className="button-row">
            <button type="button" className="button button-secondary" disabled={busy}
              onClick={() => void run(() => setRetrainingEnabled(!status.enabled), status.enabled ? "Retraining disabled." : "Retraining enabled.")}>
              {status.enabled ? "Disable retraining" : "Enable retraining"}
            </button>{" "}
            <button type="button" className="button button-primary" disabled={busy || !status.enabled}
              onClick={() => void run(() => triggerRetraining(false), "Retraining cycle queued; only models that are due will run.")}>
              Run due models now
            </button>
          </div>

          {status.models.length === 0 ? (
            <p className="empty-state">No models are registered for retraining.</p>
          ) : (
            <div className="status-table-wrap">
              <table className="status-table">
                <thead><tr><th>Model</th><th>State</th><th>Last retrained</th><th>Drift PSI</th><th>Actions</th></tr></thead>
                <tbody>
                  {status.models.map((m) => {
                    const on = m.enabled !== false;
                    return (
                      <tr key={m.key}>
                        <td><strong>{m.symbol}</strong> · {m.market} · {m.timeframe}<div className="panel-subtitle">{m.strategy_name} · {m.model_type ?? "—"}</div></td>
                        <td><span className={on ? "badge badge-ok" : "badge badge-warn"}>{on ? "Active" : "Paused"}</span></td>
                        <td>{m.last_retrained_at ?? "never"}</td>
                        <td>{typeof m.drift_psi === "number" ? m.drift_psi.toFixed(3) : "—"}</td>
                        <td>
                          {removing === m.key ? (
                            <div role="group" aria-label={`Confirm removing ${m.symbol} ${m.strategy_name}`}>
                              <p className="api-error" role="alert">Removes this model from retraining. Saved model artifacts are kept.</p>
                              <button type="button" className="button button-danger" disabled={busy}
                                onClick={() => void run(() => removeRetrainingModel(m.key), "Model removed from retraining.")}>
                                Confirm remove {m.symbol}
                              </button>{" "}
                              <button type="button" className="button button-secondary" onClick={() => setRemoving(null)}>Cancel</button>
                            </div>
                          ) : (
                            <>
                              <button type="button" className="button button-secondary button-sm" disabled={busy || !status.enabled || !on}
                                onClick={() => void run(() => triggerModelRetraining(m.key, true), `Retraining queued for ${m.symbol} ${m.strategy_name}.`)}>
                                Run {m.symbol} {m.strategy_name} now
                              </button>{" "}
                              <button type="button" className="button button-secondary button-sm" disabled={busy}
                                onClick={() => void run(() => setRetrainingModelEnabled(m.key, !on), on ? "Model paused." : "Model resumed.")}>
                                {on ? `Pause ${m.symbol} ${m.strategy_name}` : `Resume ${m.symbol} ${m.strategy_name}`}
                              </button>{" "}
                              <button type="button" className="button button-secondary button-sm" disabled={busy} onClick={() => setRemoving(m.key)}>
                                Remove {m.symbol} {m.strategy_name}
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="panel-subsection" style={{ marginTop: 12 }}>
            <h3>Retraining history (recent audit)</h3>
            {status.recent_audit.length === 0 ? (
              <p className="empty-state">No retraining runs recorded yet.</p>
            ) : (
              <div className="status-table-wrap">
                <table className="status-table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Timestamp</th>
                      <th>Status</th>
                      <th>Winner</th>
                      <th>New candles</th>
                      <th>Drift PSI</th>
                      <th>Baseline → ML (R)</th>
                      <th>Promotion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.recent_audit.map((entry, idx) => (
                      <tr key={`${entry.key ?? "run"}-${entry.timestamp ?? idx}-${idx}`}>
                        <td>{entry.key ?? "—"}</td>
                        <td>{entry.timestamp ?? "—"}</td>
                        <td>
                          <span className={entry.status === "retrained" ? "badge badge-ok" : "badge badge-warn"}>
                            {entry.status ?? "—"}
                          </span>
                          {entry.message ? <div className="panel-subtitle">{entry.message}</div> : null}
                        </td>
                        <td>{entry.winner_model ?? "—"}</td>
                        <td>{typeof entry.new_candles_processed === "number" ? entry.new_candles_processed : "—"}</td>
                        <td>{typeof entry.drift_psi === "number" ? entry.drift_psi.toFixed(3) : "—"}</td>
                        <td>
                          {typeof entry.baseline_test_r === "number" || typeof entry.ml_test_r === "number"
                            ? `${typeof entry.baseline_test_r === "number" ? entry.baseline_test_r.toFixed(2) : "—"} → ${typeof entry.ml_test_r === "number" ? entry.ml_test_r.toFixed(2) : "—"}`
                            : "—"}
                        </td>
                        <td>{entry.promotion_status ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      <form onSubmit={submit} className="screener-control-grid" aria-label="Register model for retraining" style={{ marginTop: 12 }}>
        <div className="screener-control-field"><label htmlFor="rt-symbol">Symbol</label>
          <input id="rt-symbol" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} placeholder="BTCUSDT" /></div>
        <div className="screener-control-field"><label htmlFor="rt-market">Market</label>
          <select id="rt-market" value={form.market} onChange={(e) => setForm({ ...form, market: e.target.value as "spot" | "futures" })}>
            <option value="spot">spot</option><option value="futures">futures</option></select></div>
        <div className="screener-control-field"><label htmlFor="rt-timeframe">Timeframe</label>
          <select id="rt-timeframe" value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}</select></div>
        <div className="screener-control-field"><label htmlFor="rt-strategy">Strategy</label>
          <input id="rt-strategy" value={form.strategy_name} onChange={(e) => setForm({ ...form, strategy_name: e.target.value })} /></div>
        <div className="screener-control-field"><label htmlFor="rt-model">Model type</label>
          <input id="rt-model" value={form.model_type} onChange={(e) => setForm({ ...form, model_type: e.target.value })} /></div>
        <div className="screener-control-field"><button type="submit" className="button button-primary" disabled={busy}>Register model</button></div>
      </form>
    </section>
  );
}
