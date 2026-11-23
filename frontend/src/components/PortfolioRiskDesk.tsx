import { useMemo } from "react";
import { PieChart, ShieldAlert } from "lucide-react";
import type { PaperPosition, PaperTrade } from "../lib/paperTrading";

interface PortfolioRiskDeskProps {
  positions: PaperPosition[];
  trades: PaperTrade[];
  circuitBreakerTripped?: boolean;
  maxDailyLossUsd?: number;
}

export function PortfolioRiskDesk({
  positions,
  trades,
  circuitBreakerTripped = false,
  maxDailyLossUsd = 1000,
}: PortfolioRiskDeskProps) {
  // Aggregate Long / Short Notional & Symbol Exposure
  const exposure = useMemo(() => {
    let longNotional = 0;
    let shortNotional = 0;
    const bySymbol: Record<string, { long: number; short: number }> = {};

    for (const p of positions) {
      const notional = Math.abs(p.quantity * (p.current_price || p.entry_price));
      const sym = p.symbol.toUpperCase();
      if (!bySymbol[sym]) bySymbol[sym] = { long: 0, short: 0 };

      if (p.direction === "LONG") {
        longNotional += notional;
        bySymbol[sym].long += notional;
      } else {
        shortNotional += notional;
        bySymbol[sym].short += notional;
      }
    }

    const gross = longNotional + shortNotional;
    const net = longNotional - shortNotional;

    return { longNotional, shortNotional, gross, net, bySymbol };
  }, [positions]);

  // Aggregate daily realized PnL and proximity to breaker
  const dailyMetrics = useMemo(() => {
    const now = new Date();
    const todayMidnight = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).getTime();

    let todayRealizedPnl = 0;
    for (const t of trades) {
      const exitTime = new Date(t.exit_time_wib).getTime();
      if (!isNaN(exitTime) && exitTime >= todayMidnight) {
        todayRealizedPnl += t.net_pnl;
      }
    }

    const dailyLossProximity =
      todayRealizedPnl < 0 ? Math.min(100, (Math.abs(todayRealizedPnl) / maxDailyLossUsd) * 100) : 0;

    return { todayRealizedPnl, dailyLossProximity };
  }, [trades, maxDailyLossUsd]);

  // Strategy performance attribution
  const strategyStats = useMemo(() => {
    const stats: Record<string, { wins: number; total: number; pnl: number }> = {};
    for (const t of trades) {
      const s = t.strategy_name || "Manual";
      if (!stats[s]) stats[s] = { wins: 0, total: 0, pnl: 0 };
      stats[s].total += 1;
      stats[s].pnl += t.net_pnl;
      if (t.net_pnl > 0) stats[s].wins += 1;
    }
    return Object.entries(stats).map(([strategy, data]) => ({
      strategy,
      winRate: data.total > 0 ? (data.wins / data.total) * 100 : 0,
      totalTrades: data.total,
      netPnl: data.pnl,
    }));
  }, [trades]);

  return (
    <section className="panel" aria-labelledby="portfolio-risk-title">
      <div className="panel-heading">
        <div>
          <h2 id="portfolio-risk-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <PieChart size={18} /> Portfolio Risk & Exposure Desk
          </h2>
          <p>Long/Short directional exposure, daily loss proximity, and per-strategy attribution.</p>
        </div>
        {circuitBreakerTripped && (
          <span
            className="status-pill status-negative"
            style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <ShieldAlert size={13} /> Breaker Tripped
          </span>
        )}
      </div>

      <div
        className="risk-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "12px",
          marginBottom: "16px",
        }}
      >
        <div className="card" style={{ padding: "12px" }}>
          <div style={{ fontSize: "11px", color: "var(--color-muted)", textTransform: "uppercase" }}>
            Gross Notional Exposure
          </div>
          <div style={{ fontSize: "18px", fontWeight: 700, margin: "4px 0" }}>
            ${exposure.gross.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-muted)" }}>
            Long: ${exposure.longNotional.toFixed(0)} | Short: ${exposure.shortNotional.toFixed(0)}
          </div>
        </div>

        <div className="card" style={{ padding: "12px" }}>
          <div style={{ fontSize: "11px", color: "var(--color-muted)", textTransform: "uppercase" }}>
            Net Directional Bias
          </div>
          <div
            style={{
              fontSize: "18px",
              fontWeight: 700,
              margin: "4px 0",
              color: exposure.net >= 0 ? "#10b981" : "#ef4444",
            }}
          >
            {exposure.net >= 0 ? "+" : ""}${exposure.net.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "12px", color: "var(--color-muted)" }}>
            {exposure.net >= 0 ? "Net Long Bias" : "Net Short Bias"}
          </div>
        </div>

        <div className="card" style={{ padding: "12px" }}>
          <div style={{ fontSize: "11px", color: "var(--color-muted)", textTransform: "uppercase" }}>
            Daily Breaker Loss Proximity
          </div>
          <div style={{ fontSize: "18px", fontWeight: 700, margin: "4px 0" }}>
            {dailyMetrics.dailyLossProximity.toFixed(1)}%
          </div>
          <div
            style={{
              width: "100%",
              height: "6px",
              background: "var(--color-border, #334155)",
              borderRadius: "3px",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${dailyMetrics.dailyLossProximity}%`,
                height: "100%",
                background:
                  dailyMetrics.dailyLossProximity > 75
                    ? "#ef4444"
                    : dailyMetrics.dailyLossProximity > 50
                    ? "#f59e0b"
                    : "#10b981",
              }}
            />
          </div>
        </div>
      </div>

      {strategyStats.length > 0 && (
        <div className="status-table-wrap">
          <table className="status-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Trades</th>
                <th>Win Rate</th>
                <th>Net PnL</th>
              </tr>
            </thead>
            <tbody>
              {strategyStats.map((s) => (
                <tr key={s.strategy}>
                  <td><strong>{s.strategy}</strong></td>
                  <td>{s.totalTrades}</td>
                  <td>{s.winRate.toFixed(1)}%</td>
                  <td style={{ color: s.netPnl >= 0 ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                    {s.netPnl >= 0 ? "+" : ""}${s.netPnl.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
