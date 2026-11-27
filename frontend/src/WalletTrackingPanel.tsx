import { useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, CheckCircle, Plus, RefreshCw, Settings, ShieldCheck, Wallet, X } from "lucide-react";
import { useAuth } from "./AuthGate";
import { ApiError } from "./lib/api";
import { trapFocus } from "./lib/dialog";
import {
  fetchWalletTrackingSnapshot,
  fetchWalletPerformance,
  setWalletTelegram,
  toggleWalletTracking,
  type WalletPerformance,
  saveWalletSettings,
  trackWallet,
  untrackWallet,
  updateWalletLabel,
  WalletActionError,
  type TrackedWallet,
  type WalletTrackingSnapshot,
} from "./lib/walletTracking";

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
  const auth = useAuth();
  const canTrade = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());

  const [data, setData] = useState<WalletTrackingSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [period, setPeriod] = useState(24);
  const [metric, setMetric] = useState("pnl_usd");
  const [ranking, setRanking] = useState<WalletPerformance[]>([]);
  const [rankingError, setRankingError] = useState(false);
  const [rankingBusy, setRankingBusy] = useState(true);
  const [changingAlert, setChangingAlert] = useState(false);
  const [togglingWalletId, setTogglingWalletId] = useState<number | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setRankingBusy(true);
    setRankingError(false);
    fetchWalletPerformance(period, metric, controller.signal)
      .then(result => { if (!controller.signal.aborted) setRanking(result.wallets); })
      .catch(() => { if (!controller.signal.aborted) setRankingError(true); })
      .finally(() => { if (!controller.signal.aborted) setRankingBusy(false); });
    return () => controller.abort();
  }, [period, metric, attempt]);

  // Feedback notifications
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // WT-04: Track wallet modal
  const [trackOpen, setTrackOpen] = useState(false);
  const [trackBusy, setTrackBusy] = useState(false);
  const [trackError, setTrackError] = useState<string | null>(null);
  const [trackChain, setTrackChain] = useState("solana");
  const [trackAddress, setTrackAddress] = useState("");
  const [trackLabel, setTrackLabel] = useState("");
  const trackDialogRef = useRef<HTMLDialogElement | null>(null);
  const trackTriggerRef = useRef<HTMLButtonElement | null>(null);

  // WT-04: Untrack wallet modal
  const [labelEdit, setLabelEdit] = useState<{ id: number; text: string } | null>(null);
  const [savingLabel, setSavingLabel] = useState(false);
  const [untrackTarget, setUntrackTarget] = useState<TrackedWallet | null>(null);
  const [untrackBusy, setUntrackBusy] = useState(false);
  const [untrackError, setUntrackError] = useState<string | null>(null);
  const untrackDialogRef = useRef<HTMLDialogElement | null>(null);
  const untrackTriggerRef = useRef<HTMLButtonElement | null>(null);

  // WT-05: Settings modal
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [sEnabled, setSEnabled] = useState(false);
  const [sNetworks, setSNetworks] = useState<string[]>([]);
  const [sMinTrade, setSMinTrade] = useState<number | string>(10000);
  const [sMinNet, setSMinNet] = useState<number | string>(150000);
  const [sAutoTrackNet, setSAutoTrackNet] = useState<number | string>(500000);
  const [sPollInterval, setSPollInterval] = useState<number | string>(300);
  const [sBirdeyeKey, setSBirdeyeKey] = useState("");
  const [sClearBirdeyeKey, setSClearBirdeyeKey] = useState(false);
  const settingsDialogRef = useRef<HTMLDialogElement | null>(null);
  const settingsTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetchWalletTrackingSnapshot(controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setData(result);
        const chains = Object.keys(result.meta.chains);
        if (chains.length > 0 && !chains.includes(trackChain)) {
          setTrackChain(chains[0]);
        }
      }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attempt]);

  // Modal lifecycle: track
  useEffect(() => {
    const dialog = trackDialogRef.current;
    if (trackOpen && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [trackOpen]);

  // Modal lifecycle: untrack
  useEffect(() => {
    const dialog = untrackDialogRef.current;
    if (untrackTarget && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [untrackTarget]);

  // Modal lifecycle: settings
  useEffect(() => {
    const dialog = settingsDialogRef.current;
    if (settingsOpen && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [settingsOpen]);

  // Open / Close handlers
  const openTrackModal = () => {
    setTrackError(null);
    setTrackAddress("");
    setTrackLabel("");
    setTrackOpen(true);
  };
  const closeTrackModal = () => {
    const dialog = trackDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    setTrackOpen(false);
    setTrackError(null);
    trackTriggerRef.current?.focus();
  };

  const openUntrackModal = (wallet: TrackedWallet, trigger: HTMLButtonElement) => {
    untrackTriggerRef.current = trigger;
    setUntrackTarget(wallet);
    setUntrackError(null);
  };
  const closeUntrackModal = () => {
    const dialog = untrackDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    setUntrackTarget(null);
    setUntrackError(null);
    untrackTriggerRef.current?.focus();
  };

  const openSettingsModal = () => {
    if (data) {
      setSEnabled(data.settings.enabled);
      setSNetworks([...data.settings.networks]);
      setSMinTrade(data.settings.min_trade_usd);
      setSMinNet(data.settings.min_net_usd);
      setSAutoTrackNet(data.settings.auto_track_net_usd);
      setSPollInterval(data.settings.poll_interval_seconds);
      setSBirdeyeKey("");
      setSClearBirdeyeKey(false);
    }
    setSettingsError(null);
    setSettingsOpen(true);
  };
  const closeSettingsModal = () => {
    const dialog = settingsDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    setSettingsOpen(false);
    setSettingsError(null);
    settingsTriggerRef.current?.focus();
  };

  // WT-04: Execute track wallet
  const handleExecuteTrack = async (e: React.FormEvent) => {
    e.preventDefault();
    if (trackBusy) return;
    setTrackError(null);
    setActionError(null);
    setActionSuccess(null);

    const addr = trackAddress.trim();
    if (!addr) {
      setTrackError("Wallet address is required.");
      return;
    }

    setTrackBusy(true);
    try {
      await trackWallet(trackChain, addr, trackLabel.trim() || null);
      setActionSuccess(`Wallet '${shorten(addr)}' (${trackLabel || "unlabelled"}) added to tracked wallets on ${trackChain}.`);
      closeTrackModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setTrackError(cause instanceof WalletActionError ? cause.message : "Failed to track wallet address.");
    } finally {
      setTrackBusy(false);
    }
  };

  // WT-04: Execute untrack wallet
  const handleExecuteUntrack = async () => {
    if (!untrackTarget || untrackBusy) return;
    setUntrackBusy(true);
    setUntrackError(null);
    setActionError(null);
    setActionSuccess(null);

    try {
      await untrackWallet(untrackTarget.id);
      setActionSuccess(`Wallet '${shorten(untrackTarget.wallet)}' removed from tracked wallets.`);
      closeUntrackModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setUntrackError(cause instanceof WalletActionError ? cause.message : "Failed to untrack wallet.");
    } finally {
      setUntrackBusy(false);
    }
  };

  // WT-05: Execute save settings
  const handleExecuteSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (settingsBusy || !data) return;
    setSettingsError(null);
    setActionError(null);
    setActionSuccess(null);

    if (sEnabled && sNetworks.length === 0) {
      setSettingsError("Select at least one network to monitor when the tracker is enabled.");
      return;
    }
    const minTrade = typeof sMinTrade === "number" ? sMinTrade : parseFloat(sMinTrade);
    const minNet = typeof sMinNet === "number" ? sMinNet : parseFloat(sMinNet);
    const autoTrackNet = typeof sAutoTrackNet === "number" ? sAutoTrackNet : parseFloat(sAutoTrackNet);
    const pollInterval = typeof sPollInterval === "number" ? sPollInterval : parseInt(sPollInterval, 10);

    if (!Number.isFinite(minTrade) || minTrade <= 0) {
      setSettingsError("Minimum trade size must be greater than zero.");
      return;
    }
    if (!Number.isFinite(minNet) || minNet <= 0) {
      setSettingsError("Minimum net flow must be greater than zero.");
      return;
    }
    if (!Number.isFinite(autoTrackNet) || autoTrackNet < minNet) {
      setSettingsError("Auto-track threshold must be greater than or equal to minimum net flow.");
      return;
    }
    if (!Number.isInteger(pollInterval) || pollInterval < 15 || pollInterval > 3600) {
      setSettingsError("Polling interval must be between 15 and 3600 seconds.");
      return;
    }

    setSettingsBusy(true);
    try {
      const payload: Parameters<typeof saveWalletSettings>[0] = {
        enabled: sEnabled,
        networks: sNetworks,
        min_trade_usd: minTrade,
        min_net_usd: minNet,
        auto_track_net_usd: autoTrackNet,
        poll_interval_seconds: pollInterval,
        pools_per_network: data.settings.pools_per_network,
        retention_days: data.settings.retention_days,
        api_delay_seconds: data.settings.api_delay_seconds,
        token_mode: data.settings.token_mode,
        token_addresses: data.settings.token_addresses,
      };
      if (sClearBirdeyeKey) {
        payload.clear_birdeye_api_key = true;
      } else if (sBirdeyeKey.trim().length > 0) {
        payload.birdeye_api_key = sBirdeyeKey.trim();
      }
      await saveWalletSettings(payload);
      setActionSuccess("Wallet tracker settings updated successfully.");
      closeSettingsModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setSettingsError(cause instanceof WalletActionError ? cause.message : "Failed to save wallet settings.");
    } finally {
      setSettingsBusy(false);
    }
  };

  const status = data?.status;
  const chainOptions = data ? Object.entries(data.meta.chains) : [];

  return (
    <div className="wallet-tracking-view" aria-busy={loading}>
      <section className="panel paper-safety-banner">
        <ShieldCheck size={19} />
        <div>
          <strong>{canTrade ? "Smart Money Wallet Tracker" : "Read-only Wallet Tracking monitor"}</strong>
          <p>
            Displays tracker health, monitored addresses, token flow, and recent DEX events.
            {canTrade
              ? " Authorized operators can configure alert thresholds and add/remove monitored addresses."
              : " Read-only view for observing wallet tracking. Operator permissions required to modify settings."}
          </p>
        </div>
        <span className="panel-counter">TRACKER</span>
      </section>

      {actionSuccess && (
        <div className="api-success" role="status" aria-live="polite" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <CheckCircle size={17} />
            <span>{actionSuccess}</span>
          </div>
          <button type="button" className="icon-button" aria-label="Dismiss message" onClick={() => setActionSuccess(null)}>
            <X size={15} />
          </button>
        </div>
      )}

      {actionError && (
        <div className="api-error" role="alert" aria-live="assertive" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <AlertTriangle size={17} />
            <span>{actionError}</span>
          </div>
          <button type="button" className="icon-button" aria-label="Dismiss error" onClick={() => setActionError(null)}>
            <X size={15} />
          </button>
        </div>
      )}

      {error && (
        <div className="api-error" role="alert">
          <div>
            <strong>Unable to load Wallet Tracking</strong>
            <p>{error}</p>
          </div>
          <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      )}

      <section className="metrics-grid" aria-label="Wallet tracking summary">
        <article className="metric-card"><div className="metric-top"><span>Tracker status</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : status?.last_cycle_at ? "Cycle observed" : status ? "No cycle yet" : "—"}</div><div className="metric-foot"><span>{status?.enabled ? "Enabled" : status ? "Disabled" : "Health unavailable"}</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Monitored wallets</span><span className="metric-icon"><Wallet size={17} /></span></div><div className="metric-value">{data ? data.tracked.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Active tracked addresses</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Token positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{data ? data.positions.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Past 24 hours · top 20</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Recent events</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{data ? data.events.length : loading ? "Loading…" : "—"}</div><div className="metric-foot"><span>Latest DEX swaps returned</span></div></article>
      </section>

      {/* Tracker Health & WT-05 Settings */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Tracker health and configuration</h2>
            <p>Scanning status and network thresholds for on-chain DEX monitoring.</p>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            {canTrade && data && (
              <button
                ref={settingsTriggerRef}
                type="button"
                className="button button-primary"
                onClick={openSettingsModal}
              >
                <Settings size={15} /> Edit settings
              </button>
            )}
            <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}>
              <RefreshCw size={15} /> Refresh
            </button>
          </div>
        </div>

        {data ? (
          <div className="paper-risk-grid">
            <div><span>Tracker state</span><strong>{data.settings.enabled ? "Active" : "Paused"}</strong></div>
            <div><span>Last scan</span><strong>{dateLabel(status?.last_cycle_at ?? null)}</strong></div>
            <div><span>Enabled networks</span><strong>{data.settings.networks.map((chain) => data.meta.chains[chain] ?? chain).join(", ") || "None"}</strong></div>
            <div><span>Minimum trade</span><strong>{money.format(data.settings.min_trade_usd)}</strong></div>
            <div><span>Minimum net flow</span><strong>{money.format(data.settings.min_net_usd)}</strong></div>
            <div><span>Auto-track threshold</span><strong>{money.format(data.settings.auto_track_net_usd)}</strong></div>
            <div><span>Polling interval</span><strong>{data.settings.poll_interval_seconds}s</strong></div>
            <div><span>Birdeye fallback</span><strong>{status?.birdeye_fallback_active ? "Configured" : "Not configured"}</strong></div>
          </div>
        ) : (
          <p className="empty-state" role="status">{loading ? "Loading tracker status…" : "Tracker status unavailable."}</p>
        )}
      </section>

      <section className="panel">
        <h2>Wallet realized P&amp;L</h2>
        <p>Estimated gross return on sales matched to earlier observed buys in the same pool and period.
          Open holdings, unmatched quantities, gas and separate fees are excluded.</p>
        <label>Period <select value={period} onChange={e => setPeriod(Number(e.target.value))}>
          {(data?.meta.periods_hours ?? [24]).map(h => <option key={h} value={h}>{h} hours</option>)}
        </select></label>{" "}
        <label>Rank by <select value={metric} onChange={e => setMetric(e.target.value)}>
          <option value="pnl_usd">Realized profit (USD)</option><option value="roi_pct">Return on matched cost (%)</option>
        </select></label>
        {rankingBusy ? <p role="status">Loading returns…</p> : rankingError ? <p role="alert">Wallet returns could not be loaded. Use Refresh to retry.</p> :
          ranking.length === 0 ? <p>No sales with an observed cost basis in this period.</p> :
          <div className="table-scroll"><table><thead><tr>
            <th>Wallet</th><th>Observed pairs</th><th>Matched cost</th><th>Realized P&amp;L</th><th>Return</th><th>Matched sells</th><th>Monitoring</th>
          </tr></thead><tbody>{ranking.map(w => <tr key={w.chain + ":" + w.wallet}>
            <td>{w.chain} <code title={w.wallet}>{shorten(w.wallet)}</code></td><td>{w.pairs.join(", ")}</td>
            <td>${w.cost_usd.toFixed(2)}</td><td>${w.pnl_usd.toFixed(2)}</td><td>{w.roi_pct.toFixed(2)}%</td><td>{w.matched_sells}</td>
            <td>{data?.tracked.some(t => t.chain === w.chain && t.wallet === w.wallet) ? "Tracked" :
              canTrade && <button className="button button-small" onClick={() => {
                openTrackModal(); setTrackChain(w.chain); setTrackAddress(w.wallet);
              }}>Track wallet</button>}</td>
          </tr>)}</tbody></table></div>}
      </section>

      {/* Tracked Wallets & WT-04 Add/Untrack */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Tracked wallets</h2>
            <p>Active wallet addresses configured for monitoring.</p>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <span className="panel-counter">{data?.tracked.length ?? "—"} WALLETS</span>
            {canTrade && data && (
              <button
                ref={trackTriggerRef}
                type="button"
                className="button button-primary"
                onClick={openTrackModal}
              >
                <Plus size={15} /> Track wallet
              </button>
            )}
          </div>
        </div>

        {loading && !data ? (
          <p className="empty-state" role="status">Loading tracked wallets…</p>
        ) : data?.tracked.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Wallet / label</th>
                  <th scope="col">Chain</th>
                  <th scope="col">Status</th>
                  <th scope="col">Added</th>
                  <th scope="col">Telegram swap alerts</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {data.tracked.map((wallet) => (
                  <tr key={wallet.id}>
                    <td>
                      {canTrade && labelEdit?.id === wallet.id ? <form onSubmit={async (event) => {
                        event.preventDefault();
                        if (savingLabel) return;
                        setSavingLabel(true);
                        setActionError(null);
                        setActionSuccess(null);
                        try {
                          await updateWalletLabel(wallet.id, labelEdit.text);
                          setLabelEdit(null);
                          setActionSuccess("Wallet label updated.");
                          setAttempt((value) => value + 1);
                        } catch {
                          setActionError("Could not update the wallet label.");
                        } finally {
                          setSavingLabel(false);
                        }
                      }}>
                        <input aria-label="Wallet label" maxLength={80} value={labelEdit.text} disabled={savingLabel}
                          onChange={(event) => setLabelEdit({ id: wallet.id, text: event.target.value })} />
                        <button type="submit" className="button button-small" disabled={savingLabel}>Save label</button>
                        <button type="button" className="button button-small" disabled={savingLabel}
                          onClick={() => setLabelEdit(null)}>Cancel</button>
                      </form> : <>
                        <strong>{wallet.label || "Unlabelled wallet"}</strong>
                        {canTrade && <button type="button" className="button button-small" disabled={savingLabel}
                          onClick={() => setLabelEdit({ id: wallet.id, text: wallet.label ?? "" })}>Edit label</button>}
                      </>}
                      <div><code>{shorten(wallet.wallet)}</code></div>
                    </td>
                    <td>{data.meta.chains[wallet.chain] ?? wallet.chain}</td>
                    <td>{wallet.status}</td>
                    <td>{dateLabel(wallet.first_flagged_at ?? null)}</td>
                    <td>{canTrade ? <button className="button button-small" disabled={changingAlert || loading}
                      aria-pressed={wallet.telegram_after_id != null}
                      onClick={async () => {
                        const enabled = wallet.telegram_after_id == null;
                        setChangingAlert(true); setActionError(null); setActionSuccess(null);
                        try {
                          await setWalletTelegram(wallet.id, enabled);
                          setActionSuccess(`Telegram alerts ${enabled ? "selected" : "disabled"} for ${shorten(wallet.wallet)}.`);
                          setAttempt(n => n + 1);
                        } catch { setActionError("Could not update Telegram alerts."); }
                        finally { setChangingAlert(false); }
                      }}>{wallet.telegram_after_id == null ? "Enable Telegram" : "Disable Telegram"}</button> :
                      wallet.telegram_after_id == null ? "Off" : "Selected"}</td>
                    <td>
                      {canTrade ? (
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                          <button
                            type="button"
                            className="button button-secondary button-small"
                            disabled={togglingWalletId !== null || loading}
                            onClick={async () => {
                              setTogglingWalletId(wallet.id);
                              setActionError(null);
                              setActionSuccess(null);
                              try {
                                const res = await toggleWalletTracking(wallet.chain, wallet.wallet, wallet.label ?? null);
                                setActionSuccess(
                                  `Tracking ${res.tracked ? "enabled" : "disabled"} for ${shorten(wallet.wallet)}.`,
                                );
                                setAttempt((n) => n + 1);
                              } catch (cause) {
                                setActionError(
                                  cause instanceof WalletActionError ? cause.message : "Could not toggle wallet tracking.",
                                );
                              } finally {
                                setTogglingWalletId(null);
                              }
                            }}
                          >
                            {togglingWalletId === wallet.id
                              ? "Updating…"
                              : wallet.status.toUpperCase() === "ACTIVE"
                              ? "Disable tracking"
                              : "Enable tracking"}
                          </button>
                          <button
                            type="button"
                            className="button button-danger button-small"
                            onClick={(e) => openUntrackModal(wallet, e.currentTarget)}
                          >
                            Untrack
                          </button>
                        </div>
                      ) : (
                        <span className="text-muted text-sm">View only</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">No active tracked wallets were returned.</p>
        )}
      </section>

      {/* Token Net Flow */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Token net flow</h2>
            <p>Top token flows across the last 24 hours.</p>
          </div>
          <span className="panel-counter">{data?.flows.length ?? "—"} TOKENS</span>
        </div>
        {loading && !data ? (
          <p className="empty-state" role="status">Loading token flow…</p>
        ) : data?.flows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Pair</th>
                  <th scope="col">Chain</th>
                  <th scope="col">Buy USD</th>
                  <th scope="col">Sell USD</th>
                  <th scope="col">Net flow</th>
                </tr>
              </thead>
              <tbody>
                {data.flows.map((flow, index) => (
                  <tr key={flow.chain + flow.pair + index}>
                    <td><strong>{flow.pair}</strong></td>
                    <td>{flow.chain_label ?? data.meta.chains[flow.chain] ?? flow.chain}</td>
                    <td>{money.format(flow.buy_usd)}</td>
                    <td>{money.format(flow.sell_usd)}</td>
                    <td className={flow.net_usd > 0 ? "paper-positive" : flow.net_usd < 0 ? "paper-negative" : ""}>
                      {money.format(flow.net_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">No token flow data for the selected 24-hour window.</p>
        )}
      </section>

      {/* Recent On-chain Events */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Recent on-chain events</h2>
            <p>Most recent DEX swap events from the tracker API.</p>
          </div>
          <span className="panel-counter">{data?.events.length ?? "—"} EVENTS</span>
        </div>
        {loading && !data ? (
          <p className="empty-state" role="status">Loading recent events…</p>
        ) : data?.events.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Chain</th>
                  <th scope="col">Wallet</th>
                  <th scope="col">Pair</th>
                  <th scope="col">Side</th>
                  <th scope="col">USD value</th>
                  <th scope="col">Transaction</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((event) => (
                  <tr key={event.id}>
                    <td>{dateLabel(event.block_time)}</td>
                    <td>{data.meta.chains[event.chain] ?? event.chain}</td>
                    <td><code>{shorten(event.wallet)}</code></td>
                    <td>{event.pair}</td>
                    <td>{event.side}</td>
                    <td>{money.format(event.usd_value)}</td>
                    <td><code>{shorten(event.tx_hash)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">No recent on-chain events were returned.</p>
        )}
      </section>

      {/* WT-04 Modal: Track Wallet */}
      {trackOpen && (
        <dialog
          ref={trackDialogRef}
          className="app-dialog"
          aria-labelledby="track-wallet-title"
          aria-describedby="track-wallet-desc"
          onCancel={(e) => { e.preventDefault(); closeTrackModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeTrackModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="track-wallet-title">Track New Wallet</h3>
              <p id="track-wallet-desc">
                Add an on-chain wallet address to monitor for DEX swap events and net token accumulation.
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeTrackModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <form onSubmit={handleExecuteTrack}>
            <div style={{ marginBottom: "14px" }}>
              <label htmlFor="w-chain" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Chain / Network</label>
              <select
                id="w-chain"
                value={trackChain}
                onChange={(e) => setTrackChain(e.target.value)}
                disabled={trackBusy}
                style={{ width: "100%", padding: "6px 8px" }}
              >
                {chainOptions.map(([key, label]) => (
                  <option key={key} value={key}>{label} ({key})</option>
                ))}
              </select>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label htmlFor="w-address" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Wallet Address</label>
              <input
                id="w-address"
                type="text"
                required
                value={trackAddress}
                onChange={(e) => setTrackAddress(e.target.value)}
                placeholder="0x... or base58 address"
                disabled={trackBusy}
                style={{ width: "100%", padding: "6px 8px" }}
              />
            </div>

            <div style={{ marginBottom: "16px" }}>
              <label htmlFor="w-label" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Label (optional)</label>
              <input
                id="w-label"
                type="text"
                value={trackLabel}
                onChange={(e) => setTrackLabel(e.target.value)}
                placeholder="e.g. Whale #1, Smart Money Alpha"
                disabled={trackBusy}
                style={{ width: "100%", padding: "6px 8px" }}
              />
            </div>

            {trackError && (
              <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
                <div><p>{trackError}</p></div>
              </div>
            )}

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button type="button" className="button button-secondary" onClick={closeTrackModal} disabled={trackBusy}>
                Cancel
              </button>
              <button type="submit" className="button button-primary" disabled={trackBusy}>
                {trackBusy ? "Adding…" : "Start Tracking"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {/* WT-04 Modal: Untrack Wallet Confirmation */}
      {untrackTarget && (
        <dialog
          ref={untrackDialogRef}
          className="app-dialog"
          aria-labelledby="untrack-wallet-title"
          aria-describedby="untrack-wallet-desc"
          onCancel={(e) => { e.preventDefault(); closeUntrackModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeUntrackModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="untrack-wallet-title">Untrack Wallet</h3>
              <p id="untrack-wallet-desc">
                Are you sure you want to stop tracking wallet <strong>{untrackTarget.label || "unlabelled"}</strong>?
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeUntrackModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <div style={{ background: "var(--color-elevated)", padding: "12px", borderRadius: "8px", marginBottom: "16px", fontSize: "0.85rem" }}>
            <div><strong>Address:</strong> <code>{untrackTarget.wallet}</code></div>
            <div><strong>Chain:</strong> {data?.meta.chains[untrackTarget.chain] ?? untrackTarget.chain}</div>
            <div><strong>Status:</strong> {untrackTarget.status}</div>
          </div>

          {untrackError && (
            <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
              <div><p>{untrackError}</p></div>
            </div>
          )}

          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
            <button type="button" className="button button-secondary" onClick={closeUntrackModal} disabled={untrackBusy}>
              Cancel
            </button>
            <button type="button" className="button button-danger" onClick={handleExecuteUntrack} disabled={untrackBusy}>
              {untrackBusy ? "Removing…" : "Untrack Wallet"}
            </button>
          </div>
        </dialog>
      )}

      {/* WT-05 Modal: Edit Wallet Tracker Settings */}
      {settingsOpen && (
        <dialog
          ref={settingsDialogRef}
          className="app-dialog"
          aria-labelledby="wallet-settings-title"
          aria-describedby="wallet-settings-desc"
          onCancel={(e) => { e.preventDefault(); closeSettingsModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeSettingsModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="wallet-settings-title">Wallet Tracker Configuration</h3>
              <p id="wallet-settings-desc">
                Adjust scanner activity, monitored networks, and volume thresholds for on-chain swaps.
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeSettingsModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <form onSubmit={handleExecuteSaveSettings}>
            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "flex", gap: "8px", alignItems: "center", cursor: "pointer", fontWeight: 600 }}>
                <input
                  type="checkbox"
                  checked={sEnabled}
                  disabled={settingsBusy}
                  onChange={(e) => setSEnabled(e.target.checked)}
                />
                <span>Enable wallet tracker background scanning</span>
              </label>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", marginBottom: "6px", fontWeight: 600 }}>
                Monitored Networks
              </label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
                {chainOptions.map(([chainKey, chainLabel]) => {
                  const checked = sNetworks.includes(chainKey);
                  return (
                    <label key={chainKey} style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.85rem", cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={settingsBusy}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSNetworks([...sNetworks, chainKey]);
                          } else {
                            setSNetworks(sNetworks.filter((k) => k !== chainKey));
                          }
                        }}
                      />
                      <span>{chainLabel}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }}>
              <div>
                <label htmlFor="s-min-trade" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Min Trade Size (USD)</label>
                <input
                  id="s-min-trade"
                  type="number"
                  min={0.01}
                  step="any"
                  required
                  value={sMinTrade}
                  onChange={(e) => setSMinTrade(e.target.value)}
                  disabled={settingsBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>

              <div>
                <label htmlFor="s-min-net" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Min Net Flow (USD)</label>
                <input
                  id="s-min-net"
                  type="number"
                  min={0.01}
                  step="any"
                  required
                  value={sMinNet}
                  onChange={(e) => setSMinNet(e.target.value)}
                  disabled={settingsBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>

              <div>
                <label htmlFor="s-auto-track" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Auto-Track Net Flow (USD)</label>
                <input
                  id="s-auto-track"
                  type="number"
                  min={typeof sMinNet === "number" ? sMinNet : (parseFloat(sMinNet) || 0.01)}
                  step="any"
                  required
                  value={sAutoTrackNet}
                  onChange={(e) => setSAutoTrackNet(e.target.value)}
                  disabled={settingsBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>

              <div>
                <label htmlFor="s-poll" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px" }}>Poll Interval (seconds)</label>
                <input
                  id="s-poll"
                  type="number"
                  min={15}
                  max={3600}
                  required
                  value={sPollInterval}
                  onChange={(e) => setSPollInterval(e.target.value)}
                  disabled={settingsBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                />
              </div>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label htmlFor="s-birdeye-key" style={{ display: "block", fontSize: "0.8rem", marginBottom: "4px", fontWeight: 600 }}>
                Birdeye API Key (Optional)
              </label>
              {data?.settings?.has_birdeye_key ? (
                <div style={{ marginBottom: "6px" }}>
                  <div style={{ fontSize: "0.8rem", color: "var(--color-muted, #888)", marginBottom: "4px" }}>
                    Configured key: <code>{data?.settings?.birdeye_api_key_masked}</code>
                  </div>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "0.85rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={sClearBirdeyeKey}
                      disabled={settingsBusy}
                      onChange={(e) => {
                        setSClearBirdeyeKey(e.target.checked);
                        if (e.target.checked) setSBirdeyeKey("");
                      }}
                    />
                    <span>Remove / clear Birdeye API key</span>
                  </label>
                </div>
              ) : (
                <p style={{ fontSize: "0.75rem", color: "var(--color-muted, #888)", margin: "0 0 6px 0" }}>
                  No Birdeye API key configured. DEX Screener fallback is used.
                </p>
              )}
              {!sClearBirdeyeKey && (
                <input
                  id="s-birdeye-key"
                  type="password"
                  placeholder={data?.settings?.has_birdeye_key ? "Leave blank to keep existing key" : "Enter Birdeye API key"}
                  value={sBirdeyeKey}
                  onChange={(e) => setSBirdeyeKey(e.target.value)}
                  disabled={settingsBusy}
                  style={{ width: "100%", padding: "6px 8px" }}
                  autoComplete="off"
                />
              )}
            </div>

            {settingsError && (
              <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
                <div><p>{settingsError}</p></div>
              </div>
            )}

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button type="button" className="button button-secondary" onClick={closeSettingsModal} disabled={settingsBusy}>
                Cancel
              </button>
              <button type="submit" className="button button-primary" disabled={settingsBusy}>
                {settingsBusy ? "Saving…" : "Save Settings"}
              </button>
            </div>
          </form>
        </dialog>
      )}
    </div>
  );
}
