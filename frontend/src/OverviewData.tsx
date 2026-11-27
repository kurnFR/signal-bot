import { useEffect, useState } from "react";
import { fetchBacktestHistory, type BacktestRunSummary } from "./lib/backtestStudio";
import { fetchDatasetCoverage, type CoverageSummary } from "./lib/masterData";

const metrics = [
  ["Candles", "totalCandles"],
  ["Datasets", "coverageEntries"],
  ["Symbols", "distinctSymbols"],
] as const;

export function OverviewData() {
  const [summary, setSummary] = useState<CoverageSummary | null>(null);
  const [runs, setRuns] = useState<BacktestRunSummary[] | null>(null);
  const [coverageError, setCoverageError] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    if (!summary) {
      void fetchDatasetCoverage(controller.signal).then(({ summary }) => {
        if (!controller.signal.aborted) setSummary(summary);
      }).catch(() => {
        if (!controller.signal.aborted) setCoverageError(true);
      });
    }
    if (!runs) {
      void fetchBacktestHistory(controller.signal, 10).then(({ runs }) => {
        if (!controller.signal.aborted) setRuns(runs);
      }).catch(() => {
        if (!controller.signal.aborted) setHistoryError(true);
      });
    }
    return () => controller.abort();
  }, [attempt]);

  function retry() {
    setCoverageError(false);
    setHistoryError(false);
    setAttempt((value) => value + 1);
  }

  return <>
    <section className="metrics-grid" aria-label="Dataset coverage" aria-busy={!summary && !coverageError}>
      {metrics.map(([label, key]) => <article key={key} className="metric-card">
        <div className="metric-top">{label}</div>
        <div className="metric-value">{summary ? summary[key].toLocaleString() : coverageError ? "Unavailable" : "Loading…"}</div>
      </article>)}
    </section>
    {coverageError && <p role="alert">Unable to load dataset coverage.</p>}
    <section className="panel" aria-labelledby="overview-backtests" aria-busy={!runs && !historyError}>
      <div className="panel-heading"><h2 id="overview-backtests">Recent backtests</h2></div>
      {historyError ? <p className="empty-state" role="alert">Unable to load recent backtests.</p>
        : !runs ? <p className="empty-state" role="status">Loading recent backtests…</p>
        : !runs.length ? <p className="empty-state">No backtests run yet.</p>
        : <div className="status-table-wrap">
          <table className="status-table">
            <thead><tr>{["Symbol", "Market / timeframe", "Strategy", "Trades", "Win rate", "Expectancy", "Created"].map((label) =>
              <th key={label} scope="col">{label}</th>)}</tr></thead>
            <tbody>{runs.map((run) => <tr key={run.id}>
              <td>{run.symbol}</td><td>{run.market} {run.timeframe}</td><td>{run.strategy}</td>
              <td>{run.totalTrades}</td><td>{run.winRatePct == null ? "—" : `${run.winRatePct}%`}</td>
              <td>{run.expectancyR == null ? "—" : `${run.expectancyR > 0 ? "+" : ""}${run.expectancyR}R`}</td>
              <td>{run.createdAt.slice(0, 16)}</td>
            </tr>)}</tbody>
          </table>
        </div>}
    </section>
    {(coverageError || historyError) && <button type="button" className="button button-secondary" onClick={retry}>Retry overview data</button>}
  </>;
}
