import { useEffect, useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { fetchPromotionSnapshot, type PromotionSnapshot } from "./lib/backtestStudio";

const numberLabel = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
function timestampLabel(value: string | null) {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
}

export function PromotionStatusPanel() {
  const [snapshot, setSnapshot] = useState<PromotionSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchPromotionSnapshot(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setSnapshot(result);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("Promotion policy and audit could not be loaded. Check API/database health, then retry.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);

  return <section className="panel" aria-labelledby="promotion-status-title" aria-busy={loading}>
    <div className="panel-heading">
      <div>
        <h2 id="promotion-status-title">Auto-promotion policy and audit</h2>
        <p>Read-only visibility into the saved promotion policy and recent decisions. This panel never changes policy, activates a strategy, or sends Telegram alerts.</p>
      </div>
      <button className="button button-secondary" type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading}>
        <RefreshCw size={15} /> Refresh
      </button>
    </div>
    {error && <div className="api-error" role="alert">
      <div><strong>Promotion status unavailable</strong><p>{error}</p></div>
      <button className="button button-secondary" type="button" onClick={() => setAttempt((value) => value + 1)} disabled={loading}>Retry</button>
    </div>}
    {loading && !snapshot ? <p className="empty-state" role="status">Loading promotion policy and audit…</p> : snapshot && <>
      <div className="panel-subsection">
        <div className="panel-heading">
          <div><h3>Saved policy</h3><p>Thresholds are shown as currently stored by the backend.</p></div>
          <span className="panel-counter">{snapshot.policy.enabled ? "ENABLED" : "DISABLED"}</span>
        </div>
        <div className="metrics-grid" aria-label="Promotion policy thresholds">
          <article className="metric-card"><div className="metric-top"><span>Minimum test trades</span></div><div className="metric-value">{snapshot.policy.min_test_trades}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum profit factor</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_profit_factor)}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum expectancy</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_expectancy_r)} R</div></article>
          <article className="metric-card"><div className="metric-top"><span>Maximum drawdown</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.max_drawdown_r)} R</div></article>
          <article className="metric-card"><div className="metric-top"><span>Minimum rank score</span></div><div className="metric-value">{numberLabel.format(snapshot.policy.min_rank_score)}</div></article>
          <article className="metric-card"><div className="metric-top"><span>Holdout required</span></div><div className="metric-value">{snapshot.policy.require_holdout ? "Yes" : "No"}</div></article>
        </div>
        <p className="control-feedback"><ShieldCheck size={15} /> Telegram alert setting: <strong>{snapshot.policy.send_telegram ? "Enabled" : "Disabled"}</strong>. If promotion is run by the legacy workflow, this setting may cause an external notification.</p>
      </div>
      <div className="panel-subsection">
        <div className="panel-heading"><div><h3>Recent promotion decisions</h3><p>Decision records can include rejected, promoted, alert-failed, and reconciliation-required outcomes.</p></div><span className="panel-counter">{snapshot.events.length} RECORDS</span></div>
        {snapshot.events.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Created</th><th scope="col">Strategy</th><th scope="col">Market</th><th scope="col">Segment</th><th scope="col">Status</th><th scope="col">Paper config</th><th scope="col">Telegram result</th><th scope="col">Reason</th></tr></thead><tbody>
          {snapshot.events.map((event) => <tr key={event.decision_hash}>
            <td>{timestampLabel(event.created_at)}</td>
            <td><strong>{event.strategy_name}</strong><div>{event.symbol} · {event.timeframe}</div></td>
            <td>{event.market}</td><td>{event.data_segment}</td><td>{event.status.replaceAll("_", " ")}</td>
            <td>{event.paper_config_id ?? "—"}</td><td>{event.telegram_result || "—"}</td><td>{event.reason || "—"}</td>
          </tr>)}
        </tbody></table></div> : <p className="empty-state">No promotion decisions have been recorded.</p>}
      </div>
    </>}
  </section>;
}
