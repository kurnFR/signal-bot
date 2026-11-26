import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SlidersHorizontal } from "lucide-react";
import {
  NEWS_MAX_TRADES_CEILING, NEWS_MIN_CONFIDENCE_FLOOR, fetchNewsAISettings, resetNewsAISettings,
  updateNewsAISettings, type NewsAISettings,
} from "../lib/news";

/** Admin-only guardrails for the News AI overlay. Overrides the env defaults at runtime. */
export function NewsAISettingsCard({ isAdmin }: { isAdmin: boolean }) {
  const [settings, setSettings] = useState<NewsAISettings | null>(null);
  const [minConfidence, setMinConfidence] = useState("");
  const [maxTrades, setMaxTrades] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await fetchNewsAISettings(signal);
      if (signal?.aborted) return;
      setSettings(next);
      setMinConfidence(String(next.min_confidence));
      setMaxTrades(String(next.max_trades_per_day));
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

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const mc = Number(minConfidence);
    const mt = Number(maxTrades);
    if (!Number.isInteger(mc) || mc < NEWS_MIN_CONFIDENCE_FLOOR || mc > 100) {
      setError(`Minimum confidence must be a whole number from ${NEWS_MIN_CONFIDENCE_FLOOR} to 100.`);
      return;
    }
    if (!Number.isInteger(mt) || mt < 0 || mt > NEWS_MAX_TRADES_CEILING) {
      setError(`Max trades per day must be a whole number from 0 to ${NEWS_MAX_TRADES_CEILING}.`);
      return;
    }
    setBusy(true); setError(null); setNotice(null);
    try {
      await updateNewsAISettings(mc, mt);
      setNotice("Guardrails saved. Workers pick them up within about 15 seconds.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to save settings");
    } finally { setBusy(false); }
  };

  const reset = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      await resetNewsAISettings();
      setNotice("Override removed; env defaults are in effect.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to reset settings");
    } finally { setBusy(false); }
  };

  return (
    <section className="panel" aria-labelledby="news-settings-title">
      <div className="panel-heading">
        <div>
          <h2 id="news-settings-title"><SlidersHorizontal size={17} className="inline-icon" /> News AI guardrails</h2>
          <p className="panel-subtitle">
            Runtime overrides for trade-taking signals. Source: <strong>{settings?.source === "override" ? "admin override" : "env defaults"}</strong>
            {settings?.updated_by ? ` · last changed by ${settings.updated_by}` : ""}. Setting max trades to 0 blocks all news-driven paper trades.
          </p>
        </div>
      </div>
      {error && <div className="api-error" role="alert">{error}</div>}
      {notice && <p className="empty-state" role="status">{notice}</p>}
      <form onSubmit={(e) => void submit(e)} className="screener-control-grid" aria-label="News AI guardrails">
        <div className="screener-control-field">
          <label htmlFor="news-min-conf">Minimum confidence ({NEWS_MIN_CONFIDENCE_FLOOR}–100)</label>
          <input id="news-min-conf" inputMode="numeric" value={minConfidence} onChange={(e) => setMinConfidence(e.target.value)} />
          {settings && <small>Default {settings.defaults.min_confidence}</small>}
        </div>
        <div className="screener-control-field">
          <label htmlFor="news-max-trades">Max trades per day (0–{NEWS_MAX_TRADES_CEILING})</label>
          <input id="news-max-trades" inputMode="numeric" value={maxTrades} onChange={(e) => setMaxTrades(e.target.value)} />
          {settings && <small>Default {settings.defaults.max_trades_per_day}</small>}
        </div>
        <div className="screener-control-field">
          <button type="submit" className="button button-primary" disabled={busy || !settings}>Save guardrails</button>{" "}
          <button type="button" className="button button-secondary" disabled={busy || settings?.source !== "override"} onClick={() => void reset()}>
            Reset to defaults
          </button>
        </div>
      </form>
    </section>
  );
}
