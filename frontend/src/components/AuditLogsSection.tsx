import { useEffect, useState } from "react";
import { Download, RefreshCw, Shield } from "lucide-react";
import { fetchAuditLogs, type AuditLogEntry } from "../lib/users";

function escapeCsvCell(value: unknown): string {
  const str = value == null ? "" : typeof value === "string" ? value : JSON.stringify(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replaceAll('"', '""')}"`;
  }
  return str;
}

export function AuditLogsSection() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchAuditLogs(
      {
        limit: 100,
        action: actionFilter.trim() || undefined,
        username: userFilter.trim() || undefined,
      },
      controller.signal,
    )
      .then((items) => {
        if (!controller.signal.aborted) setLogs(items);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Failed to load audit logs");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [actionFilter, userFilter, attempt]);

  const handleExportCsv = () => {
    const header = ["id", "created_at", "username", "action", "ip_address", "details"];
    const rows = logs.map((entry) =>
      [
        entry.id,
        entry.created_at,
        entry.username,
        entry.action,
        entry.ip_address ?? "",
        entry.details ? JSON.stringify(entry.details) : "",
      ]
        .map(escapeCsvCell)
        .join(","),
    );
    const csv = [header.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "signal-bot-audit-logs.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="panel" aria-labelledby="settings-audit-logs-title" aria-busy={loading}>
      <div className="panel-heading">
        <div>
          <h2 id="settings-audit-logs-title">
            <Shield size={17} className="inline-icon" /> Operator Audit Logs
          </h2>
          <p>Immutable history of security, user management, Telegram, and compliance actions.</p>
        </div>
        <span className="panel-counter">{logs.length} EVENTS</span>
      </div>

      <div className="screener-control-grid" style={{ marginBottom: 12 }}>
        <div className="screener-control-field">
          <label htmlFor="audit-filter-action">Filter by Action</label>
          <input
            id="audit-filter-action"
            type="text"
            placeholder="e.g. USER_CREATED, TERMS_ACCEPTED"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
          />
        </div>
        <div className="screener-control-field">
          <label htmlFor="audit-filter-user">Filter by Username</label>
          <input
            id="audit-filter-user"
            type="text"
            placeholder="e.g. admin"
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
          />
        </div>
      </div>

      {error && (
        <div className="api-error" role="alert" style={{ marginBottom: 12 }}>
          <div>
            <strong>Audit logs unavailable</strong>
            <p>{error}</p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => setAttempt((v) => v + 1)}
          >
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      )}

      {loading && logs.length === 0 ? (
        <p className="empty-state" role="status">Loading audit logs…</p>
      ) : logs.length === 0 ? (
        <p className="empty-state">No audit log entries match the current filters.</p>
      ) : (
        <div className="status-table-wrap">
          <table className="status-table">
            <thead>
              <tr>
                <th scope="col">Timestamp</th>
                <th scope="col">User</th>
                <th scope="col">Action</th>
                <th scope="col">IP</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.created_at}</td>
                  <td>{entry.username}</td>
                  <td><code>{entry.action}</code></td>
                  <td>{entry.ip_address || "—"}</td>
                  <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {entry.details ? JSON.stringify(entry.details) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="screener-control-footer" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          className="button button-secondary"
          type="button"
          disabled={loading}
          onClick={() => setAttempt((v) => v + 1)}
        >
          <RefreshCw size={15} /> Refresh logs
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={logs.length === 0}
          onClick={handleExportCsv}
        >
          <Download size={15} /> Export CSV
        </button>
      </div>
    </section>
  );
}
