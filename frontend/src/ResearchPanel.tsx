import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, CheckCircle, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useAuth } from "./AuthGate";
import { ApiError } from "./lib/api";
import { trapFocus } from "./lib/dialog";
import {
  fetchResearchSnapshot,
  ResearchActionError,
  saveStrategyLifecycle,
  stopAndHideStrategy,
  type ResearchSnapshot,
  type ResearchStrategy,
} from "./lib/research";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
function value(val: unknown) {
  if (typeof val === "number" && Number.isFinite(val)) return numberFormat.format(val);
  if (typeof val === "string" || typeof val === "boolean") return String(val);
  return "—";
}
function dateLabel(val: string | null | undefined) {
  if (!val) return "—";
  const timestamp = Date.parse(val);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : val;
}
function errorMessage(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view Research data.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot view Research data.";
  return "Research data could not be loaded. Check API/database health, then retry.";
}

export function ResearchPanel({ historyOnly = false }: { historyOnly?: boolean } = {}) {
  const auth = useAuth();
  const canTrade = !historyOnly && ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());

  const [data, setData] = useState<ResearchSnapshot | null>(null);
  const [strategies, setStrategies] = useState<ResearchStrategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");

  // Feedback notifications
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // RB-03: Row-level saving state
  const [savingRowKey, setSavingRowKey] = useState<string | null>(null);

  // RB-04: Stop & hide modal state
  const [stopTargetRow, setStopTargetRow] = useState<ResearchStrategy | null>(null);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const stopDialogRef = useRef<HTMLDialogElement | null>(null);
  const stopTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchResearchSnapshot(controller.signal, !historyOnly)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
          setStrategies(result.strategies);
        }
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt, historyOnly]);

  // Modal lifecycle: stop & hide
  useEffect(() => {
    const dialog = stopDialogRef.current;
    if (stopTargetRow && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }
  }, [stopTargetRow]);

  const openStopModal = (row: ResearchStrategy, trigger: HTMLButtonElement) => {
    stopTriggerRef.current = trigger;
    setStopTargetRow(row);
    setStopError(null);
  };

  const closeStopModal = () => {
    const dialog = stopDialogRef.current;
    if (dialog?.open) {
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }
    setStopTargetRow(null);
    setStopError(null);
    stopTriggerRef.current?.focus();
  };

  // RB-03: Toggle strategy flag in local state
  const handleCheckboxChange = (
    strategyId: string,
    mode: string,
    field: "evaluation_enabled" | "paper_enabled" | "telegram_enabled" | "report_enabled",
    checked: boolean,
  ) => {
    setStrategies((prev) =>
      prev.map((item) =>
        item.strategy_id === strategyId && item.mode === mode
          ? { ...item, [field]: checked }
          : item,
      ),
    );
  };

  // RB-03: Save lifecycle settings for row
  const handleSaveLifecycle = async (row: ResearchStrategy) => {
    const rowKey = `${row.strategy_id}:${row.mode}`;
    if (savingRowKey !== null) return;
    setSavingRowKey(rowKey);
    setActionError(null);
    setActionSuccess(null);

    try {
      await saveStrategyLifecycle({
        strategy_id: row.strategy_id,
        mode: row.mode as "shadow" | "inverse",
        evaluation_enabled: row.evaluation_enabled,
        paper_enabled: row.paper_enabled,
        telegram_enabled: row.telegram_enabled,
        report_enabled: row.report_enabled,
        auto_promote_enabled: false,
      });
      setActionSuccess(`Lifecycle settings saved for '${row.strategy_id}' (${row.mode}).`);
      setAttempt((n) => n + 1);
    } catch (cause) {
      setActionError(cause instanceof ResearchActionError ? cause.message : "Failed to save strategy lifecycle.");
    } finally {
      setSavingRowKey(null);
    }
  };

  // RB-04: Execute stop & hide
  const handleExecuteStopAndHide = async () => {
    if (!stopTargetRow || stopping) return;
    setStopping(true);
    setStopError(null);
    setActionError(null);
    setActionSuccess(null);

    try {
      await stopAndHideStrategy(
        stopTargetRow.strategy_id,
        stopTargetRow.mode as "shadow" | "inverse",
      );
      setActionSuccess(`Strategy '${stopTargetRow.strategy_id}' (${stopTargetRow.mode}) stopped and hidden.`);
      closeStopModal();
      setAttempt((n) => n + 1);
    } catch (cause) {
      setStopError(cause instanceof ResearchActionError ? cause.message : "Failed to stop strategy.");
    } finally {
      setStopping(false);
    }
  };

  const filteredStrategies = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return strategies.filter((row) => {
      return !needle || row.strategy_id.toLowerCase().includes(needle) || row.mode.toLowerCase().includes(needle);
    });
  }, [strategies, filter]);

  return (
    <div className="research-view" aria-busy={loading}>
      <section className="panel paper-safety-banner">
        <ShieldCheck size={19} />
        <div>
          <strong>{historyOnly ? "RetailBot2 Historical Archive (Read-Only)" : "RetailBot2 Research & Strategy Lifecycle"}</strong>
          <p>
            {historyOnly
              ? "Review historical RetailBot2 positions, closed trades, and saved mode/strategy performance summaries. RetailBot2 is retired — use Strategy Library and Paper Trading for active simulations."
              : "Review RetailBot2 positions, closed trades, strategy lifecycle flags, and saved performance summaries. Authorized operators can configure evaluation, paper tracking, Telegram alerts, and report visibility below."}
          </p>
        </div>
        <span className="panel-counter">{historyOnly ? "ARCHIVE" : "RESEARCH"}</span>
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
            <strong>Unable to load Research</strong>
            <p>{error}</p>
          </div>
          <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      )}

      <section className="metrics-grid" aria-label="Research summary">
        <article className="metric-card"><div className="metric-top"><span>Open positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.positions.length ?? "—"}</div><div className="metric-foot"><span>{historyOnly ? "Historical RetailBot2 open rows" : "Active RetailBot2 trades"}</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Recent closed trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.trades.length ?? "—"}</div><div className="metric-foot"><span>Latest 50 returned by API</span></div></article>
        {historyOnly ? (
          <>
            <article className="metric-card"><div className="metric-top"><span>Shadow realized PnL</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : value(data?.stats.by_mode.shadow?.realized_pnl)}</div><div className="metric-foot"><span>{data?.stats.by_mode.shadow ? `${value(data.stats.by_mode.shadow.closed_count)} closed trades` : "Historical shadow ledger"}</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Inverse realized PnL</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : value(data?.stats.by_mode.inverse?.realized_pnl)}</div><div className="metric-foot"><span>{data?.stats.by_mode.inverse ? `${value(data.stats.by_mode.inverse.closed_count)} closed trades` : "Historical inverse ledger"}</span></div></article>
          </>
        ) : (
          <>
            <article className="metric-card"><div className="metric-top"><span>Registered strategy rows</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.strategies.length ?? "—"}</div><div className="metric-foot"><span>Shadow and inverse modes</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Telegram-enabled rows</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : strategies.filter((row) => row.telegram_enabled).length}</div><div className="metric-foot"><span>Persisted lifecycle readback</span></div></article>
          </>
        )}
      </section>

      <section className="panel" aria-labelledby="research-performance-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-performance-title">Performance by mode</h2>
            <p>Shadow and inverse PnL excludes fees, slippage, and funding.</p>
          </div>
          <span className="panel-counter">{data ? 2 : 0} MODES</span>
        </div>
        {loading && !data ? (
          <p className="empty-state">Loading performance…</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Mode</th>
                  <th scope="col">Gross realized PnL</th>
                  <th scope="col">Closed trades</th>
                  <th scope="col">Wins</th>
                  <th scope="col">Win rate</th>
                  <th scope="col">Open trades</th>
                </tr>
              </thead>
              <tbody>
                {(["shadow", "inverse"] as const).map((mode) => {
                  const stats = data?.stats.by_mode[mode];
                  return (
                    <tr key={mode}>
                      <td>{mode}</td>
                      <td>{stats ? value(stats.realized_pnl) : "—"}</td>
                      <td>{stats ? value(stats.closed_count) : "—"}</td>
                      <td>{stats ? value(stats.wins) : "—"}</td>
                      <td>{stats?.win_rate == null ? "—" : `${value(stats.win_rate)}%`}</td>
                      <td>{stats ? value(stats.open_count) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="research-strategy-performance-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-strategy-performance-title">Performance by strategy</h2>
            <p>Closed-trade aggregates after report-visibility filtering.</p>
          </div>
          <span className="panel-counter">{data?.stats.by_strategy.length ?? 0} ROWS</span>
        </div>
        {loading && !data ? (
          <p className="empty-state">Loading strategy performance…</p>
        ) : data?.stats.by_strategy.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Closed trades</th>
                  <th scope="col">Wins</th>
                  <th scope="col">Losses</th>
                  <th scope="col">Gross realized PnL</th>
                  <th scope="col">Last updated</th>
                </tr>
              </thead>
              <tbody>
                {data.stats.by_strategy.map((row) => (
                  <tr key={row.strategy + row.mode}>
                    <td>{row.strategy}</td>
                    <td>{row.mode}</td>
                    <td>{value(row.total_trades)}</td>
                    <td>{value(row.wins)}</td>
                    <td>{value(row.losses)}</td>
                    <td>{value(row.total_pnl)}</td>
                    <td>{dateLabel(row.last_updated)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">No strategy performance rows returned.</p>
        )}
      </section>

      <section className="panel" aria-labelledby="research-positions-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-positions-title">Active positions</h2>
            <p>Open RetailBot2 rows from the existing API; no current-price or unrealized-PnL fields are returned by this route.</p>
          </div>
          <span className="panel-counter">{data?.positions.length ?? 0} ROWS</span>
        </div>
        {data?.positions.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Stop</th>
                  <th scope="col">Target</th>
                  <th scope="col">Strategy</th>
                  <th scope="col">Confluence</th>
                  <th scope="col">Opened</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((row) => (
                  <tr key={row.id}>
                    <td>{row.symbol}</td>
                    <td>{row.side}</td>
                    <td>{row.mode}</td>
                    <td>{value(row.entry_price)}</td>
                    <td>{value(row.stop_price)}</td>
                    <td>{value(row.target_price)}</td>
                    <td>{row.strategy}</td>
                    <td>{value(row.confluence_score)}</td>
                    <td>{dateLabel(row.entry_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">{loading ? "Loading positions…" : "No active Research positions returned."}</p>
        )}
      </section>

      <section className="panel" aria-labelledby="research-trades-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-trades-title">Recent closed trades</h2>
            <p>Historical trades returned by the Research API, using the ledger’s net PnL and exit timestamp fields.</p>
          </div>
          <span className="panel-counter">{data?.trades.length ?? 0} ROWS</span>
        </div>
        {data?.trades.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Exit</th>
                  <th scope="col">Net PnL</th>
                  <th scope="col">Exit reason</th>
                  <th scope="col">Closed</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((row) => (
                  <tr key={row.id}>
                    <td>{row.symbol}</td>
                    <td>{row.side}</td>
                    <td>{row.mode}</td>
                    <td>{value(row.entry_price)}</td>
                    <td>{value(row.exit_price)}</td>
                    <td>{value(row.net_pnl)}</td>
                    <td>{value(row.exit_reason)}</td>
                    <td>{dateLabel(row.exit_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">{loading ? "Loading trades…" : "No closed trades returned."}</p>
        )}
      </section>

      {/* RB-03 & RB-04: Strategy Lifecycle Snapshot & Management (hidden in retired historyOnly mode) */}
      {!historyOnly && (
      <section className="panel" aria-labelledby="research-strategies-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-strategies-title">Strategy lifecycle snapshot</h2>
            <p>{historyOnly ? "Saved settings from the legacy research worker. Manage new simulations in Paper Trading." : "Evaluation, paper, Telegram, and report visibility controls for registered RetailBot2 strategies."}</p>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <span className="panel-counter">{filteredStrategies.length} ROWS</span>
            <button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}>
              <RefreshCw size={15} /> Refresh
            </button>
          </div>
        </div>

        <label className="filter-field">
          Filter strategies{" "}
          <input
            aria-label="Filter strategies"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Strategy ID or mode"
          />
        </label>

        {filteredStrategies.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Evaluation</th>
                  <th scope="col">Paper</th>
                  <th scope="col">Telegram</th>
                  <th scope="col">Report</th>
                  <th scope="col">Auto-promote</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredStrategies.map((row) => {
                  const rowKey = `${row.strategy_id}:${row.mode}`;
                  const isSaving = savingRowKey === rowKey;
                  return (
                    <tr key={rowKey}>
                      <td><strong>{row.strategy_id}</strong></td>
                      <td>{row.mode}</td>
                      <td>
                        {canTrade ? (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: row.historical_only ? "not-allowed" : "pointer" }}>
                            <input
                              type="checkbox"
                              checked={row.evaluation_enabled}
                              disabled={row.historical_only || isSaving}
                              title={row.historical_only ? "Historical-only strategy cannot be enabled for live evaluation" : undefined}
                              aria-label={`Evaluation for ${row.strategy_id} (${row.mode})`}
                              onChange={(e) => handleCheckboxChange(row.strategy_id, row.mode, "evaluation_enabled", e.target.checked)}
                            />
                            <span>{row.evaluation_enabled ? "On" : "Off"}</span>
                          </label>
                        ) : (
                          <span>{row.evaluation_enabled ? "On" : "Off"}</span>
                        )}
                      </td>
                      <td>
                        {canTrade ? (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: row.historical_only ? "not-allowed" : "pointer" }}>
                            <input
                              type="checkbox"
                              checked={row.paper_enabled}
                              disabled={row.historical_only || isSaving}
                              title={row.historical_only ? "Historical-only strategy cannot be enabled for paper trading" : undefined}
                              aria-label={`Paper tracking for ${row.strategy_id} (${row.mode})`}
                              onChange={(e) => handleCheckboxChange(row.strategy_id, row.mode, "paper_enabled", e.target.checked)}
                            />
                            <span>{row.paper_enabled ? "On" : "Off"}</span>
                          </label>
                        ) : (
                          <span>{row.paper_enabled ? "On" : "Off"}</span>
                        )}
                      </td>
                      <td>
                        {canTrade ? (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: row.historical_only ? "not-allowed" : "pointer" }}>
                            <input
                              type="checkbox"
                              checked={row.telegram_enabled}
                              disabled={row.historical_only || isSaving}
                              title={row.historical_only ? "Historical-only strategy cannot broadcast to Telegram" : undefined}
                              aria-label={`Telegram alerts for ${row.strategy_id} (${row.mode})`}
                              onChange={(e) => handleCheckboxChange(row.strategy_id, row.mode, "telegram_enabled", e.target.checked)}
                            />
                            <span>{row.telegram_enabled ? "On" : "Off"}</span>
                          </label>
                        ) : (
                          <span>{row.telegram_enabled ? "On" : "Off"}</span>
                        )}
                      </td>
                      <td>
                        {canTrade ? (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={row.report_enabled}
                              disabled={isSaving}
                              aria-label={`Report visibility for ${row.strategy_id} (${row.mode})`}
                              onChange={(e) => handleCheckboxChange(row.strategy_id, row.mode, "report_enabled", e.target.checked)}
                            />
                            <span>{row.report_enabled ? "Visible" : "Hidden"}</span>
                          </label>
                        ) : (
                          <span>{row.report_enabled ? "Visible" : "Hidden"}</span>
                        )}
                      </td>
                      <td>
                        <span title="Auto-promote is currently unavailable">
                          {row.auto_promote_enabled ? "On" : "Unavailable"}
                        </span>
                      </td>
                      <td>
                        {canTrade ? (
                          <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                            <button
                              type="button"
                              className="button button-secondary button-small"
                              onClick={() => handleSaveLifecycle(row)}
                              disabled={isSaving}
                            >
                              {isSaving ? "Saving…" : "Save"}
                            </button>
                            {!row.historical_only && (
                              <button
                                type="button"
                                className="button button-danger button-small"
                                onClick={(e) => openStopModal(row, e.currentTarget)}
                                disabled={isSaving}
                              >
                                Stop &amp; Hide
                              </button>
                            )}
                          </div>
                        ) : (
                          <span className="text-muted text-sm">View only</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">{loading ? "Loading strategies…" : "No strategies match this filter."}</p>
        )}
      </section>
      )}

      {/* RB-04 Modal: Stop & Hide Confirmation */}
      {!historyOnly && stopTargetRow && (
        <dialog
          ref={stopDialogRef}
          className="app-dialog"
          aria-labelledby="stop-modal-title"
          aria-describedby="stop-modal-desc"
          onCancel={(e) => { e.preventDefault(); closeStopModal(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); closeStopModal(); } else { trapFocus(e); } }}
        >
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <h3 id="stop-modal-title">Stop &amp; Hide Strategy</h3>
              <p id="stop-modal-desc">
                Are you sure you want to stop and hide strategy <strong>{stopTargetRow.strategy_id}</strong> ({stopTargetRow.mode})?
              </p>
            </div>
            <button type="button" aria-label="Close dialog" onClick={closeStopModal} className="icon-button">
              <X size={16} />
            </button>
          </div>

          <div style={{ background: "var(--color-elevated)", padding: "12px", borderRadius: "8px", marginBottom: "16px", fontSize: "0.85rem" }}>
            <p style={{ margin: 0 }}>
              This will disable live evaluation, paper tracking, and Telegram alerts, and hide the strategy from performance reports.
            </p>
          </div>

          {stopError && (
            <div className="api-error" role="alert" style={{ marginBottom: "14px" }}>
              <div><p>{stopError}</p></div>
            </div>
          )}

          <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
            <button type="button" className="button button-secondary" onClick={closeStopModal} disabled={stopping}>
              Cancel
            </button>
            <button type="button" className="button button-danger" onClick={handleExecuteStopAndHide} disabled={stopping}>
              {stopping ? "Stopping…" : "Stop & Hide Strategy"}
            </button>
          </div>
        </dialog>
      )}
    </div>
  );
}
