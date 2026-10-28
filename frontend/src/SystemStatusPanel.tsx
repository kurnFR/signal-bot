import { useEffect, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchSystemStatus, type SystemStatusResponse } from "./lib/systemStatus";

export function SystemStatusPanel() {
  const [data, setData] = useState<SystemStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchSystemStatus(controller.signal).then(setData).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) setError("Your session has expired. Sign in again to view system status.");
      else if (cause instanceof ApiError && cause.status === 403) setError("Your account cannot access system status.");
      else setError("System status could not be loaded. Check the API connection and retry.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const display = (value: string | null | undefined) => loading && !data ? "Loading…" : value ?? "Unavailable";
  const good = (value: string | undefined) => ["ok", "online"].includes((value ?? "").toLowerCase());
  return <>
    <section className="metrics-grid" aria-label="Read-only system status" aria-busy={loading}>
      <article className="metric-card"><div className="metric-top"><span>Database</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{display(data?.database.status)}</div><div className="metric-foot"><span className={"status-pill " + (good(data?.database.status) ? "status-positive" : "status-warning")}>{good(data?.database.status) ? "Connected" : "Check status"}</span><span>{data ? Object.keys(data.database.tables).length + " tables" : "System status"}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Binance Spot API</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{display(data?.binance.spotApi)}</div><div className="metric-foot"><span className={"status-pill " + (good(data?.binance.spotApi) ? "status-positive" : "status-warning")}>{good(data?.binance.spotApi) ? "Online" : "Check status"}</span><span>Read-only check</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Binance Futures API</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{display(data?.binance.futuresApi)}</div><div className="metric-foot"><span className={"status-pill " + (good(data?.binance.futuresApi) ? "status-positive" : "status-warning")}>{good(data?.binance.futuresApi) ? "Online" : "Check status"}</span><span>{data?.binance.enableFuturesConfig ? "Futures enabled" : "Futures config off"}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Collector heartbeats</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.heartbeats.length ?? "Unavailable"}</div><div className="metric-foot"><span className="status-pill status-neutral">Read only</span><span>Heartbeat records</span></div></article>
    </section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load system status</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="panel" aria-labelledby="heartbeat-title">
      <div className="panel-heading"><div><h2 id="heartbeat-title">Collector health</h2><p>Latest heartbeat metadata from the existing read-only status API.</p></div><span className="panel-counter">{loading ? "LOADING" : data ? data.heartbeats.length + " RECORDS" : "NO DATA"}</span></div>
      {loading && !data ? <p className="empty-state" role="status">Loading collector status…</p> : data && data.heartbeats.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th scope="col">Collector</th><th scope="col">Status</th><th scope="col">Last heartbeat</th></tr></thead><tbody>{data.heartbeats.map((item, index) => <tr key={item.collector + "-" + index}><td>{item.collector}</td><td><span className={"status-pill " + (good(item.status) ? "status-positive" : "status-warning")}>{item.status}</span></td><td className="heartbeat-time">{display(item.lastBeat)}</td></tr>)}</tbody></table></div> : <p className="empty-state">{error ? "Collector data is unavailable until the API request succeeds." : "No collector heartbeat records were returned."}</p>}
    </section>
  </>;
}
