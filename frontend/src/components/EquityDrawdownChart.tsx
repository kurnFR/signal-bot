import { useEffect, useRef } from "react";
import {
  createChart,
  LineSeries,
  AreaSeries,
  ColorType,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";

export interface TradeItem {
  entryTimeWib?: string;
  exitTimeWib?: string;
  netReturnPct?: number;
  rMultiple?: number | null;
  [key: string]: any;
}

export interface EquityDrawdownChartProps {
  trades: TradeItem[];
  height?: number;
}

export function EquityDrawdownChart({ trades, height = 360 }: EquityDrawdownChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || !trades || trades.length === 0) return;

    const isLight = document.documentElement.dataset.theme === "light";
    const bgColor = isLight ? "#ffffff" : "#0d1117";
    const textColor = isLight ? "#334155" : "#94a3b8";
    const gridColor = isLight ? "#f1f5f9" : "#1e293b";

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth || 600,
      height,
      layout: {
        background: { type: ColorType.Solid, color: bgColor },
        textColor,
      },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      timeScale: {
        borderColor: gridColor,
        timeVisible: true,
      },
    });

    chartRef.current = chart;

    // Series 0: Cumulative Equity (R)
    const equitySeries = chart.addSeries(LineSeries, {
      color: "#10b981",
      lineWidth: 2,
      priceFormat: {
        type: "custom",
        formatter: (price: number) => `${price >= 0 ? "+" : ""}${price.toFixed(2)} R`,
      },
      title: "Cumulative R",
    }, 0);

    // Series 1: Underwater Drawdown (R) in bottom pane (paneIndex: 1)
    const drawdownSeries = chart.addSeries(AreaSeries, {
      topColor: "rgba(239, 68, 68, 0.4)",
      bottomColor: "rgba(239, 68, 68, 0.05)",
      lineColor: "#ef4444",
      lineWidth: 2,
      priceFormat: {
        type: "custom",
        formatter: (price: number) => `${price.toFixed(2)} R`,
      },
      title: "Drawdown",
    }, 1);

    // Compute data points
    let cumR = 0;
    let peakR = 0;
    let lastTime = 1600000000;

    const equityData: Array<{ time: UTCTimestamp; value: number }> = [];
    const drawdownData: Array<{ time: UTCTimestamp; value: number }> = [];

    // Parse base time
    const parsedBase = trades[0]?.entryTimeWib ? new Date(trades[0].entryTimeWib.replace(" ", "T")).getTime() / 1000 : 1600000000;
    if (Number.isFinite(parsedBase) && parsedBase > 0) {
      lastTime = Math.floor(parsedBase);
    }

    equityData.push({ time: (lastTime - 3600) as UTCTimestamp, value: 0 });
    drawdownData.push({ time: (lastTime - 3600) as UTCTimestamp, value: 0 });

    for (let i = 0; i < trades.length; i++) {
      const trade = trades[i];
      let t = 0;
      if (trade.exitTimeWib) {
        t = Math.floor(new Date(trade.exitTimeWib.replace(" ", "T")).getTime() / 1000);
      } else if (trade.entryTimeWib) {
        t = Math.floor(new Date(trade.entryTimeWib.replace(" ", "T")).getTime() / 1000);
      }
      if (!Number.isFinite(t) || t <= lastTime) {
        t = lastTime + 3600;
      }
      lastTime = t;

      const r = trade.rMultiple != null && Number.isFinite(trade.rMultiple)
        ? trade.rMultiple
        : (trade.netReturnPct != null && Number.isFinite(trade.netReturnPct) ? trade.netReturnPct / 2.0 : 0);

      cumR += r;
      if (cumR > peakR) peakR = cumR;
      const dd = cumR - peakR;

      equityData.push({ time: t as UTCTimestamp, value: parseFloat(cumR.toFixed(3)) });
      drawdownData.push({ time: t as UTCTimestamp, value: parseFloat(dd.toFixed(3)) });
    }

    equitySeries.setData(equityData);
    drawdownSeries.setData(drawdownData);
    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
        });
      }
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [trades, height]);

  if (!trades || trades.length === 0) {
    return (
      <div style={{ padding: "20px", textAlign: "center", color: "var(--color-muted)" }}>
        No closed trades available to construct equity and drawdown curves.
      </div>
    );
  }

  return (
    <div className="equity-chart-container" style={{ width: "100%", marginTop: "12px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "8px",
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--color-muted)" }}>
          EQUITY CURVE &amp; UNDERWATER DRAWDOWN ({trades.length} TRADES)
        </span>
      </div>
      <div ref={containerRef} style={{ width: "100%", height }} />
    </div>
  );
}
