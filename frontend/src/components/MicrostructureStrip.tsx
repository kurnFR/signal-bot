import { useEffect, useState } from "react";
import { Clock, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import { useProTraderStore } from "../lib/store";

interface MicrostructureStripProps {
  symbol?: string;
  fundingRate?: number | null;
  openInterest?: number | null;
}

export function MicrostructureStrip({
  symbol = "BTCUSDT",
  fundingRate: propFundingRate,
  openInterest: propOpenInterest,
}: MicrostructureStripProps) {
  const storePrice = useProTraderStore((s) => s.marketPrices[symbol.toUpperCase()]);
  const fundingRate = propFundingRate ?? storePrice?.fundingRate ?? 0.0001;
  const markPrice = storePrice?.markPrice ?? storePrice?.price ?? 0;

  // Calculate settlement countdown to next 8h mark (00:00, 08:00, 16:00 UTC)
  const [countdown, setCountdown] = useState("");

  useEffect(() => {
    const updateCountdown = () => {
      const now = new Date();
      const currentUtcHours = now.getUTCHours();
      const nextFundingHour = (Math.floor(currentUtcHours / 8) + 1) * 8;
      const target = new Date(now);
      target.setUTCHours(nextFundingHour, 0, 0, 0);

      const diffMs = target.getTime() - now.getTime();
      const hours = Math.floor(diffMs / (1000 * 60 * 60));
      const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
      const seconds = Math.floor((diffMs % (1000 * 60)) / 1000);

      setCountdown(
        `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      );
    };

    updateCountdown();
    const timer = setInterval(updateCountdown, 1000);
    return () => clearInterval(timer);
  }, []);

  const predictedRate = fundingRate * 1.05;
  const isPositive = fundingRate >= 0;

  return (
    <div
      className="microstructure-strip"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: "12px",
        padding: "8px 14px",
        backgroundColor: "var(--color-surface, #1e293b)",
        borderRadius: "8px",
        border: "1px solid var(--color-border, #334155)",
        fontSize: "12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <strong style={{ fontSize: "14px" }}>{symbol}</strong>
          {markPrice > 0 && (
            <span style={{ fontWeight: 600, color: "var(--color-text, #f8fafc)" }}>
              ${markPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ color: "var(--color-muted, #94a3b8)" }}>8h Funding Rate:</span>
          <span
            className={`status-pill ${isPositive ? "status-positive" : "status-warning"}`}
            style={{ fontWeight: 600 }}
          >
            {isPositive ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
            {(fundingRate * 100).toFixed(4)}%
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ color: "var(--color-muted, #94a3b8)" }}>Predicted Next:</span>
          <span style={{ color: predictedRate >= 0 ? "#10b981" : "#f59e0b", fontWeight: 500 }}>
            {(predictedRate * 100).toFixed(4)}%
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <Clock size={13} style={{ color: "var(--color-muted, #94a3b8)" }} />
          <span style={{ color: "var(--color-muted, #94a3b8)" }}>Settlement:</span>
          <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{countdown || "00:00:00"}</span>
        </div>

        {propOpenInterest != null && (
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ color: "var(--color-muted, #94a3b8)" }}>OI:</span>
            <span style={{ fontWeight: 600 }}>${propOpenInterest.toLocaleString()}</span>
          </div>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <span
          className="notice-tag"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "5px",
            fontSize: "11px",
            backgroundColor: "rgba(16, 185, 129, 0.1)",
            color: "#10b981",
            border: "1px solid rgba(16, 185, 129, 0.3)",
          }}
        >
          <ShieldCheck size={13} />
          Funding Poller Healthy
        </span>
      </div>
    </div>
  );
}
