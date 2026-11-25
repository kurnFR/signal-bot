import { Check, ClipboardCheck } from "lucide-react";

const STEPS = [
  { name: "Check market data", detail: "Confirm candle coverage for your symbol, market, and timeframe.", views: ["Master Data"] },
  { name: "Review market context", detail: "Review the market, wallet flows, and news relevant to your setup.", views: ["Market Screener", "Liquidation Heatmap", "Wallet Tracking", "News AI", "Research"] },
  { name: "Select and validate a strategy", detail: "Choose a saved backtest run or an eligible ML filter as evidence.", views: ["Strategy Library", "Backtest Studio", "ML Studio"] },
  { name: "Review Telegram delivery", detail: "Check your destination in Settings if you plan to send alerts. You can leave alerts off in the deployment form.", views: ["Settings"] },
];

export const PAPER_READINESS_STEPS = STEPS.length;

interface PaperReadinessProps {
  completed: number;
  evidence: string | null;
  evidenceView: string;
  onComplete: () => void;
  onReset: () => void;
  onSelectView: (view: string) => void;
}

export function PaperReadiness({ completed, evidence, evidenceView, onComplete, onReset, onSelectView }: PaperReadinessProps) {
  const ready = completed === PAPER_READINESS_STEPS;
  return <section className="panel paper-readiness" aria-labelledby="paper-readiness-title">
    <div className="panel-heading">
      <div>
        <h2 id="paper-readiness-title"><ClipboardCheck size={17} aria-hidden="true" /> Paper trading readiness</h2>
        <p>Complete the review in order before activating a simulation in this workspace.</p>
      </div>
      <span className="status-pill status-neutral" role="status">{ready ? "Ready to deploy" : `${completed} of ${PAPER_READINESS_STEPS} complete`}</span>
    </div>
    <ol className="roadmap-list">
      {STEPS.map((step, index) => {
        const done = index < completed;
        const current = index === completed;
        return <li key={step.name} className={done ? "roadmap-complete" : current ? "roadmap-active" : ""}>
          <span className="roadmap-step" aria-hidden="true">{done ? <Check size={15} /> : index + 1}</span>
          <div>
            <strong>{step.name}</strong>
            <p>{step.detail}</p>
            {index === 2 && evidence && <p><strong>Selected evidence</strong> · {evidence}</p>}
            {current && <div className="readiness-links">
              {step.views.map((view) => <button key={view} type="button" className="button button-secondary button-sm" onClick={() => onSelectView(view)}>{view}</button>)}
            </div>}
          </div>
          {current ? <button type="button" className="button button-secondary button-sm" disabled={index === 2 && !evidence} onClick={onComplete}>Confirm review</button>
            : <span className="roadmap-state">{done ? "Complete" : "Pending"}</span>}
        </li>;
      })}
    </ol>
    {completed > 0 && <div className="readiness-links">
      {ready && <button type="button" className="button button-secondary button-sm" onClick={() => onSelectView(evidenceView)}>Return to selected evidence</button>}
      <button type="button" className="button button-secondary button-sm" onClick={onReset}>Reset review</button>
    </div>}
  </section>;
}
