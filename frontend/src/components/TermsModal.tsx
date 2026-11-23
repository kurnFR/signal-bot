import { useState } from "react";
import { AlertCircle, ShieldCheck } from "lucide-react";
import { apiRequest } from "../lib/api";

interface TermsModalProps {
  isOpen: boolean;
  onAccepted: () => void;
}

export function TermsModal({ isOpen, onAccepted }: TermsModalProps) {
  const [agreed, setAgreed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleAccept = async () => {
    if (!agreed) return;
    setLoading(true);
    setError(null);
    try {
      await apiRequest("/api/auth/accept-terms", { method: "POST" });
      onAccepted();
    } catch (err: any) {
      setError(err.message || "Failed to record terms acknowledgment. Please retry.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(4px)",
        zIndex: 99999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
      }}
    >
      <div
        className="modal-container"
        style={{
          width: "100%",
          maxWidth: "520px",
          backgroundColor: "var(--color-surface, #1e293b)",
          borderRadius: "12px",
          border: "1px solid var(--color-border, #334155)",
          padding: "24px",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px" }}>
          <ShieldCheck size={24} color="#3b82f6" />
          <h2 style={{ fontSize: "18px", margin: 0, fontWeight: 700 }}>
            Legal Risk Disclaimer & Compliance Terms
          </h2>
        </div>

        <div
          style={{
            fontSize: "13px",
            lineHeight: 1.6,
            color: "var(--color-text)",
            backgroundColor: "rgba(0, 0, 0, 0.2)",
            padding: "16px",
            borderRadius: "8px",
            border: "1px solid var(--color-border, #334155)",
            marginBottom: "16px",
            maxHeight: "220px",
            overflowY: "auto",
          }}
        >
          <p style={{ marginTop: 0 }}>
            <strong>NOTICE & RISK ACKNOWLEDGMENT:</strong>
          </p>
          <p>
            Crypto Signal Bot is intended strictly for <strong>informational, research, and simulated/paper trading purposes</strong>.
            Nothing within this application constitutes financial, investment, legal, or tax advice.
          </p>
          <p>
            Cryptocurrency markets are subject to high market risk and extreme price volatility. Simulated results and historical backtests do not guarantee future performance. Live execution against exchange accounts is disabled by policy.
          </p>
          <p style={{ marginBottom: 0 }}>
            By continuing, you acknowledge that you are using this platform at your own discretion and agree to the platform Terms of Service.
          </p>
        </div>

        {error && (
          <div
            style={{
              padding: "10px 14px",
              marginBottom: "16px",
              backgroundColor: "rgba(239, 68, 68, 0.15)",
              border: "1px solid #ef4444",
              borderRadius: "6px",
              color: "#ef4444",
              fontSize: "12px",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <AlertCircle size={15} />
            <span>{error}</span>
          </div>
        )}

        <label
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "10px",
            fontSize: "13px",
            cursor: "pointer",
            marginBottom: "20px",
          }}
        >
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            style={{ marginTop: "2px" }}
          />
          <span>
            I understand that all signals, metrics, and paper trading runs are simulated and not financial advice. I accept the platform terms.
          </span>
        </label>

        <button
          type="button"
          className="button button-primary"
          style={{ width: "100%", padding: "10px", fontSize: "14px" }}
          onClick={handleAccept}
          disabled={!agreed || loading}
        >
          {loading ? "Recording Acknowledgment…" : "Acknowledge & Enter Workstation"}
        </button>
      </div>
    </div>
  );
}
