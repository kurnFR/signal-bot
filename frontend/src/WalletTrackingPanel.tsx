import { useEffect, useState } from "react";
import { Activity, RefreshCw, ShieldCheck, Wallet } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchWalletTrackingSnapshot, type WalletTrackingSnapshot } from "./lib/walletTracking";

const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
function dateLabel(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view wallet tracking.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view wallet tracking data.";
  return "Wallet tracking data could not be loaded. Check API and database health, then retry.";
}
function shorten(value: string | undefined) {
  if (!value) return "—";
  return value.length > 22 ? value.slice(0, 10) + "…" + value.slice(-8) : value;
}

export function WalletTrackingPanel() {
  const [data, setData] = useState<WalletTrackingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchWalletTrackingSnapshot(controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  const status = data?.status;
  return <div className="wallet-tracking-view" aria-busy={loading}>
    <section className="panel paper-safety-banner">
      <ShieldCheck size={19} />
      <div><strong>Read-only Wallet Tracking monitor</strong><p>Displays tracker health, monitored addresses, token flow, and recent DEX events. Settings and wallet monitoring controls are intentionally unavailable in this migration slice.</p></div>
      <span className="panel-counter">READ ONLY</span>
    </section>
    {error && <div className="api-error" role="alert"><div><strong>Unable to load Wallet Tracking</strong><p>{error}</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="metrics-grid" aria-label="Wallet tracking summary">
      <article className="metric-card"><div className="metric-top"><span>Tracker status</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : status?.last_cycle_at ? "Cycle observed" : status ? "No cycle yet" : "—"}</div><div className="metric-foot"><span>{status?.enabled ? "Enabled" : status ? "Disabled" : "Health unavailable"}</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Monitored wallets</span><span className="metric-icon"><Wallet size={17} /></span></div><div className="metric-value">{data ? data.tracked.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Active tracked addresses</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Token positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{data ? data.positions.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Past 24 hours · top 20</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Recent events</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{data ? data.events.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Latest DEX swaps returned</span></div></article>
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Tracker health and configuration</h2><p>Configuration is displayed from the server; this view cannot edit it.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      {data ? <div className="paper-risk-grid">
        <div><span>Last scan</span><strong>{dateLabel(status?.last_cycle_at ?? null)}</strong></div>
        <div><span>Enabled networks</span><strong>{data.settings.networks.map((chain) => data.meta.chains[chain] ?? chain).join(", ") || "None"}</strong></div>
        <div><span>Minimum trade</span><strong>{money.format(data.settings.min_trade_usd)}</strong></div>
        <div><span>Minimum net flow</span><strong>{money.format(data.settings.min_net_usd)}</strong></div>
        <div><span>Polling interval</span><strong>{data.settings.poll_interval_seconds}s</strong></div>
        <div><span>Birdeye fallback</span><strong>{status?.birdeye_fallback_active ? "Configured" : "Not configured"}</strong></div>
      </div> : <p className="empty-state" role="status">{loading ? "Loading tracker status…" : "Tracker status unavailable."}</p>}
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Tracked wallets</h2><p>Active wallet addresses configured for monitoring.</p></div><span className="panel-counter">{data?.tracked.length ?? "—"} WALLETS</span></div>
      {loading && !data ? <p className="empty-state" role="status">Loading tracked wallets…</p> : data?.tracked.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Wallet / label</th><th scope="col">Chain</th><th scope="col">Status</th><th scope="col">Added</th></tr></thead><tbody>{data.tracked.map((wallet) => <tr key={wallet.id}><td><strong>{wallet.label || "Unlabelled wallet"}</strong><div><code>{shorten(wallet.wallet)}</code></div></td><td>{data.meta.chains[wallet.chain] ?? wallet.chain}</td><td>{wallet.status}</td><td>{dateLabel(wallet.first_flagged_at ?? null)}</td></tr>)}</tbody></table></div> : <p className="empty-state">No active tracked wallets were returned.</p>}
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Token net flow</h2><p>Top token flows across the last 24 hours.</p></div><span className="panel-counter">{data?.flows.length ?? "—"} TOKENS</span></div>
      {loading && !data ? <p className="empty-state" role="status">Loading token flow…</p> : data?.flows.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Pair</th><th scope="col">Chain</th><th scope="col">Buy USD</th><th scope="col">Sell USD</th><th scope="col">Net flow</th></tr></thead><tbody>{data.flows.map((flow, index) => <tr key={flow.chain + flow.pair + index}><td><strong>{flow.pair}</strong></td><td>{flow.chain_label ?? data.meta.chains[flow.chain] ?? flow.chain}</td><td>{money.format(flow.buy_usd)}</td><td>{money.format(flow.sell_usd)}</td><td className={flow.net_usd > 0 ? "paper-positive" : flow.net_usd < 0 ? "paper-negative" : ""}>{money.format(flow.net_usd)}</td></tr>)}</tbody></table></div> : <p className="empty-state">No token flow data for the selected 24-hour window.</p>}
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Recent on-chain events</h2><p>Most recent DEX swap events from the tracker API.</p></div><span className="panel-counter">{data?.events.length ?? "—"} EVENTS</span></div>
      {loading && !data ? <p className="empty-state" role="status">Loading recent events…</p> : data?.events.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Time</th><th scope="col">Chain</th><th scope="col">Wallet</th><th scope="col">Pair</th><th scope="col">Side</th><th scope="col">USD value</th><th scope="col">Transaction</th></tr></thead><tbody>{data.events.map((event) => <tr key={event.id}><td>{dateLabel(event.block_time)}</td><td>{data.meta.chains[event.chain] ?? event.chain}</td><td><code>{shorten(event.wallet)}</code></td><td>{event.pair}</td><td>{event.side}</td><td>{money.format(event.usd_value)}</td><td><code>{shorten(event.tx_hash)}</code></td></tr>)}</tbody></table></div> : <p className="empty-state">No recent on-chain events were returned.</p>}
    </section>
    <p className="page-description">Scope note: only authenticated GET endpoints are used. Tracking/untracking wallets, changing settings, and triggering worker actions remain unavailable.</p>
  </div>;
}
