import { useState } from "react";
import { Archive, Download, RefreshCw } from "lucide-react";
import { ApiError, getJson } from "../lib/api";

interface ArchiveSummary {
  tradesCount: number;
  statsCount: number;
}

interface ModeStat {
  realized_pnl: number;
  closed_count: number;
  wins: number;
  win_rate: number | null;
  open_count: number;
}

interface StatsResponse {
  by_strategy: Array<{
    strategy: string;
    mode: string;
    total_trades: number;
    wins: number;
    losses: number;
    total_pnl: number;
    last_updated: string | null;
  }>;
  by_mode: Record<string, ModeStat>;
}

const ARCHIVE_EXPORT_META = {
  trades: {
    url: "/api/research/retailbot2/archive/trades.csv",
    filename: "paper_trades_archive.csv",
    label: "Export paper_trades (CSV)",
  },
  stats: {
    url: "/api/research/retailbot2/archive/stats.csv",
    filename: "paper_strategy_stats_archive.csv",
    label: "Export paper strategy stats (CSV)",
  },
} as const;

function readAuthToken(): string | null {
  try {
    return window.localStorage.getItem("token");
  } catch {
    return null;
  }
}

function isArchiveSummary(val: unknown): val is ArchiveSummary {
  return (
    val !== null &&
    typeof val === "object" &&
    !Array.isArray(val) &&
    typeof (val as ArchiveSummary).tradesCount === "number" &&
    typeof (val as ArchiveSummary).statsCount === "number"
  );
}

/** Read-only archive under Settings providing row counts, stats preview, and CSV downloads for the Paper Trading ledger (paper_trades). The retired rdt_* tables are dropped by migration 041. */
export function RetailBot2ArchiveSection() {
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<ArchiveSummary | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"trades" | "stats" | null>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);

  const loadArchive = async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryPayload, statsPayload] = await Promise.all([
        getJson<unknown>("/api/research/retailbot2/archive/summary"),
        getJson<StatsResponse>("/api/research/retailbot2/stats"),
      ]);
      if (!isArchiveSummary(summaryPayload)) {
        throw new Error("Archive summary returned an invalid payload");
      }
      setSummary(summaryPayload);
      setStats(statsPayload);
    } catch (cause: unknown) {
      if (cause instanceof ApiError && cause.status === 401) {
        setError("Session expired. Sign in again to load the archive summary.");
      } else {
        setError("Failed to load Paper Trading archive.");
      }
    } finally {
      setLoading(false);
    }
  };

  const downloadCsv = async (kind: "trades" | "stats") => {
    const meta = ARCHIVE_EXPORT_META[kind];
    setExporting(kind);
    setError(null);
    setExportMessage(null);
    try {
      const token = readAuthToken();
      const response = await fetch(meta.url, {
        method: "GET",
        credentials: "same-origin",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        throw new Error(`Export failed (${response.status})`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = meta.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
      setExportMessage(`Downloaded ${meta.filename}.`);
    } catch {
      setError(`Could not export ${meta.filename}. Check your session and database health.`);
    } finally {
      setExporting(null);
    }
  };

  return (
    <section className="panel" aria-labelledby="settings-retailbot2-archive-title" aria-busy={loading || exporting !== null}>
      <div className="panel-heading">
        <div>
          <h2 id="settings-retailbot2-archive-title"><Archive size={17} className="inline-icon" /> Paper Trading Archive (Read-Only)</h2>
          <p>Export closed trades from <code>paper_trades</code> and per-strategy aggregates computed from the same ledger via streamed CSV. Active simulation controls live in Paper Trading.</p>
        </div>
        <span className="panel-counter">READ-ONLY ARCHIVE</span>
      </div>

      {error && <div className="api-error" role="alert"><div><strong>Archive request failed</strong><p>{error}</p></div></div>}
      {exportMessage && <p className="control-feedback control-success" role="status">{exportMessage}</p>}

      {summary && (
        <div className="settings-telegram-status" style={{ marginBottom: 12 }}>
          <div><span>Paper trades</span><strong>{summary.tradesCount.toLocaleString()} rows</strong></div>
          <div><span>Paper strategies</span><strong>{summary.statsCount.toLocaleString()} rows</strong></div>
          {stats?.by_mode && Object.entries(stats.by_mode).map(([mode, m]) => (
            <div key={mode}>
              <span>{mode.toUpperCase()} PnL ({m.closed_count} closed)</span>
              <strong>${Number(m.realized_pnl ?? 0).toFixed(2)}</strong>
            </div>
          ))}
        </div>
      )}

      {stats?.by_strategy && stats.by_strategy.length > 0 && (
        <div className="status-table-wrap" style={{ marginBottom: 12 }}>
          <table className="status-table">
            <thead>
              <tr>
                <th scope="col">Strategy</th>
                <th scope="col">Mode</th>
                <th scope="col">Trades</th>
                <th scope="col">Wins</th>
                <th scope="col">Total PnL</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_strategy.map((row) => (
                <tr key={`${row.strategy}-${row.mode}`}>
                  <td>{row.strategy}</td>
                  <td>{row.mode}</td>
                  <td>{row.total_trades}</td>
                  <td>{row.wins}</td>
                  <td>${Number(row.total_pnl ?? 0).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="screener-control-footer" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="button button-secondary"
          disabled={loading}
          onClick={() => void loadArchive()}
        >
          <RefreshCw size={15} />
          {loading ? "Loading archive…" : summary ? "Refresh archive summary" : "Load archive summary"}
        </button>
        <button
          type="button"
          className="button button-secondary"
          disabled={exporting !== null}
          onClick={() => void downloadCsv("trades")}
        >
          <Download size={15} />
          {exporting === "trades" ? "Exporting paper_trades…" : ARCHIVE_EXPORT_META.trades.label}
        </button>
        <button
          type="button"
          className="button button-secondary"
          disabled={exporting !== null}
          onClick={() => void downloadCsv("stats")}
        >
          <Download size={15} />
          {exporting === "stats" ? "Exporting paper strategy stats…" : ARCHIVE_EXPORT_META.stats.label}
        </button>
      </div>
    </section>
  );
}
