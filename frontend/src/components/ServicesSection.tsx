import { useCallback, useEffect, useState } from "react";
import { Power, RefreshCw } from "lucide-react";
import { fetchScreenerControl, fetchScreenerStatus, saveScreenerControl } from "../lib/screener";
import { viewPath } from "../lib/workspaceNavigation";
import { fetchServices, setServiceEnabled, type ServiceState } from "../lib/services";

const serviceLaunchFlags: Record<string, string> = {
  paper_engine: "PAPER_ENGINE_ENABLED=1",
  news_ai: "NEWS_AI_ENABLED=1",
  screener: "The default run_all.py worker set includes the Screener",
  telegram_outbox: "RETAILBOT2_OUTBOX_WORKER_ENABLED=1 and TELEGRAM_DELIVERY_ENABLED=1",
  paper_report: "PAPER_REPORT_ENABLED=1 (requires the Telegram outbox worker)",
};

function heartbeatAgeSeconds(timestamp: string) {
  const normalized = timestamp.includes("T") ? timestamp : timestamp.replace(" ", "T") + "Z";
  return Math.max(0, Math.floor((Date.now() - Date.parse(normalized)) / 1000));
}

/** Admin-only runtime pause/resume for background workers (no restart, no .env edit). */
export function ServicesSection({ isAdmin }: { isAdmin: boolean }) {
  const [services, setServices] = useState<ServiceState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [screenerError, setScreenerError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [rows, screener] = await Promise.all([
        fetchServices(signal),
        fetchScreenerStatus(signal).then(
          (status) => ({ status, error: null }),
          () => ({ status: null, error: "Screener status unavailable. Refresh to retry." }),
        ),
      ]);
      if (signal?.aborted) return;
      setScreenerError(screener.error);
      if (!screener.status) {
        setServices(rows);
        return;
      }
      setServices([...rows, {
        service: "screener",
        label: "Screener",
        description: "Scans markets for smart-money signals.",
        pauseWarning: "While paused, the Screener stops generating signals.",
        enabled: screener.status.enabled,
        updatedBy: null,
        updatedAt: null,
        staleAfterSeconds: 180,
        heartbeats: [{ name: "screener", status: null, detail: null, lastBeat: screener.status.last_beat_at }],
      }]);
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "Failed to load services");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [isAdmin, load]);

  const apply = async (service: string, enabled: boolean) => {
    setBusy(service);
    setError(null);
    try {
      if (service === "screener") {
        const control = await fetchScreenerControl();
        await saveScreenerControl(enabled, control.overrides);
      } else {
        await setServiceEnabled(service, enabled);
      }
      setConfirming(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to update service");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel" aria-labelledby="settings-services-title" aria-busy={loading}>
      <div className="panel-heading">
        <div>
          <h2 id="settings-services-title">
            <Power size={17} className="inline-icon" /> Background Services
          </h2>
          <p className="panel-subtitle">
            Pause or resume workers without editing .env or restarting. Takes effect on the worker's next cycle.
          </p>
        </div>
        <button type="button" className="button button-secondary" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      </div>

      {error && <div className="api-error" role="alert" style={{ marginBottom: 12 }}>{error}</div>}
      {screenerError && <p className="control-feedback control-error">{screenerError}</p>}
      {loading && services.length === 0 ? (
        <p className="empty-state" role="status">Loading services…</p>
      ) : (
        <div className="status-table-wrap">
          <table className="status-table">
            <thead>
              <tr><th>Service</th><th>State</th><th>Last heartbeat</th><th>Action</th></tr>
            </thead>
            <tbody>
              {services.map((svc) => {
                const latest = svc.heartbeats
                  .map((h) => h.lastBeat)
                  .filter((v): v is string => Boolean(v))
                  .sort()
                  .at(-1);
                const launchFlag = serviceLaunchFlags[svc.service];
                const awaitingHeartbeat = Boolean(launchFlag && !latest);
                const age = latest ? heartbeatAgeSeconds(latest) : NaN;
                const stale = svc.staleAfterSeconds != null && age > svc.staleAfterSeconds;
                return (
                  <tr key={svc.service}>
                    <td>
                      <strong>{svc.label}</strong>
                      <div className="panel-subtitle">{svc.description}</div>
                      {svc.service === "screener" && <a href={viewPath("Market Screener") + "#screener-controls-title"}>Screener thresholds</a>}
                      {svc.updatedBy && <div className="panel-subtitle">Last changed by {svc.updatedBy}</div>}
                    </td>
                    <td>
                      <span className={svc.enabled && !awaitingHeartbeat && !stale ? "badge badge-ok" : "badge badge-warn"}>
                        {!svc.enabled ? "Paused" : awaitingHeartbeat ? "No heartbeat" : stale ? "Stale heartbeat" : "Enabled"}
                      </span>
                    </td>
                    <td>{latest ?? "—"}{Number.isFinite(age) && <div className="panel-subtitle">{age.toLocaleString()}s ago</div>}</td>
                    <td>
                      {confirming === svc.service ? (
                        <div role="group" aria-label={`Confirm pausing ${svc.label}`}>
                          <p className="api-error" role="alert">{svc.pauseWarning}</p>
                          <button
                            type="button"
                            className="button button-danger"
                            disabled={busy === svc.service}
                            onClick={() => void apply(svc.service, false)}
                          >
                            Confirm pause {svc.label}
                          </button>{" "}
                          <button type="button" className="button button-secondary" onClick={() => setConfirming(null)}>
                            Cancel
                          </button>
                        </div>
                      ) : svc.enabled ? (
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={busy === svc.service}
                          onClick={() => setConfirming(svc.service)}
                        >
                          Pause {svc.label}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="button button-primary"
                          disabled={busy === svc.service}
                          onClick={() => void apply(svc.service, true)}
                        >
                          Resume {svc.label}
                        </button>
                      )}
                      {awaitingHeartbeat && (
                        <div className="panel-subtitle">Check the worker process and its launcher setting <code>{launchFlag}</code>.</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
