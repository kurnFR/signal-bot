import { useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { TradingChart } from "./components/TradingChart";
import { MicrostructureStrip } from "./components/MicrostructureStrip";
import { ExecutionBlotter } from "./components/ExecutionBlotter";
import { PortfolioRiskDesk } from "./components/PortfolioRiskDesk";
import type { PaperOrder, PaperPosition, PaperTrade } from "./lib/paperTrading";

interface TradingDeskPanelProps {
  orders?: PaperOrder[];
  positions?: PaperPosition[];
  trades?: PaperTrade[];
  canTrade?: boolean;
  onCancelOrder?: (order: PaperOrder) => void;
  cancellingOrderId?: number | null;
}

export function TradingDeskPanel({
  orders = [],
  positions = [],
  trades = [],
  canTrade = false,
  onCancelOrder,
  cancellingOrderId,
}: TradingDeskPanelProps) {
  const [activeSymbol, setActiveSymbol] = useState("BTCUSDT");
  const [activeTimeframe, setActiveTimeframe] = useState("1h");

  return (
    <div
      className="trading-desk-container"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        height: "calc(100vh - 180px)",
        minHeight: "750px",
      }}
    >
      <MicrostructureStrip symbol={activeSymbol} />

      <Group orientation="vertical" style={{ flex: 1, minHeight: 0 }}>
        {/* Top Split: Chart on Left, Active Positions & Risk Desk on Right */}
        <Panel defaultSize={60} minSize={30}>
          <Group orientation="horizontal">
            <Panel defaultSize={65} minSize={40}>
              <div style={{ height: "100%", paddingRight: "6px" }}>
                <TradingChart
                  symbol={activeSymbol}
                  timeframe={activeTimeframe}
                  height={420}
                  onTimeframeChange={setActiveTimeframe}
                />
              </div>
            </Panel>

            <Separator
              style={{
                width: "4px",
                background: "var(--color-border, #334155)",
                cursor: "col-resize",
                borderRadius: "2px",
              }}
            />

            <Panel defaultSize={35} minSize={20}>
              <div
                style={{
                  height: "100%",
                  overflowY: "auto",
                  paddingLeft: "6px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "12px",
                }}
              >
                <section className="panel" style={{ margin: 0, padding: "12px" }}>
                  <div className="panel-heading" style={{ marginBottom: "8px" }}>
                    <h3 style={{ fontSize: "14px", margin: 0 }}>Active Positions</h3>
                    <span className="panel-counter">{positions.length} OPEN</span>
                  </div>
                  {positions.length ? (
                    <div className="status-table-wrap">
                      <table className="status-table">
                        <thead>
                          <tr>
                            <th>Symbol</th>
                            <th>Side</th>
                            <th>Entry</th>
                            <th>Unrealized %</th>
                          </tr>
                        </thead>
                        <tbody>
                          {positions.map((p) => (
                            <tr
                              key={p.id}
                              onClick={() => setActiveSymbol(p.symbol)}
                              style={{ cursor: "pointer" }}
                            >
                              <td><strong>{p.symbol}</strong></td>
                              <td style={{ color: p.direction === "LONG" ? "#10b981" : "#ef4444" }}>
                                {p.direction}
                              </td>
                              <td>${p.entry_price.toLocaleString()}</td>
                              <td style={{ color: (p.unrealized_pnl_pct ?? 0) >= 0 ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                                {p.unrealized_pnl_pct != null
                                  ? `${p.unrealized_pnl_pct >= 0 ? "+" : ""}${p.unrealized_pnl_pct.toFixed(2)}%`
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="empty-state" style={{ padding: "16px", fontSize: "12px" }}>
                      No open positions.
                    </p>
                  )}
                </section>

                <PortfolioRiskDesk positions={positions} trades={trades} />
              </div>
            </Panel>
          </Group>
        </Panel>

        <Separator
          style={{
            height: "4px",
            background: "var(--color-border, #334155)",
            cursor: "row-resize",
            margin: "4px 0",
            borderRadius: "2px",
          }}
        />

        {/* Bottom Split: Full Paginated Execution Blotter */}
        <Panel defaultSize={40} minSize={20}>
          <div
            style={{
              height: "100%",
              overflowY: "auto",
              backgroundColor: "var(--color-surface, #1e293b)",
              borderRadius: "8px",
              border: "1px solid var(--color-border, #334155)",
              padding: "12px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <h3 style={{ fontSize: "14px", margin: 0 }}>Execution & Order Blotter</h3>
              <span className="panel-counter">{orders.length} TOTAL</span>
            </div>
            <ExecutionBlotter
              orders={orders}
              canTrade={canTrade}
              onCancelOrder={onCancelOrder}
              cancellingOrderId={cancellingOrderId}
            />
          </div>
        </Panel>
      </Group>
    </div>
  );
}
