import { useEffect, useState } from "react";
import { BookOpen, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { fetchCustomStrategies, fetchStrategyIndicators, type CustomStrategy, type StrategyIndicator } from "./lib/strategyLibrary";

function messageFor(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) return "Your session has expired. Sign in again to view the strategy library.";
  if (cause instanceof ApiError && cause.status === 403) return "Your account cannot access the strategy library.";
  return "Strategy library data could not be loaded. Check the API connection and retry.";
}
function Conditions({ label, items }: { label: string; items?: CustomStrategy["longConditions"] }) {
  return <div className="strategy-conditions"><strong>{label}</strong>{items?.length ? <ul>{items.map((condition, index) => <li key={label + index}><code>{condition.indicator ?? "indicator"} {condition.operator ?? "?"} {String(condition.value ?? "—")}</code></li>)}</ul> : <p className="empty-state">No conditions defined.</p>}</div>;
}
export function StrategyLibraryPanel() {
  const [strategies, setStrategies] = useState<CustomStrategy[]>([]);
  const [indicators, setIndicators] = useState<StrategyIndicator[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([fetchCustomStrategies(controller.signal), fetchStrategyIndicators(controller.signal)])
      .then(([saved, definitions]) => {
        setStrategies(Array.isArray(saved.strategies) ? saved.strategies : []);
        setIndicators(Array.isArray(definitions.indicators) ? definitions.indicators : []);
      })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(messageFor(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  return <>
    <section className="metrics-grid" aria-label="Strategy library summary" aria-busy={loading}>
      <article className="metric-card"><div className="metric-top"><span>Saved custom strategies</span><span className="metric-icon"><BookOpen size={17} /></span></div><div className="metric-value">{loading && !strategies.length ? "Loading…" : strategies.length}</div><div className="metric-foot"><span>Persisted definitions</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Available indicators</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{loading && !indicators.length ? "Loading…" : indicators.length}</div><div className="metric-foot"><span>From backend metadata</span></div></article>
    </section>
    {error && <div className="error-banner" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    <section className="panel" aria-labelledby="strategy-library-title">
      <div className="panel-heading"><div><h2 id="strategy-library-title">Custom strategy library</h2><p>Read-only view of saved rule definitions from the existing API.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      {loading && !strategies.length && !error ? <p className="empty-state" role="status">Loading saved strategies…</p> : !strategies.length && !error ? <p className="empty-state">No saved custom strategies were returned by the API.</p> : <div className="strategy-library-list">{strategies.map((strategy) => <article className="strategy-library-card" key={strategy.name}>
        <div className="panel-heading"><div><h3>{strategy.displayName || strategy.name}</h3><p>{strategy.name}</p></div><span className="panel-counter">SAVED DEFINITION</span></div>
        {strategy.description && <p>{strategy.description}</p>}{strategy.rationale && <p className="strategy-rationale">{strategy.rationale}</p>}
        <div className="strategy-rule-grid"><Conditions label="Long entry conditions" items={strategy.longConditions} /><Conditions label="Short entry conditions" items={strategy.shortConditions} /></div>
        <div className="strategy-param-list"><span>Stop loss: {strategy.slAtrMult ?? "—"} ATR</span><span>Risk/reward: {strategy.riskRewardRatio ?? "—"}:1</span><span>Trailing stop: {strategy.useTrailingStop ? "Enabled" : "Disabled"}</span></div>
      </article>)}</div>}
    </section>
    <section className="panel" aria-labelledby="indicator-catalog-title">
      <div className="panel-heading"><div><h2 id="indicator-catalog-title">Indicator catalog</h2><p>Definitions and descriptions provided by the backend.</p></div><span className="panel-counter">{indicators.length} INDICATORS</span></div>
      {loading && !indicators.length && !error ? <p className="empty-state" role="status">Loading indicator catalog…</p> : <div className="table-scroll"><table><thead><tr><th scope="col">Indicator</th><th scope="col">Category</th><th scope="col">Description</th><th scope="col">Default</th></tr></thead><tbody>{indicators.map((item) => <tr key={item.name}><td><strong>{item.label || item.name}</strong><div><code>{item.name}</code></div></td><td>{item.category || "—"}</td><td>{item.description || "—"}</td><td>{item.default ?? "—"}</td></tr>)}</tbody></table></div>}
    </section>
    <p className="page-description">Safety: this migration slice only reads strategy definitions and indicator metadata. Creating, deleting, generating, quick-testing, promoting, or activating strategies remains unavailable here.</p>
  </>;
}
