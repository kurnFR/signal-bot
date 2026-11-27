import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SlidersHorizontal } from "lucide-react";
import {
  NEWS_MAX_TRADES_CEILING, NEWS_MIN_CONFIDENCE_FLOOR, fetchNewsAISettings, resetNewsAISettings,
  updateNewsAISettings, type NewsAISettingKey, type NewsAISettings, type NewsAISettingsPatch,
} from "../lib/news";

const fields: ReadonlyArray<{ key: NewsAISettingKey; label: string; min: number; max: number; integer?: boolean }> = [
  { key: "min_confidence", label: "Minimum confidence", min: NEWS_MIN_CONFIDENCE_FLOOR, max: 100, integer: true },
  { key: "max_trades_per_day", label: "Max trades per day", min: 0, max: NEWS_MAX_TRADES_CEILING, integer: true },
  { key: "stop_atr_mult", label: "Stop loss (ATR multiple)", min: 0.5, max: 5 },
  { key: "tp_r_scalp", label: "Scalp take profit (R)", min: 0.5, max: 10 },
  { key: "tp_r_intraday", label: "Intraday take profit (R)", min: 0.5, max: 10 },
  { key: "tp_r_swing", label: "Swing take profit (R)", min: 0.5, max: 10 },
  { key: "poll_interval_seconds", label: "Strategy poll interval (seconds)", min: 30, max: 3600, integer: true },
  { key: "execution_poll_interval_seconds", label: "Execution poll interval (seconds)", min: 30, max: 3600, integer: true },
];

export function NewsAISettingsCard({ isAdmin }: { isAdmin: boolean }) {
  const [settings, setSettings] = useState<NewsAISettings | null>(null);
  const [draft, setDraft] = useState<Partial<Record<NewsAISettingKey, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await fetchNewsAISettings(signal);
      if (signal?.aborted) return;
      setSettings(next);
      setDraft({});
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Failed to load News AI settings");
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [isAdmin, load]);

  if (!isAdmin) return null;

  const apply = async (mutation: () => Promise<void>, message: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await mutation();
      setNotice(message);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to update News AI settings");
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const changes: NewsAISettingsPatch = {};
    for (const { key, label, min, max, integer } of fields) {
      const raw = draft[key];
      if (raw === undefined) continue;
      const value = Number(raw);
      if (!raw.trim() || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
        setError(`${label} must be a ${integer ? "whole number" : "number"} from ${min} to ${max}.`);
        return;
      }
      if (value !== settings?.[key]) changes[key] = value;
    }
    if (Object.keys(changes).length) {
      void apply(() => updateNewsAISettings(changes), "Settings saved. Workers read them on their next cycle.");
    }
  };

  return (
    <section className="panel" aria-labelledby="news-settings-title">
      <div className="panel-heading">
        <div>
          <h2 id="news-settings-title"><SlidersHorizontal size={17} className="inline-icon" /> News AI guardrails</h2>
          <p className="panel-subtitle">
            {settings?.updated_by && `Last changed by ${settings.updated_by}. `}
            Setting max trades to 0 blocks all news-driven paper trades.
          </p>
        </div>
      </div>
      {error && <div className="api-error" role="alert">{error}</div>}
      {notice && <p className="empty-state" role="status">{notice}</p>}
      <form onSubmit={submit} className="screener-control-grid" aria-label="News AI guardrails">
        {fields.filter(({ key }) => settings?.[key] !== undefined).map(({ key, label, min, max, integer }) => (
          <div className="screener-control-field" key={key}>
            <label htmlFor={`news-ai-${key}`}>{label} · {min}–{max}</label>
            <input id={`news-ai-${key}`} inputMode={integer ? "numeric" : "decimal"} disabled={busy}
              value={draft[key] ?? String(settings?.[key])}
              onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} />
            <small>Default {settings?.defaults[key]}</small>
            <button type="button" className="button button-secondary" disabled={busy || settings?.overrides?.[key] === undefined}
              onClick={() => void apply(() => updateNewsAISettings({ [key]: null }), `${label} reset to its default.`)}>
              Reset {label}
            </button>
          </div>
        ))}
        <div className="screener-control-field">
          <button type="submit" className="button button-primary" disabled={busy || !settings}>Save guardrails</button>{" "}
          <button type="button" className="button button-secondary" disabled={busy || settings?.source !== "override"}
            onClick={() => void apply(resetNewsAISettings, "Overrides removed. Server defaults are in effect.")}>
            Reset to defaults
          </button>
        </div>
      </form>
    </section>
  );
}
