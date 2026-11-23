import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineStyle,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type UTCTimestamp,
} from "lightweight-charts";
import { getJson } from "../lib/api";
import { connectWebSocketChannel } from "../lib/ws";

export interface TradingChartProps {
  symbol: string;
  market?: "spot" | "futures";
  timeframe?: string;
  entryPrice?: number | null;
  stopLoss?: number | null;
  takeProfit?: number | null;
  markPrice?: number | null;
  fundingRate?: number | null;
  height?: number;
  showTimeframeSelector?: boolean;
  onTimeframeChange?: (tf: string) => void;
}

const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

export function TradingChart({
  symbol,
  market = "spot",
  timeframe: initialTimeframe = "1h",
  entryPrice,
  stopLoss,
  takeProfit,
  markPrice: propMarkPrice,
  fundingRate: propFundingRate,
  height = 420,
  showTimeframeSelector = true,
  onTimeframeChange,
}: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const [activeTimeframe, setActiveTimeframe] = useState(initialTimeframe);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [liveMarkPrice, setLiveMarkPrice] = useState<number | null>(propMarkPrice ?? null);
  const [liveFundingRate, setLiveFundingRate] = useState<number | null>(propFundingRate ?? null);

  const handleTimeframeClick = (tf: string) => {
    setActiveTimeframe(tf);
    onTimeframeChange?.(tf);
  };

  useEffect(() => {
    if (!containerRef.current) return;

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
      rightPriceScale: {
        borderColor: gridColor,
      },
      timeScale: {
        borderColor: gridColor,
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#3b82f6",
      priceFormat: {
        type: "volume",
      },
      priceScaleId: "",
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

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
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, [height]);

  // Load candles when symbol, market, or timeframe changes
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const loadData = async () => {
      try {
        const data = await getJson<{
          symbol: string;
          market: string;
          timeframe: string;
          candles: Array<{
            time: number;
            open: number;
            high: number;
            low: number;
            close: number;
            volume: number;
          }>;
          mark_price?: number | null;
          funding_rate?: number | null;
        }>(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}&timeframe=${encodeURIComponent(activeTimeframe)}&limit=250`);

        if (cancelled) return;

        if (data.mark_price != null) setLiveMarkPrice(data.mark_price);
        if (data.funding_rate != null) setLiveFundingRate(data.funding_rate);

        if (candleSeriesRef.current && volumeSeriesRef.current && data.candles?.length) {
          const formattedCandles: CandlestickData<UTCTimestamp>[] = data.candles.map((c) => ({
            time: c.time as UTCTimestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }));

          const formattedVolumes: HistogramData<UTCTimestamp>[] = data.candles.map((c) => ({
            time: c.time as UTCTimestamp,
            value: c.volume,
            color: c.close >= c.open ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)",
          }));

          candleSeriesRef.current.setData(formattedCandles);
          volumeSeriesRef.current.setData(formattedVolumes);
          chartRef.current?.timeScale().fitContent();

          // Render price lines if series exists
          const series = candleSeriesRef.current;
          if (entryPrice != null && Number.isFinite(entryPrice)) {
            series.createPriceLine({
              price: entryPrice,
              color: "#3b82f6",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: "Entry",
            });
          }
          if (stopLoss != null && Number.isFinite(stopLoss)) {
            series.createPriceLine({
              price: stopLoss,
              color: "#ef4444",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: "SL",
            });
          }
          if (takeProfit != null && Number.isFinite(takeProfit)) {
            series.createPriceLine({
              price: takeProfit,
              color: "#10b981",
              lineWidth: 2,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: "TP",
            });
          }
          const activeMark = data.mark_price ?? propMarkPrice;
          if (activeMark != null && Number.isFinite(activeMark)) {
            series.createPriceLine({
              price: activeMark,
              color: "#f59e0b",
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: true,
              title: "Mark",
            });
          }
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err.message || "Failed to load market data");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadData();

    // Connect WebSocket stream for real-time market updates
    const disconnectWs = connectWebSocketChannel("market", {
      subscribeSymbol: symbol,
      subscribeMarket: market,
      subscribeTimeframe: activeTimeframe,
      onMessage: (msg) => {
        if (msg.type === "market_snapshot" && msg.symbol === symbol.toUpperCase()) {
          if (msg.mark_price != null) setLiveMarkPrice(msg.mark_price);
          if (msg.funding_rate != null) setLiveFundingRate(msg.funding_rate);
        }
      },
    });

    return () => {
      cancelled = true;
      disconnectWs();
    };
  }, [symbol, market, activeTimeframe, entryPrice, stopLoss, takeProfit, propMarkPrice]);

  return (
    <div className="trading-chart-wrapper" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <div
        className="trading-chart-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "8px",
          padding: "6px 12px",
          backgroundColor: "var(--color-surface, #1e293b)",
          borderRadius: "8px",
          border: "1px solid var(--color-border, #334155)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <strong style={{ fontSize: "14px" }}>{symbol}</strong>
          <span className="notice-tag">{market.toUpperCase()}</span>
          {liveMarkPrice != null && (
            <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text)" }}>
              ${liveMarkPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
            </span>
          )}
          {liveFundingRate != null && (
            <span
              className={`status-pill ${liveFundingRate >= 0 ? "status-positive" : "status-warning"}`}
              title="Estimated 8-hour funding rate"
              style={{ fontSize: "11px" }}
            >
              8h Funding: {(liveFundingRate * 100).toFixed(4)}%
            </span>
          )}
        </div>

        {showTimeframeSelector && (
          <div style={{ display: "flex", gap: "4px" }}>
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                className={`button button-sm ${activeTimeframe === tf ? "button-primary" : "button-secondary"}`}
                style={{ padding: "3px 8px", fontSize: "11px", minHeight: "26px" }}
                onClick={() => handleTimeframeClick(tf)}
              >
                {tf}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={{ position: "relative", width: "100%", height }}>
        {loading && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "rgba(0,0,0,0.3)",
              zIndex: 10,
              color: "var(--color-muted)",
              fontSize: "12px",
            }}
          >
            Loading chart candles…
          </div>
        )}
        {error && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--color-negative, #ef4444)",
              fontSize: "12px",
            }}
          >
            {error}
          </div>
        )}
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      </div>
    </div>
  );
}
