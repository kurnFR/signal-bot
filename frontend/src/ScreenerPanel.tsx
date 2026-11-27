import { DataTable } from "./components/DataTable";
import { useEffect, useState } from "react";
import { Activity, RefreshCw, ShieldCheck, X } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchScreenerSnapshot, type ScreenerSnapshot, type ScreenerSignal } from "./lib/screener";
import { TradingChart } from "./components/TradingChart";

const compactNumber = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const priceNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 8 });
const dateTime = (value: string | null | undefined) => {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
};

export function ScreenerPanel() {
  const [snapshot, setSnapshot] = useState<ScreenerSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [selectedSignalForChart, setSelectedSignalForChart] = useState<ScreenerSignal | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchScreenerSnapshot(controller.signal).then(setSnapshot).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) setError("Sign in through the existing dashboard first.");
      else if (cause instanceof ApiError && cause.status === 403) setError("Your account cannot view screener data.");
      else setError("Screener data could not be loaded. Check the API and database status, then retry.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const status = snapshot?.status;
  const stat = snapshot?.stats;
  const pill = (ok: boolean) => "status-pill " + (ok ? "status-positive" : "status-warning");
  return <>
    <section className="metrics-grid screener-metrics" aria-label="Smart money screener status" aria-busy={loading}>
      <article className="metric-card"><div className="metric-top"><span>Worker process</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !snapshot ? "Loading…" : status?.running ? "Running" : "Offline"}</div><div className="metric-foot"><span className={pill(Boolean(status?.running))}>{status?.running ? "Heartbeat fresh" : "No fresh heartbeat"}</span><span>{status?.timeframe ?? "Timeframe —"}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Telegram alerts</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{loading && !snapshot ? "Loading…" : status?.enabled ? "Enabled" : "Paused"}</div><div className="metric-foot"><span className={pill(Boolean(status?.enabled))}>{status?.enabled ? "Gate open" : "Gate paused"}</span><span>Read only</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Symbols monitored</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{status?.symbols_monitored ?? (loading ? "Loading…" : "—")}</div><div className="metric-foot"><span className={pill(Boolean(status?.database_ok))}>{status?.database_ok ? "Database available" : "Database unavailable"}</span><span>Current universe</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Signals · 24 hours</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{stat?.signals_last_24h ?? (loading ? "Loading…" : "—")}</div><div className="metric-foot"><span className="status-pill status-neutral">{stat?.total_signals ?? "—"} total</span><span>Stored signal history</span></div></article>
    </section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Market Screener V2</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="panel" aria-labelledby="screener-signal-title">
      <div className="panel-heading"><div><h2 id="screener-signal-title">Recent smart-money signals</h2><p>Latest persisted signals returned by the existing read-only screener API.</p></div><button className="button button-secondary" onClick={() => setAttempt((value) => value + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      <DataTable rows={snapshot?.signals ?? []} rowKey={(row) => row.id}
        storageId="screener-signals" label="recent signals" searchLabel="Filter signals"
        searchText={(row) => `${row.symbol} ${row.signal_type}`}
        loading={loading} empty={error ? "Signal rows are unavailable until the API request succeeds." : "No recent smart-money signals were returned."}
        noMatch="No signals match this filter." className="status-table screener-table"
        columns={[
          { key: "symbol", label: "Symbol", value: (row) => row.symbol, className: "screener-symbol" },
          { key: "signal", label: "Signal", value: (row) => row.signal_type, filter: true },
          { key: "price", label: "Price", value: (row) => row.price, cell: (row) => priceNumber.format(row.price) },
          { key: "volume", label: "24h quote vol.", value: (row) => row.quote_volume, cell: (row) => "$" + compactNumber.format(row.quote_volume) },
          { key: "rvol", label: "RVOL", value: (row) => row.rvol, cell: (row) => row.rvol.toFixed(2) + "×" },
          { key: "velocity", label: "Velocity", value: (row) => row.volume_velocity, cell: (row) => row.volume_velocity.toFixed(2) + "×" },
          { key: "quality", label: "Quality", value: (row) => row.quality_score, cell: (row) => <span className={pill(row.quality_score >= 70)}>{Math.round(row.quality_score)}</span> },
          { key: "telegram", label: "Telegram", value: (row) => row.telegram_sent ? "Sent" : "Not sent", cell: (row) => <span className={pill(row.telegram_sent)}>{row.telegram_sent ? "Sent" : "Not sent"}</span> },
          { key: "detected", label: "Detected", value: (row) => row.detected_at, className: "heartbeat-time", cell: (row) => dateTime(row.detected_at) },
          { key: "chart", label: "Chart", required: true, cell: (row) => <button type="button" className="button button-secondary button-xs" onClick={() => setSelectedSignalForChart(row)}>Chart</button> },
        ]} />
      {selectedSignalForChart && (
        <dialog className="modal-dialog" open style={{ maxWidth: "800px", width: "95%" }}>
          <div className="panel-heading">
            <div>
              <h2>{selectedSignalForChart.symbol} Signal Chart</h2>
              <p>{selectedSignalForChart.signal_type} · Detected price: {priceNumber.format(selectedSignalForChart.price)} · RVOL: {Number(selectedSignalForChart.rvol).toFixed(2)}×</p>
            </div>
            <button type="button" className="icon-button" onClick={() => setSelectedSignalForChart(null)} aria-label="Close chart">
              <X size={16} />
            </button>
          </div>
          <div style={{ padding: "16px 0" }}>
            <TradingChart
              symbol={selectedSignalForChart.symbol}
              timeframe={status?.timeframe ?? "1h"}
              markPrice={selectedSignalForChart.price}
            />
          </div>
        </dialog>
      )}
      {status?.last_signal_at && <p className="screener-footnote">Last worker signal timestamp: {dateTime(status.last_signal_at)}. This is worker metadata, not a promise that an alert was delivered.</p>}
    </section>
    <p className="screener-safety-note"><ShieldCheck size={15} /> This migration preview is read-only. Editing alert gates and detection overrides remains in the existing production dashboard until migration parity and release approval are complete.</p>
  </>;
}
