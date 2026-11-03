import { useState, type FormEvent } from "react";
import { BrainCircuit, LoaderCircle, Play } from "lucide-react";
import { ApiError } from "./lib/api";
import {
  runMLGeneratorExperiment,
  type MLGeneratorExperimentResponse,
} from "./lib/mlStudio";

interface Props {
  onCompleted?: () => void;
}

export function MLGeneratorTrainingPanel({ onCompleted }: Props) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [market, setMarket] = useState<"spot" | "futures">("spot");
  const [timeframe, setTimeframe] = useState("1h");
  const [modelType, setModelType] = useState<"logistic_regression" | "random_forest" | "hist_gradient_boosting">("hist_gradient_boosting");
  const [horizon, setHorizon] = useState(12);
  const [barrier, setBarrier] = useState(1.5);
  const [feeRate, setFeeRate] = useState(0.001);
  const [slippageRate, setSlippageRate] = useState(0.0005);
  const [minimumTrades, setMinimumTrades] = useState(10);
  const [stopAtr, setStopAtr] = useState(1.5);
  const [riskReward, setRiskReward] = useState(2);
  const [maxHold, setMaxHold] = useState(72);
  const [result, setResult] = useState<MLGeneratorExperimentResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await runMLGeneratorExperiment({
        symbol,
        market,
        timeframe,
        model_type: modelType,
        horizon_bars: horizon,
        barrier_atr_multiple: barrier,
        fee_rate: feeRate,
        slippage_rate: slippageRate,
        min_net_return: 0,
        min_validation_trades: minimumTrades,
        stop_atr_multiple: stopAtr,
        risk_reward_ratio: riskReward,
        max_hold_bars: maxHold,
        seed: 42,
      });
      setResult(response);
      onCompleted?.();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setError("Your session expired. Sign in again before starting an ML experiment.");
      } else if (cause instanceof ApiError && cause.status === 403) {
        setError("Your account is not permitted to run ML experiments.");
      } else if (cause instanceof ApiError && cause.status === 422) {
        setError("The experiment could not satisfy the data, validation, or execution requirements. Try a longer history or different model settings.");
      } else {
        setError("Generator training failed. Check closed-candle feature availability and server diagnostics, then retry.");
      }
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel ml-generator-panel" aria-labelledby="ml-generator-title" aria-busy={busy}>
    <div className="panel-heading">
      <div>
        <h2 id="ml-generator-title"><BrainCircuit size={16} /> Train independent ML signal generator</h2>
        <p>Train a new model that creates LONG, SHORT, or HOLD decisions without relying on a technical strategy signal.</p>
      </div>
      <span className="panel-counter">RESEARCH ONLY</span>
    </div>
    <form onSubmit={submit}>
      <div className="ml-generator-form-grid">
        <label>Symbol<input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} required minLength={2} maxLength={20} pattern="[A-Z0-9]+" /></label>
        <label>Market<select value={market} onChange={(event) => setMarket(event.target.value as "spot" | "futures")}><option value="spot">Spot</option><option value="futures">Futures</option></select></label>
        <label>Timeframe<select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{["15m", "1h", "4h", "1d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Model<select value={modelType} onChange={(event) => setModelType(event.target.value as typeof modelType)}><option value="hist_gradient_boosting">Histogram Gradient Boosting</option><option value="random_forest">Random Forest</option><option value="logistic_regression">Logistic Regression</option></select></label>
        <label>Label horizon (bars)<input type="number" min={2} max={200} value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} required /></label>
        <label>ATR barrier multiple<input type="number" min={0.1} max={20} step={0.1} value={barrier} onChange={(event) => setBarrier(Number(event.target.value))} required /></label>
        <label>Fee rate<input type="number" min={0} max={0.099} step={0.0001} value={feeRate} onChange={(event) => setFeeRate(Number(event.target.value))} required /></label>
        <label>Slippage rate<input type="number" min={0} max={0.099} step={0.0001} value={slippageRate} onChange={(event) => setSlippageRate(Number(event.target.value))} required /></label>
        <label>Minimum validation trades<input type="number" min={1} max={500} value={minimumTrades} onChange={(event) => setMinimumTrades(Number(event.target.value))} required /></label>
        <label>Stop distance (ATR)<input type="number" min={0.1} max={20} step={0.1} value={stopAtr} onChange={(event) => setStopAtr(Number(event.target.value))} required /></label>
        <label>Risk / reward ratio<input type="number" min={0.1} max={20} step={0.1} value={riskReward} onChange={(event) => setRiskReward(Number(event.target.value))} required /></label>
        <label>Maximum holding bars<input type="number" min={1} max={1000} value={maxHold} onChange={(event) => setMaxHold(Number(event.target.value))} required /></label>
      </div>
      <div className="ml-generator-actions">
        <p>Uses closed candles, chronological purging, validation-only policy selection, and one untouched canonical-simulator test. Training does not activate paper trading or Telegram.</p>
        <button className="button button-primary" type="submit" disabled={busy || !symbol.trim()}>
          {busy ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}
          {busy ? "Training generator…" : "Train generator"}
        </button>
      </div>
    </form>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {result && <div className="ml-generator-result" role="status">
      <div><strong>{result.eligible_for_paper ? "Untouched test passed the paper eligibility gate" : "Saved as research-only"}</strong><p>{result.message}</p></div>
      <dl>
        <div><dt>Model ID</dt><dd><code>{result.model_id}</code></dd></div>
        <div><dt>Decision policy</dt><dd>{(result.decision_policy.min_confidence * 100).toFixed(1)}% confidence · {(result.decision_policy.min_directional_margin * 100).toFixed(1)}% directional margin</dd></div>
        <div><dt>Dataset rows</dt><dd>{result.dataset_rows.toLocaleString()}</dd></div>
        <div><dt>Train / validation / test</dt><dd>{result.train_rows.toLocaleString()} / {result.validation_rows.toLocaleString()} / {result.test_rows.toLocaleString()}</dd></div>
        <div><dt>Test trades</dt><dd>{typeof result.test_metrics.trades === "number" ? result.test_metrics.trades : "—"}</dd></div>
        <div><dt>Test profit factor</dt><dd>{typeof result.test_metrics.profit_factor === "number" ? result.test_metrics.profit_factor.toFixed(2) : "—"}</dd></div>
        <div><dt>Test max drawdown</dt><dd>{typeof result.test_metrics.max_drawdown_pct === "number" ? `${result.test_metrics.max_drawdown_pct.toFixed(2)}%` : "—"}</dd></div>
      </dl>
    </div>}
  </section>;
}
