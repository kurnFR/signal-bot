import { useEffect, useState } from "react";
import { useAuth } from "./AuthGate";
import { BookOpen, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { createCustomStrategy, deleteCustomStrategy, fetchCustomStrategies, fetchStrategyIndicators, quickTestCustomStrategy, StrategyLibraryActionError, StrategyQuickTestError, type CustomStrategy, type QuickTestResult, type StrategyConditionInput, type StrategyIndicator } from "./lib/strategyLibrary";

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
  const [operators, setOperators] = useState<string[]>(["<", "<=", ">", ">=", "==", "!="]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const auth = useAuth();
  const canQuickTest = ["admin", "trader"].includes((auth?.user.role ?? "").toLowerCase());
  const [testSymbol, setTestSymbol] = useState("BTCUSDT");
  const [testMarket, setTestMarket] = useState<"spot" | "futures">("spot");
  const [testTimeframe, setTestTimeframe] = useState("1h");
  const [quickTestBusy, setQuickTestBusy] = useState<string | null>(null);
  const [quickTestError, setQuickTestError] = useState<string | null>(null);
  const [quickTestResults, setQuickTestResults] = useState<Record<string, QuickTestResult>>({});
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDisplayName, setCreateDisplayName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [createRationale, setCreateRationale] = useState("");
  const [longConditions, setLongConditions] = useState([{ indicator: "rsi", operator: "<", value: "30" }]);
  const [shortConditions, setShortConditions] = useState([{ indicator: "rsi", operator: ">", value: "70" }]);
  const [slAtrMult, setSlAtrMult] = useState("1.5");
  const [riskRewardRatio, setRiskRewardRatio] = useState("2.5");
  const [useTrailingStop, setUseTrailingStop] = useState(false);
  const [trailActivationR, setTrailActivationR] = useState("1");
  const [trailDistanceAtrMult, setTrailDistanceAtrMult] = useState("1.5");
  const [actionBusy, setActionBusy] = useState<"create" | "delete" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CustomStrategy | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    Promise.all([fetchCustomStrategies(controller.signal), fetchStrategyIndicators(controller.signal)])
      .then(([saved, definitions]) => {
        setStrategies(Array.isArray(saved.strategies) ? saved.strategies : []);
        setIndicators(Array.isArray(definitions.indicators) ? definitions.indicators : []);
        setOperators(Array.isArray(definitions.operators) && definitions.operators.length ? definitions.operators : ["<", "<=", ">", ">=", "==", "!="]);
      })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(messageFor(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);
  const canManageStrategies = canQuickTest;
  const conditionInput = (conditions: Array<{ indicator: string; operator: string; value: string }>): StrategyConditionInput[] =>
    conditions.map((condition) => {
      const value = condition.value.trim();
      const numeric = Number(value);
      return { indicator: condition.indicator, operator: condition.operator, value: value !== "" && Number.isFinite(numeric) ? numeric : value };
    });
  const submitCreateStrategy = async () => {
    const normalizedName = createName.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
    if (!normalizedName || normalizedName.length > 80) { setActionError("Enter a valid strategy name (letters, numbers, underscores; max 80 characters)."); return; }
    if (!createDisplayName.trim()) { setActionError("Display name is required."); return; }
    if (longConditions.length + shortConditions.length === 0) { setActionError("Add at least one long or short entry condition."); return; }
    const sl = Number(slAtrMult), rr = Number(riskRewardRatio), activation = Number(trailActivationR), distance = Number(trailDistanceAtrMult);
    if (!Number.isFinite(sl) || sl < 0.5 || sl > 5 || !Number.isFinite(rr) || rr < 1 || rr > 6 ||
        !Number.isFinite(activation) || activation < 0.25 || activation > 5 ||
        !Number.isFinite(distance) || distance < 0.5 || distance > 5) {
      setActionError("Stop-loss, risk/reward, and trailing-stop values must be within the documented ranges."); return;
    }
    if ([...longConditions, ...shortConditions].some((condition) => !condition.indicator || !condition.operator || !condition.value.trim())) {
      setActionError("Every entry condition needs an indicator, operator, and value."); return;
    }
    setActionBusy("create"); setActionError(null); setActionMessage(null);
    try {
      const result = await createCustomStrategy({
        name: normalizedName, displayName: createDisplayName.trim(),
        description: createDescription.trim() || "Custom rule-based strategy",
        rationale: createRationale.trim(),
        longConditions: conditionInput(longConditions), shortConditions: conditionInput(shortConditions),
        slAtrMult: sl, riskRewardRatio: rr, useTrailingStop,
        trailActivationR: activation, trailDistanceAtrMult: distance,
      });
      setActionMessage(result.message); setShowCreateForm(false); setAttempt((value) => value + 1);
    } catch (cause) {
      if (cause instanceof StrategyLibraryActionError && cause.status === 403) setActionError("Your account cannot create strategies. Only admin or trader roles can perform this action.");
      else setActionError(cause instanceof Error ? cause.message : "Strategy creation failed. Please retry.");
    } finally { setActionBusy(null); }
  };
  const confirmDeleteStrategy = async () => {
    if (!pendingDelete) return;
    setActionBusy("delete"); setActionError(null); setActionMessage(null);
    try {
      const result = await deleteCustomStrategy(pendingDelete.name);
      setActionMessage(result.message); setPendingDelete(null); setAttempt((value) => value + 1);
    } catch (cause) {
      if (cause instanceof StrategyLibraryActionError && cause.status === 403) setActionError("Your account cannot delete strategies. Only admin or trader roles can perform this action.");
      else setActionError(cause instanceof Error ? cause.message : "Strategy deletion failed. Please retry.");
    } finally { setActionBusy(null); }
  };
  const runQuickTest = async (strategy: CustomStrategy) => {
    const normalizedSymbol = testSymbol.trim().toUpperCase();
    if (!/^[A-Z0-9._-]{1,30}$/.test(normalizedSymbol)) { setQuickTestError("Enter a valid symbol using letters, numbers, dot, underscore, or hyphen."); return; }
    setQuickTestBusy(strategy.name); setQuickTestError(null);
    try {
      const result = await quickTestCustomStrategy(strategy, { symbol: normalizedSymbol, market: testMarket, timeframe: testTimeframe });
      setQuickTestResults((current) => ({ ...current, [strategy.name]: result }));
    } catch (cause) {
      if (cause instanceof StrategyQuickTestError && cause.status === 403) setQuickTestError("Your account cannot run strategy quick tests. Only admin or trader roles can perform this action.");
      else setQuickTestError(cause instanceof Error ? cause.message : "Quick test failed. Please retry.");
    } finally { setQuickTestBusy(null); }
  };
  return <>
    <section className="metrics-grid" aria-label="Strategy library summary" aria-busy={loading}>
      <article className="metric-card"><div className="metric-top"><span>Saved custom strategies</span><span className="metric-icon"><BookOpen size={17} /></span></div><div className="metric-value">{loading && !strategies.length ? "Loading…" : strategies.length}</div><div className="metric-foot"><span>Persisted definitions</span></div></article>
      <article className="metric-card"><div className="metric-top"><span>Available indicators</span><span className="metric-icon"><ShieldCheck size={17} /></span></div><div className="metric-value">{loading && !indicators.length ? "Loading…" : indicators.length}</div><div className="metric-foot"><span>From backend metadata</span></div></article>
    </section>
    {error && <div className="error-banner" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw size={15} /> Retry</button></div>}
    {quickTestError && <div className="error-banner" role="alert"><p>{quickTestError}</p><button className="button button-secondary" onClick={() => setQuickTestError(null)}>Dismiss</button></div>}
    {actionError && <div className="error-banner" role="alert"><p>{actionError}</p><button className="button button-secondary" onClick={() => setActionError(null)}>Dismiss</button></div>}
    {actionMessage && <p className="control-feedback control-success" role="status">{actionMessage}</p>}
    {canQuickTest ? <section className="panel" aria-labelledby="strategy-quick-test-config-title"><div className="panel-heading"><div><h2 id="strategy-quick-test-config-title">Quick-test configuration</h2><p>Runs a non-persisted simulation against up to the latest 1,000 stored candles. It does not create, activate, or promote a strategy.</p></div><span className="panel-counter">ADMIN / TRADER</span></div><div className="screener-control-grid"><label className="screener-control-field">Symbol<input aria-label="Quick-test symbol" value={testSymbol} maxLength={30} onChange={(event) => setTestSymbol(event.target.value)} /></label><label className="screener-control-field">Market<select aria-label="Quick-test market" value={testMarket} onChange={(event) => setTestMarket(event.target.value as "spot" | "futures")}><option value="spot">Spot</option><option value="futures">Futures</option></select></label><label className="screener-control-field">Timeframe<select aria-label="Quick-test timeframe" value={testTimeframe} onChange={(event) => setTestTimeframe(event.target.value)}>{["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div></section> : <p className="empty-state">Strategy quick tests are restricted to admin and trader roles. This session can review saved definitions only.</p>}
    {canManageStrategies && <section className="panel" aria-labelledby="strategy-create-title">
      <div className="panel-heading"><div><h2 id="strategy-create-title">Create a custom strategy</h2><p>Saving persists a rule definition to the backend strategy library. It does not activate or promote it.</p></div><button className="button button-secondary" type="button" disabled={actionBusy !== null} onClick={() => { setShowCreateForm((shown) => !shown); setActionError(null); setActionMessage(null); }}>{showCreateForm ? "Close editor" : "New strategy"}</button></div>
      {showCreateForm && <form className="screener-control-grid" onSubmit={(event) => { event.preventDefault(); void submitCreateStrategy(); }}>
        <label className="screener-control-field">Strategy name<input aria-label="Strategy name" value={createName} maxLength={80} required onChange={(event) => setCreateName(event.target.value)} placeholder="rsi_volume_v1" /></label>
        <label className="screener-control-field">Display name<input aria-label="Strategy display name" value={createDisplayName} required onChange={(event) => setCreateDisplayName(event.target.value)} placeholder="RSI + Volume" /></label>
        <label className="screener-control-field">Description<input aria-label="Strategy description" value={createDescription} onChange={(event) => setCreateDescription(event.target.value)} /></label>
        <label className="screener-control-field">Rationale<input aria-label="Strategy rationale" value={createRationale} onChange={(event) => setCreateRationale(event.target.value)} /></label>
        <div className="strategy-rule-grid">
          <section className="strategy-conditions"><div className="panel-heading"><div><h3>Long entry conditions</h3><p>All configured conditions are evaluated by the backend rule engine.</p></div><button className="button button-secondary" type="button" onClick={() => setLongConditions((current) => [...current, { indicator: indicators[0]?.name ?? "rsi", operator: "<", value: "30" }])}>Add condition</button></div>
            {longConditions.map((condition, index) => <div className="screener-control-grid" key={`long-${index}`}>
              <label className="screener-control-field">Indicator<select aria-label={`Long condition ${index + 1} indicator`} value={condition.indicator} onChange={(event) => setLongConditions((current) => current.map((item, i) => i === index ? { ...item, indicator: event.target.value } : item))}>{indicators.map((item) => <option key={item.name} value={item.name}>{item.label || item.name}</option>)}</select></label>
              <label className="screener-control-field">Operator<select aria-label={`Long condition ${index + 1} operator`} value={condition.operator} onChange={(event) => setLongConditions((current) => current.map((item, i) => i === index ? { ...item, operator: event.target.value } : item))}>{operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}</select></label>
              <label className="screener-control-field">Value or indicator<input aria-label={`Long condition ${index + 1} value`} value={condition.value} onChange={(event) => setLongConditions((current) => current.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} required /></label>
              <button className="button button-secondary" type="button" aria-label={`Remove long condition ${index + 1}`} disabled={longConditions.length <= 1} onClick={() => setLongConditions((current) => current.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
          </section>
          <section className="strategy-conditions"><div className="panel-heading"><div><h3>Short entry conditions</h3><p>Use indicator values or numeric thresholds.</p></div><button className="button button-secondary" type="button" onClick={() => setShortConditions((current) => [...current, { indicator: indicators[0]?.name ?? "rsi", operator: ">", value: "70" }])}>Add condition</button></div>
            {shortConditions.map((condition, index) => <div className="screener-control-grid" key={`short-${index}`}>
              <label className="screener-control-field">Indicator<select aria-label={`Short condition ${index + 1} indicator`} value={condition.indicator} onChange={(event) => setShortConditions((current) => current.map((item, i) => i === index ? { ...item, indicator: event.target.value } : item))}>{indicators.map((item) => <option key={item.name} value={item.name}>{item.label || item.name}</option>)}</select></label>
              <label className="screener-control-field">Operator<select aria-label={`Short condition ${index + 1} operator`} value={condition.operator} onChange={(event) => setShortConditions((current) => current.map((item, i) => i === index ? { ...item, operator: event.target.value } : item))}>{operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}</select></label>
              <label className="screener-control-field">Value or indicator<input aria-label={`Short condition ${index + 1} value`} value={condition.value} onChange={(event) => setShortConditions((current) => current.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} required /></label>
              <button className="button button-secondary" type="button" aria-label={`Remove short condition ${index + 1}`} disabled={shortConditions.length <= 1} onClick={() => setShortConditions((current) => current.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
          </section>
        </div>
        <label className="screener-control-field">Stop loss (ATR multiple)<input aria-label="Stop loss ATR multiple" type="number" min="0.5" max="5" step="0.25" value={slAtrMult} onChange={(event) => setSlAtrMult(event.target.value)} required /></label>
        <label className="screener-control-field">Risk/reward ratio<input aria-label="Risk reward ratio" type="number" min="1" max="6" step="0.5" value={riskRewardRatio} onChange={(event) => setRiskRewardRatio(event.target.value)} required /></label>
        <label className="screener-alert-toggle"><input type="checkbox" checked={useTrailingStop} onChange={(event) => setUseTrailingStop(event.target.checked)} /><span><strong>Use trailing stop</strong></span></label>
        <label className="screener-control-field">Trail activation (R)<input aria-label="Trail activation R" type="number" min="0.25" max="5" step="0.25" value={trailActivationR} onChange={(event) => setTrailActivationR(event.target.value)} required /></label>
        <label className="screener-control-field">Trail distance (ATR multiple)<input aria-label="Trail distance ATR multiple" type="number" min="0.5" max="5" step="0.25" value={trailDistanceAtrMult} onChange={(event) => setTrailDistanceAtrMult(event.target.value)} required /></label>
        <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={actionBusy !== null} onClick={() => setShowCreateForm(false)}>Cancel</button><button className="button button-primary" type="submit" disabled={actionBusy !== null || loading}>{actionBusy === "create" ? "Saving…" : "Save strategy"}</button></div>
      </form>}
    </section>}
    <section className="panel" aria-labelledby="strategy-library-title">
      <div className="panel-heading"><div><h2 id="strategy-library-title">Custom strategy library</h2><p>Saved rule definitions from the existing API. Creating or deleting a definition does not activate it.</p></div><button className="button button-secondary" onClick={() => setAttempt((n) => n + 1)} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
      {loading && !strategies.length && !error ? <p className="empty-state" role="status">Loading saved strategies…</p> : !strategies.length && !error ? <p className="empty-state">No saved custom strategies were returned by the API.</p> : <div className="strategy-library-list">{strategies.map((strategy) => <article className="strategy-library-card" key={strategy.name}>
        <div className="panel-heading"><div><h3>{strategy.displayName || strategy.name}</h3><p>{strategy.name}</p></div><span className="panel-counter">SAVED DEFINITION</span></div>
        {strategy.description && <p>{strategy.description}</p>}{strategy.rationale && <p className="strategy-rationale">{strategy.rationale}</p>}
        <div className="strategy-rule-grid"><Conditions label="Long entry conditions" items={strategy.longConditions} /><Conditions label="Short entry conditions" items={strategy.shortConditions} /></div>
        <div className="strategy-param-list"><span>Stop loss: {strategy.slAtrMult ?? "—"} ATR</span><span>Risk/reward: {strategy.riskRewardRatio ?? "—"}:1</span><span>Trailing stop: {strategy.useTrailingStop ? "Enabled" : "Disabled"}</span></div>{canQuickTest && <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={quickTestBusy !== null || loading} onClick={() => void runQuickTest(strategy)}>{quickTestBusy === strategy.name ? "Testing…" : "Quick test"}</button>{canManageStrategies && <button className="button button-secondary" type="button" disabled={actionBusy !== null || loading} onClick={() => { setActionError(null); setActionMessage(null); setPendingDelete(strategy); }}>Delete strategy</button>}</div>}{quickTestResults[strategy.name] && <section className="strategy-quick-test-result" aria-label={`Quick test result for ${strategy.name}`}><h4>Quick-test result · {quickTestResults[strategy.name].symbol} {quickTestResults[strategy.name].market} {quickTestResults[strategy.name].timeframe}</h4><div className="metrics-grid"><article className="metric-card"><div className="metric-top"><span>Candles</span></div><div className="metric-value">{quickTestResults[strategy.name].totalCandles}</div></article><article className="metric-card"><div className="metric-top"><span>Trades</span></div><div className="metric-value">{quickTestResults[strategy.name].totalTrades}</div></article><article className="metric-card"><div className="metric-top"><span>Win rate</span></div><div className="metric-value">{quickTestResults[strategy.name].winRatePct == null ? "—" : `${Number(quickTestResults[strategy.name].winRatePct).toFixed(2)}%`}</div></article><article className="metric-card"><div className="metric-top"><span>Net R</span></div><div className="metric-value">{quickTestResults[strategy.name].netR ?? "—"}</div></article><article className="metric-card"><div className="metric-top"><span>Profit factor</span></div><div className="metric-value">{quickTestResults[strategy.name].profitFactor ?? "—"}</div></article><article className="metric-card"><div className="metric-top"><span>Max drawdown</span></div><div className="metric-value">{quickTestResults[strategy.name].maxDrawdownR == null ? "—" : `${quickTestResults[strategy.name].maxDrawdownR} R`}</div></article><article className="metric-card"><div className="metric-top"><span>Expectancy</span></div><div className="metric-value">{quickTestResults[strategy.name].expectancyR ?? "—"}</div></article></div></section>}
      </article>)}</div>}
    </section>
    <section className="panel" aria-labelledby="indicator-catalog-title">
      <div className="panel-heading"><div><h2 id="indicator-catalog-title">Indicator catalog</h2><p>Definitions and descriptions provided by the backend.</p></div><span className="panel-counter">{indicators.length} INDICATORS</span></div>
      {loading && !indicators.length && !error ? <p className="empty-state" role="status">Loading indicator catalog…</p> : <div className="table-scroll"><table><thead><tr><th scope="col">Indicator</th><th scope="col">Category</th><th scope="col">Description</th><th scope="col">Default</th></tr></thead><tbody>{indicators.map((item) => <tr key={item.name}><td><strong>{item.label || item.name}</strong><div><code>{item.name}</code></div></td><td>{item.category || "—"}</td><td>{item.description || "—"}</td><td>{item.default ?? "—"}</td></tr>)}</tbody></table></div>}
    </section>
    <p className="page-description">Safety: admin/trader users can explicitly quick-test, create, and delete saved definitions. Quick tests are non-persisted; create/delete changes the saved strategy library but does not activate or promote a strategy. AI generation, promotion, and activation remain unavailable here.</p>
    {pendingDelete && <section className="panel" aria-labelledby="strategy-delete-confirm-title"><div className="panel-heading"><div><h2 id="strategy-delete-confirm-title">Delete strategy?</h2><p>This permanently removes the saved definition and its inverse registration. It does not delete historical backtest records.</p></div></div><p><strong>{pendingDelete.displayName || pendingDelete.name}</strong> ({pendingDelete.name})</p><div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={actionBusy !== null} onClick={() => setPendingDelete(null)}>Cancel</button><button className="button button-primary" type="button" disabled={actionBusy !== null} onClick={() => void confirmDeleteStrategy()}>{actionBusy === "delete" ? "Deleting…" : "Delete strategy"}</button></div></section>}
  </>;
}
