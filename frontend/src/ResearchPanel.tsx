import { useEffect, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import {
  fetchResearchSnapshot,
  type ResearchSnapshot,
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

// Paper Trading research archive (read-only): open positions from
// paper_positions, closed trades and per-strategy stats from
// paper_trades. The retired RetailBot2 rdt_* tables are dropped by
// migration 041, so there is no shadow/inverse mode anymore -- every
// row here is mode "paper". Lifecycle controls (enable/disable,
// Telegram, reports) live per deployment in Paper Trading.
export function ResearchPanel() {
  const [data, setData] = useState<ResearchSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchResearchSnapshot(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
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

  const modeEntries = data ? Object.entries(data.stats.by_mode) : [];
  const paperStats = data?.stats.by_mode.paper;

  return (
    <div className="research-view" aria-busy={loading}>
      <section className="panel paper-safety-banner">
        <ShieldCheck size={19} />
        <div>
          <strong>Paper Trading Research Archive (Read-Only)</strong>
          <p>
            Review Paper Trading open positions, closed trades, and per-strategy performance from the paper_trades ledger. Use Strategy Library and Paper Trading for active simulations.
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
        <article className="metric-card"><div className="metric-top"><span>Open positions</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.positions.length ?? "—"}</div><div className="metric-foot"><span>Open paper_positions rows</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Recent closed trades</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : data?.trades.length ?? "—"}</div><div className="metric-foot"><span>Latest 50 returned by API</span></div></article>
        <article className="metric-card"><div className="metric-top"><span>Paper realized PnL</span><span className="metric-icon"><Activity size={17} /></span></div><div className="metric-value">{loading && !data ? "Loading…" : value(paperStats?.realized_pnl)}</div><div className="metric-foot"><span>{paperStats ? `${value(paperStats.closed_count)} closed trades` : "Paper trading ledger"}</span></div></article>
      </section>

      <section className="panel" aria-labelledby="research-performance-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-performance-title">Performance by mode</h2>
            <p>Paper trading PnL includes fees, slippage, and funding as recorded in paper_trades.</p>
          </div>
          <span className="panel-counter">{modeEntries.length} MODES</span>
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
                {modeEntries.map(([mode, stats]) => (
                  <tr key={mode}>
                    <td>{mode}</td>
                    <td>{value(stats.realized_pnl)}</td>
                    <td>{value(stats.closed_count)}</td>
                    <td>{value(stats.wins)}</td>
                    <td>{stats.win_rate == null ? "—" : `${value(stats.win_rate)}%`}</td>
                    <td>{value(stats.open_count)}</td>
                  </tr>
                ))}
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
            <h2 id="research-positions-title">Open paper positions</h2>
            <p>Open rows from paper_positions; no current-price or unrealized-PnL fields are returned by this route.</p>
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
                  <th scope="col">Market</th>
                  <th scope="col">Timeframe</th>
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
                    <td>{value(row.market)}</td>
                    <td>{value(row.timeframe)}</td>
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
          <p className="empty-state">{loading ? "Loading positions…" : "No open paper positions returned."}</p>
        )}
      </section>

      <section className="panel" aria-labelledby="research-trades-title">
        <div className="panel-heading">
          <div>
            <h2 id="research-trades-title">Recent closed trades</h2>
            <p>Closed trades from the paper_trades ledger, using the ledger’s net PnL and exit timestamp fields.</p>
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
                  <th scope="col">Market</th>
                  <th scope="col">Timeframe</th>
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
                    <td>{value(row.market)}</td>
                    <td>{value(row.timeframe)}</td>
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
    </div>
  );
}
