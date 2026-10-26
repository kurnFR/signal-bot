import { useEffect, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useAuth } from "./AuthGate";
import { ApiError } from "./lib/api";
import { fetchScreenerControl, saveScreenerControl, ScreenerControlError, type ScreenerControlResponse } from "./lib/screener";

const NUMBER_FIELDS = [
  { key: "rvol_multiplier", label: "RVOL multiplier", min: 1, max: 100, step: 0.1 },
  { key: "divergence_max_price_change", label: "Divergence max price change (%)", min: 0, max: 20, step: 0.1 },
  { key: "velocity_threshold", label: "Velocity threshold", min: 0.1, max: 100, step: 0.1 },
  { key: "max_alerts_per_hour", label: "Maximum alerts per hour", min: 1, max: 1000, step: 1 },
  { key: "max_alerts_per_symbol_per_hour", label: "Maximum alerts per symbol per hour", min: 1, max: 100, step: 1 },
  { key: "alert_cooldown_sec", label: "Alert cooldown (seconds)", min: 0, max: 86400, step: 10 },
  { key: "min_signal_rvol", label: "Minimum signal RVOL", min: 1, max: 100, step: 0.1 },
  { key: "min_signal_velocity", label: "Minimum signal velocity", min: 1, max: 100, step: 0.1 },
  { key: "min_quote_volume_24h", label: "Minimum 24h quote volume (USD)", min: 0, max: 1000000000000, step: 100000 },
  { key: "min_market_cap_usd", label: "Minimum market cap (USD)", min: 0, max: 1000000000000, step: 1000000 },
  { key: "min_quality_score", label: "Minimum quality score", min: 0, max: 100, step: 1 },
  { key: "min_alert_quality_score", label: "Minimum Telegram alert quality", min: 0, max: 100, step: 1 },
  { key: "max_signal_age_sec", label: "Maximum signal age (seconds)", min: 1, max: 86400, step: 30 },
  { key: "expired_signal_retention_days", label: "Expired signal retention (days)", min: 1, max: 3650, step: 1 },
  { key: "expired_signal_cleanup_interval_days", label: "Expired signal cleanup interval (days)", min: 1, max: 365, step: 1 },
] as const;
const BOOLEAN_FIELDS = [
  { key: "enable_divergence_detection", label: "Enable divergence detection" },
  { key: "enable_velocity_detection", label: "Enable velocity detection" },
  { key: "require_confluence", label: "Require signal confluence" },
] as const;
type BooleanDraft = Record<string, { override: boolean; value: boolean }>;

export function ScreenerControlsPanel() {
  const auth = useAuth();
  const canEdit = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [control, setControl] = useState<ScreenerControlResponse | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [numbers, setNumbers] = useState<Record<string, string>>({});
  const [booleans, setBooleans] = useState<BooleanDraft>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!canEdit) return;
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchScreenerControl(controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setControl(result); setEnabled(result.enabled);
      const nextNumbers: Record<string, string> = {};
      for (const field of NUMBER_FIELDS) nextNumbers[field.key] = Object.prototype.hasOwnProperty.call(result.overrides, field.key) ? String(result.overrides[field.key]) : "";
      const nextBooleans: BooleanDraft = {};
      for (const field of BOOLEAN_FIELDS) {
        const hasOverride = Object.prototype.hasOwnProperty.call(result.overrides, field.key);
        nextBooleans[field.key] = { override: hasOverride, value: hasOverride ? Boolean(result.overrides[field.key]) : true };
      }
      setNumbers(nextNumbers); setBooleans(nextBooleans);
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setError(cause instanceof ApiError ? cause.message : "Screener settings are unavailable. Confirm the screener control table has been initialized.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [canEdit, reload]);

  const handleSave = async () => {
    if (!control) return;
    setError(null); setMessage(null);
    const nextOverrides: Record<string, number | boolean> = { ...control.overrides };
    for (const field of NUMBER_FIELDS) {
      const raw = (numbers[field.key] ?? "").trim();
      if (!raw) { delete nextOverrides[field.key]; continue; }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < field.min || value > field.max) { setError(`${field.label} must be between ${field.min} and ${field.max}.`); return; }
      nextOverrides[field.key] = value;
    }
    for (const field of BOOLEAN_FIELDS) {
      const draft = booleans[field.key];
      if (draft?.override) nextOverrides[field.key] = draft.value;
      else delete nextOverrides[field.key];
    }
    setSaving(true);
    try {
      const result = await saveScreenerControl(enabled, nextOverrides);
      setControl({ ...control, enabled, overrides: nextOverrides });
      setMessage(result.note || "Screener settings saved.");
    } catch (cause) {
      if (cause instanceof ScreenerControlError && cause.status === 403) setError("Your account cannot change screener settings. Only admin or trader roles can save these controls.");
      else setError(cause instanceof Error ? cause.message : "Unable to save screener settings.");
    } finally { setSaving(false); }
  };

  if (!canEdit) return <section className="panel screener-controls-readonly"><div className="panel-heading"><div><h2>Screener configuration</h2><p>Configuration editing is restricted to admin and trader roles.</p></div><ShieldCheck size={18} /></div><p className="empty-state">You can view screener status and signals. Use an authorized role to change the alert gate or detection thresholds.</p></section>;

  return <section className="panel" aria-labelledby="screener-controls-title" aria-busy={loading || saving}>
    <div className="panel-heading"><div><h2 id="screener-controls-title">Screener configuration</h2><p>Changes are persisted and picked up by the running worker on its next control-loop tick. Saving does not restart the worker.</p></div><button className="button button-secondary" onClick={() => setReload((value) => value + 1)} disabled={loading || saving}><RefreshCw size={15} /> Reload</button></div>
    {error && <p className="control-feedback control-error" role="alert">{error}</p>}
    {message && <p className="control-feedback control-success" role="status">{message}</p>}
    {loading && !control ? <p className="empty-state" role="status">Loading saved control settings…</p> : control ? <>
      <label className="screener-alert-toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span><strong>Telegram alerting enabled</strong><small>Pausing alerts does not stop signal detection.</small></span></label>
      <div className="screener-control-grid">
        {NUMBER_FIELDS.map((field) => <label className="screener-control-field" key={field.key}>{field.label}<input type="number" min={field.min} max={field.max} step={field.step} value={numbers[field.key] ?? ""} placeholder="Use server default" onChange={(event) => setNumbers((current) => ({ ...current, [field.key]: event.target.value }))} /><small>{Object.prototype.hasOwnProperty.call(control.overrides, field.key) ? "Saved override · clear to restore server default" : "No override · server default applies"}</small></label>)}
      </div>
      <div className="screener-boolean-grid">{BOOLEAN_FIELDS.map((field) => { const draft = booleans[field.key] ?? { override: false, value: true }; return <div className="screener-boolean-field" key={field.key}><label><input type="checkbox" checked={draft.override} onChange={(event) => setBooleans((current) => ({ ...current, [field.key]: { ...draft, override: event.target.checked } }))} /><span>Override default</span></label><label className={!draft.override ? "boolean-value-disabled" : ""}><input type="checkbox" checked={draft.value} disabled={!draft.override} onChange={(event) => setBooleans((current) => ({ ...current, [field.key]: { ...draft, value: event.target.checked } }))} /><span>{field.label}</span></label><small>{draft.override ? "Explicit override" : "Worker default is preserved"}</small></div>; })}</div>
      <div className="screener-control-footer"><span className="panel-counter">{control.tunable_fields.length + " TUNABLE FIELDS"}</span><button className="button button-primary" onClick={() => void handleSave()} disabled={saving || loading}><Save size={15} /> {saving ? "Saving…" : "Save changes"}</button></div>
    </> : <p className="empty-state">Settings are unavailable. Reload after the screener has initialized its control table.</p>}
  </section>;
}
