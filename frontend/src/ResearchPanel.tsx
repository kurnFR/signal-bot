import { useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import {
  fetchResearchSnapshot,
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

// RetailBot2 is retired: this panel is a read-only archive of its saved
// positions, trades, stats, and lifecycle rows. The worker is deleted and
// POST /api/research/retailbot2/strategies and /control are retired, so
// there are no lifecycle controls here. Active simulations live in
// Strategy Library and Paper Trading.
export function ResearchPanel() {
  const [data, setData] = useState<ResearchSnapshot | null>(null);
  const [strategies, setStrategies] = useState<ResearchStrategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchResearchSnapshot(controller.signal)
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
  }, [attempt]);

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
          <strong>RetailBot2 Historical Archive (Read-Only)</strong>
          <p>
            Review historical RetailBot2 positions, closed trades, and saved mode/strategy performance summaries. RetailBot2 is retired — use Strategy Library and Paper Trading for active simulations.
          </p>
        </div>
        <span className="panel-counter">ARCHIVE</span>
      </section>

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
        <article className="metric-card"><div className="metric-top"><span>Open positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.positions.length ?? "—"}</div><div className="metric-foot"><span>Historical RetailBot2 open rows</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Recent closed trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.trades.length ?? "—"}</div><div className="metric-foot"><span>Latest 50 returned by API</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Shadow realized PnL</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : value(data?.stats.by_mode.shadow?.realized_pnl)}</div><div className="metric-foot"><span>{data?.stats.by_mode.shadow ? `${value(data.stats.by_mode.shadow.closed_count)} closed trades` : "Historical shadow ledger"}</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Inverse realized PnL</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : value(data?.stats.by_mode.inverse?.realized_pnl)}</div><div className="metric-foot"><span>{data?.stats.by_mode.inverse ? `${value(data.stats.by_mode.inverse.closed_count)} closed trades` : "Historical inverse ledger"}</span></div></article>
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
            <h2 id="research-positions-title">Historical open positions</h2>
            <p>Historical open RetailBot2 rows from the archive; no current-price or unrealized-PnL fields are returned by this route.</p>
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
          <p className="empty-state">{loading ? "Loading positions…" : "No historical open positions returned."}</p>
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

      {/* Read-only snapshot of the saved lifecycle rows (archived). No
          editing: the POST endpoints that wrote these rows are retired. */}
      <section className="panel" aria-labelledby="research-strategies-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-strategies-title">Strategy lifecycle snapshot</h2>
            <p>Saved settings from the legacy research worker. Manage new simulations in Paper Trading.</p>
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
                </tr>
              </thead>
              <tbody>
                {filteredStrategies.map((row) => {
                  const rowKey = `${row.strategy_id}:${row.mode}`;
                  return (
                    <tr key={rowKey}>
                      <td><strong>{row.strategy_id}</strong></td>
                      <td>{row.mode}</td>
                      <td>{row.evaluation_enabled ? "On" : "Off"}</td>
                      <td>{row.paper_enabled ? "On" : "Off"}</td>
                      <td>{row.telegram_enabled ? "On" : "Off"}</td>
                      <td>{row.report_enabled ? "Visible" : "Hidden"}</td>
                      <td>
                        <span title="Auto-promote is currently unavailable">
                          {row.auto_promote_enabled ? "On" : "Unavailable"}
                        </span>
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
    </div>
  );
}
