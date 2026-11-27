import { useEffect, useState } from "react";
import { fetchBacktestStrategies, type BacktestStrategyParameter } from "../lib/backtestStudio";

export type DeploymentOverrides = Record<string, string | number | boolean>;

export function DeploymentParameters({ strategy, value, onChange, disabled }: {
  strategy: string;
  value: DeploymentOverrides;
  onChange: (value: DeploymentOverrides) => void;
  disabled: boolean;
}) {
  const [parameters, setParameters] = useState<BacktestStrategyParameter[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void fetchBacktestStrategies(controller.signal).then(({ strategies }) => {
      if (controller.signal.aborted) return;
      setParameters(strategies.find((item) => item.id === strategy)?.params ?? []);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [strategy]);

  function setOverride(key: string, next: string | number | boolean | undefined) {
    const overrides = { ...value };
    if (next === undefined) delete overrides[key];
    else overrides[key] = next;
    onChange(overrides);
  }

  return <fieldset disabled={disabled}>
    <legend>Strategy parameters</legend>
    {error ? <p role="alert">Could not load the strategy parameter schema.</p>
      : parameters === null ? <p role="status">Loading strategy parameters…</p>
      : parameters.length === 0 ? <p>No adjustable parameters in this strategy’s schema.</p>
      : <div className="paper-risk-grid">{parameters.map((parameter) => {
        const override = value[parameter.key];
        const factor = parameter.factor ?? 1;
        return <label className="screener-control-field" key={parameter.key}>{parameter.label}
          {parameter.type === "bool" ? <select value={override === undefined ? "" : String(override)}
            onChange={(event) => setOverride(parameter.key, event.target.value === "" ? undefined : event.target.value === "true")}>
            <option value="">Default{parameter.default === undefined ? "" : ` (${parameter.default ? "on" : "off"})`}</option>
            <option value="true">On</option><option value="false">Off</option>
          </select> : parameter.type === "string" ? <input value={String(override ?? "")}
            placeholder={String(parameter.default ?? "Default")}
            onChange={(event) => setOverride(parameter.key, event.target.value || undefined)} />
          : <input type="number" min={parameter.min} max={parameter.max}
            step={parameter.step ?? (parameter.type === "int" ? 1 : "any")}
            placeholder={String(parameter.default ?? "Default")}
            value={typeof override === "number" ? Number((override * factor).toPrecision(15)) : ""}
            onChange={(event) => setOverride(parameter.key, event.target.value === "" ? undefined : event.target.valueAsNumber / factor)} />}
        </label>;
      })}</div>}
    {Object.keys(value).length > 0 && <button type="button" className="button button-secondary"
      onClick={() => onChange({})}>Reset parameters to defaults</button>}
  </fieldset>;
}
