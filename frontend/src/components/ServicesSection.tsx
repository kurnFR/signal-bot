import { useCallback, useEffect, useState } from "react";
import { Power, RefreshCw } from "lucide-react";
import { fetchServices, setServiceEnabled, type ServiceState } from "../lib/services";

/** Admin-only runtime pause/resume for background workers (no restart, no .env edit). */
export function ServicesSection({ isAdmin }: { isAdmin: boolean }) {
  const [services, setServices] = useState<ServiceState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchServices(signal);
      if (!signal?.aborted) setServices(rows);
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
      await setServiceEnabled(service, enabled);
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
                return (
                  <tr key={svc.service}>
                    <td>
                      <strong>{svc.label}</strong>
                      <div className="panel-subtitle">{svc.description}</div>
                      {svc.updatedBy && <div className="panel-subtitle">Last changed by {svc.updatedBy}</div>}
                    </td>
                    <td>
                      <span className={svc.enabled ? "badge badge-ok" : "badge badge-warn"}>
                        {svc.enabled ? "Running" : "Paused"}
                      </span>
                    </td>
                    <td>{latest ?? "—"}</td>
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
