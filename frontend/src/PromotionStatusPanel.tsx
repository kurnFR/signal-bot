import { useEffect, useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useAuth } from "./AuthGate";
import {
  fetchPromotionSnapshot,
  savePromotionPolicy,
  type PromotionPolicy,
  type PromotionSnapshot,
} from "./lib/backtestStudio";

const numberLabel = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
function timestampLabel(value: string | null) {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
}
function policyError(cause: unknown) {
  if (cause && typeof cause === "object" && "status" in cause && cause.status === 403) return "Your account cannot change the promotion policy.";
  if (cause instanceof Error) return cause.message;
  return "Promotion policy could not be saved. Refresh and retry.";
}

interface PromotionStatusPanelProps { onPolicyLoaded?: (policy: PromotionPolicy | null) => void; }
export function PromotionStatusPanel({ onPolicyLoaded }: PromotionStatusPanelProps) {
  const auth = useAuth();
  const canEdit = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [snapshot, setSnapshot] = useState<PromotionSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draftPolicy, setDraftPolicy] = useState<PromotionPolicy | null>(null);
  const [pendingSave, setPendingSave] = useState<PromotionPolicy | null>(null);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [acknowledgeAutoPromotion, setAcknowledgeAutoPromotion] = useState(false);
  const [acknowledgeTelegram, setAcknowledgeTelegram] = useState(false);
  const [readbackUnverified, setReadbackUnverified] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    onPolicyLoaded?.(null);
    fetchPromotionSnapshot(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setSnapshot(result);
        setDraftPolicy(result.policy);
        setReadbackUnverified(false);
        setSaveNotice(null);
        setSaveError(null);
        onPolicyLoaded?.(result.policy);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setSnapshot(null);
          onPolicyLoaded?.(null);
          setError("Promotion policy and audit could not be loaded. Check API/database health, then retry.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt, onPolicyLoaded]);

  const beginEditing = () => {
    if (!snapshot || !canEdit || readbackUnverified) return;
    setDraftPolicy({ ...snapshot.policy });
    setEditing(true);
    setPendingSave(null);
    setSaveError(null);
    setSaveNotice(null);
  };
  const cancelEditing = () => {
    setDraftPolicy(snapshot ? { ...snapshot.policy } : null);
    setEditing(false);
    setPendingSave(null);
    setSaveError(null);
    setAcknowledgeAutoPromotion(false);
    setAcknowledgeTelegram(false);
  };
  const confirmSave = async () => {
    if (!pendingSave || saveBusy || (pendingSave.enabled && !acknowledgeAutoPromotion) ||
        (pendingSave.enabled && pendingSave.send_telegram && !acknowledgeTelegram)) return;
    setSaveBusy(true);
    setSaveError(null);
    setSaveNotice(null);
    try {
      const result = await savePromotionPolicy(pendingSave);
      setPendingSave(null);
      setEditing(false);
      setAcknowledgeAutoPromotion(false);
      setAcknowledgeTelegram(false);
      if (result.status === "saved_readback_unavailable") {
        setSnapshot(null);
        setDraftPolicy(null);
        setReadbackUnverified(true);
        onPolicyLoaded?.(null);
        setSaveNotice(result.warning || "Policy save completed, but the saved state could not be verified. Refresh before continuing.");
        return;
      }
      setSnapshot((current) => current ? { ...current, policy: result.policy } : current);
      setDraftPolicy(result.policy);
      setReadbackUnverified(false);
      onPolicyLoaded?.(result.policy);
      setSaveNotice("Promotion policy saved and verified by readback.");
    } catch (cause) {
      setSaveError(policyError(cause));
    } finally {
      setSaveBusy(false);
    }
  };

  return <section className="panel" aria-labelledby="promotion-status-title" aria-busy={loading || saveBusy}>
    <div className="panel-heading">
      <div>
        <h2 id="promotion-status-title">Auto-promotion policy and audit</h2>
        <p>Saved thresholds and recent decisions. Editing policy does not itself run a backtest, activate a strategy, or send Telegram.</p>
      </div>
      <div className="screener-control-footer">
        <button className="button button-secondary" type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading || editing || Boolean(pendingSave) || saveBusy}>
          <RefreshCw size={15} /> Refresh
        </button>
        {canEdit && snapshot && !editing && !readbackUnverified && <button className="button button-primary" type="button" onClick={beginEditing} disabled={loading || saveBusy}>Edit policy</button>}
      </div>
    </div>
    {error && <div className="api-error" role="alert">
      <div><strong>Promotion status unavailable</strong><p>{error}</p></div>
      <button className="button button-secondary" type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading}>Retry</button>
    </div>}
    {saveError && <div className="api-error" role="alert"><div><strong>Policy save failed</strong><p>{saveError}</p></div><button className="button button-secondary" type="button" onClick={() => setSaveError(null)}>Dismiss</button></div>}
    {saveNotice && (readbackUnverified ? <div className="api-error" role="alert"><p>{saveNotice}</p><button className="button button-secondary" type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading}>Refresh saved state</button></div> : <p className="control-feedback control-success" role="status">{saveNotice}</p>)}
    {readbackUnverified && !loading && <p className="empty-state">The last policy write completed without authoritative readback. Editing and backtest review stay disabled until a refresh verifies the current policy.</p>}
    {loading && !snapshot ? <p className="empty-state" role="status">Loading promotion policy and audit…</p> : snapshot && <>
      <div className="panel-subsection">
        <div className="panel-heading">
          <div><h3>Saved policy</h3><p>Thresholds are shown as currently stored by the backend.</p></div>
          <span className="panel-counter">{snapshot.policy.enabled ? "ENABLED" : "DISABLED"}</span>
        </div>
        <div className="metrics-grid" aria-label="Promotion policy thresholds">
          <article className="metric-card"><div className="metric-top"><span>Minimum test trades</span></div><div className="metric-value">{snapshot.policy.min_test_trades}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum profit factor</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_profit_factor)}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum expectancy</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_expectancy_r)} R</div></article>
          <article className="metric-card"><div className="metric-top"><span>Maximum drawdown</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.max_drawdown_r)} R</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum rank score</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_rank_score)}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Holdout required</span></div><div className="metric-value">{snapshot.policy.require_holdout ? "Yes" : "No"}</div></article>
        </div>
        <p className="control-feedback"><ShieldCheck size={15} /> Telegram alert setting: <strong>{snapshot.policy.send_telegram ? "Enabled" : "Disabled"}</strong>. When auto-promotion is enabled, this setting may cause an external notification after a qualifying backtest.</p>
      </div>

      {canEdit && editing && draftPolicy && <div className="panel-subsection" aria-labelledby="promotion-policy-edit-title">
        <div className="panel-heading"><div><h3 id="promotion-policy-edit-title">Edit promotion policy</h3><p>Only admin/trader roles can save changes. Saving affects future backtest runs; it does not execute promotion immediately.</p></div></div>
        <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); setPendingSave({ ...draftPolicy }); setAcknowledgeAutoPromotion(false); setAcknowledgeTelegram(false); setSaveError(null); }}>
          <label className="screener-control-field" htmlFor="promotion-min-test-trades">Minimum test trades<input id="promotion-min-test-trades" type="number" min="30" max="10000" step="1" required value={draftPolicy.min_test_trades} onChange={(event) => setDraftPolicy((current) => current ? { ...current, min_test_trades: Number(event.target.value) } : current)} /></label>
          <label className="screener-control-field" htmlFor="promotion-min-profit-factor">Minimum profit factor<input id="promotion-min-profit-factor" type="number" min="1" max="10" step="0.05" required value={draftPolicy.min_profit_factor} onChange={(event) => setDraftPolicy((current) => current ? { ...current, min_profit_factor: Number(event.target.value) } : current)} /></label>
          <label className="screener-control-field" htmlFor="promotion-min-expectancy">Minimum expectancy (R)<input id="promotion-min-expectancy" type="number" min="0" max="10" step="0.01" required value={draftPolicy.min_expectancy_r} onChange={(event) => setDraftPolicy((current) => current ? { ...current, min_expectancy_r: Number(event.target.value) } : current)} /></label>
          <label className="screener-control-field" htmlFor="promotion-max-drawdown">Maximum drawdown (R)<input id="promotion-max-drawdown" type="number" min="0.01" max="100" step="0.01" required value={draftPolicy.max_drawdown_r} onChange={(event) => setDraftPolicy((current) => current ? { ...current, max_drawdown_r: Number(event.target.value) } : current)} /></label>
          <label className="screener-control-field" htmlFor="promotion-min-rank-score">Minimum rank score<input id="promotion-min-rank-score" type="number" min="0.01" max="1" step="0.01" required value={draftPolicy.min_rank_score} onChange={(event) => setDraftPolicy((current) => current ? { ...current, min_rank_score: Number(event.target.value) } : current)} /></label>
          <label className="screener-alert-toggle"><input type="checkbox" checked={draftPolicy.enabled} onChange={(event) => setDraftPolicy((current) => current ? { ...current, enabled: event.target.checked } : current)} /><span><strong>Enable automatic promotion after qualifying backtests</strong></span></label>
          <label className="screener-alert-toggle"><input type="checkbox" checked={draftPolicy.require_holdout} onChange={(event) => setDraftPolicy((current) => current ? { ...current, require_holdout: event.target.checked } : current)} /><span><strong>Require independent holdout validation</strong></span></label>
          <label className="screener-alert-toggle"><input type="checkbox" checked={draftPolicy.send_telegram} onChange={(event) => setDraftPolicy((current) => current ? { ...current, send_telegram: event.target.checked } : current)} /><span><strong>Allow Telegram alerts for qualifying promotions</strong></span></label>
          <div className="screener-control-footer"><button className="button button-secondary" type="button" onClick={cancelEditing} disabled={saveBusy}>Cancel</button><button className="button button-primary" type="submit" disabled={saveBusy}>Review policy changes</button></div>
        </form>
      </div>}

      {pendingSave && <div className="panel-subsection" aria-labelledby="promotion-policy-confirm-title">
        <div className="panel-heading"><div><h3 id="promotion-policy-confirm-title">Confirm policy changes</h3><p>Saving this configuration changes the behavior of future backtest runs.</p></div></div>
        {pendingSave.enabled
          ? <div className="api-error" role="note"><div><strong>Automatic promotion will be enabled</strong><p>Qualifying backtests may activate paper strategies. Telegram alerts are {pendingSave.send_telegram ? "enabled and may send external messages" : "disabled"} under this policy.</p></div></div>
          : <p className="empty-state">Automatic promotion will be disabled by this policy. This save does not activate strategies or send Telegram.</p>}
        {pendingSave.enabled && <label className="screener-alert-toggle"><input aria-label="Acknowledge automatic promotion behavior" type="checkbox" checked={acknowledgeAutoPromotion} onChange={(event) => setAcknowledgeAutoPromotion(event.target.checked)} /><span><strong>I understand qualifying benchmarks may activate paper strategies.</strong></span></label>}
        {pendingSave.enabled && pendingSave.send_telegram && <label className="screener-alert-toggle"><input aria-label="Acknowledge Telegram alert side effects" type="checkbox" checked={acknowledgeTelegram} onChange={(event) => setAcknowledgeTelegram(event.target.checked)} /><span><strong>I explicitly acknowledge that Telegram alerts may be sent externally.</strong></span></label>}
        <div className="screener-control-footer"><button className="button button-secondary" type="button" onClick={() => { setPendingSave(null); setAcknowledgeAutoPromotion(false); setAcknowledgeTelegram(false); }} disabled={saveBusy}>Back to edit</button><button className="button button-primary" type="button" onClick={() => void confirmSave()} disabled={saveBusy || (pendingSave.enabled && !acknowledgeAutoPromotion) || (pendingSave.enabled && pendingSave.send_telegram && !acknowledgeTelegram)}>{saveBusy ? "Saving policy…" : "Save promotion policy"}</button></div>
      </div>}

      <div className="panel-subsection">
        <div className="panel-heading"><div><h3>Recent promotion decisions</h3><p>Decision records can include rejected, promoted, alert-failed, and reconciliation-required outcomes.</p></div><span className="panel-counter">{snapshot.events.length} RECORDS</span></div>
        {snapshot.events.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Created</th><th scope="col">Strategy</th><th scope="col">Market</th><th scope="col">Segment</th><th scope="col">Status</th><th scope="col">Paper config</th><th scope="col">Telegram result</th><th scope="col">Reason</th></tr></thead><tbody>
          {snapshot.events.map((event) => <tr key={event.decision_hash}>
            <td>{timestampLabel(event.created_at)}</td><td><strong>{event.strategy_name}</strong><div>{event.symbol} · {event.timeframe}</div></td><td>{event.market}</td><td>{event.data_segment}</td><td>{event.status.replace(/_/g, " ")}</td><td>{event.paper_config_id ?? "—"}</td><td>{event.telegram_result || "—"}</td><td>{event.reason || "—"}</td>
          </tr>)}
        </tbody></table></div> : <p className="empty-state">No promotion decisions have been recorded.</p>}
      </div>
    </>}
  </section>;
}
