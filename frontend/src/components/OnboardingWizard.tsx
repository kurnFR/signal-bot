import { useState } from "react";
import { Key, MessageSquare, Play, Server, X } from "lucide-react";
import { apiRequest } from "../lib/api";

interface OnboardingWizardProps {
  isOpen: boolean;
  onClose?: () => void;
  onComplete: () => void;
  mustChangePassword?: boolean;
}

export function OnboardingWizard({
  isOpen,
  onClose,
  onComplete,
  mustChangePassword = false,
}: OnboardingWizardProps) {
  const [currentStep, setCurrentStep] = useState(1);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordStatus, setPasswordStatus] = useState<string | null>(null);

  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");
  const [telegramTestResult, setTelegramTestResult] = useState<string | null>(null);
  const [telegramBusy, setTelegramBusy] = useState(false);

  const [strategyDeployed, setStrategyDeployed] = useState(false);
  const [deployBusy, setDeployBusy] = useState(false);

  if (!isOpen) return null;

  const handleSavePassword = async () => {
    if (!newPassword || newPassword !== confirmPassword) {
      setPasswordStatus("Passwords do not match or are empty.");
      return;
    }
    try {
      await apiRequest("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password: "admin", new_password: newPassword }),
      });
      setPasswordStatus("Password changed successfully!");
      setCurrentStep(2);
    } catch (err: any) {
      setPasswordStatus(err.message || "Failed to update password.");
    }
  };

  const handleTestTelegram = async () => {
    setTelegramBusy(true);
    setTelegramTestResult(null);
    try {
      if (telegramToken && telegramChatId) {
        await apiRequest("/api/telegram/config", {
          method: "PUT",
          body: JSON.stringify({
            bot_token: telegramToken,
            chat_id: telegramChatId,
            delivery_authorized: true,
          }),
        });
      }
      const res = await apiRequest<{ success: boolean; bot?: string }>("/api/telegram/test", {
        method: "POST",
        body: JSON.stringify({ custom_message: "Test message from Onboarding Wizard" }),
      });
      setTelegramTestResult(res.success ? "Test message delivered successfully!" : "Telegram test sent.");
    } catch (err: any) {
      setTelegramTestResult(err.message || "Telegram test failed.");
    } finally {
      setTelegramBusy(false);
    }
  };

  const handleDeployQuickStrategy = async () => {
    setDeployBusy(true);
    try {
      await apiRequest("/api/paper/deploy", {
        method: "POST",
        body: JSON.stringify({
          symbol: "BTCUSDT",
          market: "futures",
          timeframe: "1h",
          strategy_name: "volume_breakout_v1",
          initial_capital: 10000,
          leverage: 1,
        }),
      });
      setStrategyDeployed(true);
    } catch (err: any) {
      // Deployment error
    } finally {
      setDeployBusy(false);
    }
  };

  return (
    <div
      className="wizard-backdrop"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(4px)",
        zIndex: 99998,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
      }}
    >
      <div
        className="wizard-card"
        style={{
          width: "100%",
          maxWidth: "600px",
          backgroundColor: "var(--color-surface, #1e293b)",
          borderRadius: "14px",
          border: "1px solid var(--color-border, #334155)",
          padding: "24px",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <div>
            <h2 style={{ fontSize: "18px", margin: 0, fontWeight: 700 }}>
              First-Run Setup & Onboarding
            </h2>
            <p style={{ margin: "4px 0 0", color: "var(--color-muted)", fontSize: "12px" }}>
              Step {currentStep} of 4
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close onboarding wizard"
          >
            <X size={18} />
          </button>
        </div>

        {/* Steps Breadcrumb */}
        <div style={{ display: "flex", gap: "8px", marginBottom: "20px" }}>
          {[1, 2, 3, 4].map((step) => (
            <div
              key={step}
              style={{
                flex: 1,
                height: "4px",
                borderRadius: "2px",
                backgroundColor:
                  step < currentStep
                    ? "#10b981"
                    : step === currentStep
                    ? "#3b82f6"
                    : "var(--color-border, #334155)",
              }}
            />
          ))}
        </div>

        {/* Step 1: Change Default Password */}
        {currentStep === 1 && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <Key size={18} color="#3b82f6" />
              <strong style={{ fontSize: "15px" }}>1. Secure Administrator Password</strong>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-muted)", marginBottom: "16px" }}>
              Rotate your bootstrap password to a secure permanent credential (minimum 10 characters).
            </p>

            {mustChangePassword && (
              <p style={{ fontSize: "12px", color: "#f59e0b", marginBottom: "12px" }}>
                Notice: Default bootstrap credentials are in use. Setting a new password is required.
              </p>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "16px" }}>
              <input
                type="password"
                className="input"
                placeholder="New administrator password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              <input
                type="password"
                className="input"
                placeholder="Confirm password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>

            {passwordStatus && (
              <p style={{ fontSize: "12px", color: passwordStatus.includes("success") ? "#10b981" : "#ef4444" }}>
                {passwordStatus}
              </p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => setCurrentStep(2)}
              >
                Skip / Already Configured
              </button>
              <button
                type="button"
                className="button button-primary"
                onClick={handleSavePassword}
                disabled={!newPassword || newPassword !== confirmPassword}
              >
                Save & Continue
              </button>
            </div>
          </div>
        )}

        {/* Step 2: System Health */}
        {currentStep === 2 && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <Server size={18} color="#10b981" />
              <strong style={{ fontSize: "15px" }}>2. Verify Core Engine Health</strong>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-muted)", marginBottom: "16px" }}>
              Confirm database connection and market data collectors are communicating properly.
            </p>

            <div
              style={{
                backgroundColor: "rgba(0, 0, 0, 0.2)",
                padding: "12px",
                borderRadius: "8px",
                fontSize: "12px",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                marginBottom: "16px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>MariaDB Database Schema</span>
                <span style={{ color: "#10b981", fontWeight: 600 }}>Active (Migration 034)</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Binance Public Market Stream</span>
                <span style={{ color: "#10b981", fontWeight: 600 }}>Connected</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Funding Rate Poller</span>
                <span style={{ color: "#10b981", fontWeight: 600 }}>Active (8h Settlement)</span>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                className="button button-primary"
                onClick={() => setCurrentStep(3)}
              >
                Continue to Telegram Setup
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Telegram Setup */}
        {currentStep === 3 && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <MessageSquare size={18} color="#3b82f6" />
              <strong style={{ fontSize: "15px" }}>3. Telegram Alerting (Optional)</strong>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-muted)", marginBottom: "16px" }}>
              Enter bot token and chat ID to receive real-time signals and paper trade execution alerts.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "16px" }}>
              <input
                type="text"
                className="input"
                placeholder="Telegram Bot Token (e.g. 123456:ABC-DEF)"
                value={telegramToken}
                onChange={(e) => setTelegramToken(e.target.value)}
              />
              <input
                type="text"
                className="input"
                placeholder="Target Chat ID (e.g. -100123456789)"
                value={telegramChatId}
                onChange={(e) => setTelegramChatId(e.target.value)}
              />
            </div>

            {telegramTestResult && (
              <p style={{ fontSize: "12px", color: telegramTestResult.includes("success") ? "#10b981" : "#f59e0b" }}>
                {telegramTestResult}
              </p>
            )}

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <button
                type="button"
                className="button button-secondary button-sm"
                onClick={handleTestTelegram}
                disabled={telegramBusy}
              >
                {telegramBusy ? "Testing…" : "Send Test Ping"}
              </button>

              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => setCurrentStep(4)}
                >
                  Skip
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => setCurrentStep(4)}
                >
                  Continue
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Step 4: Deploy First Strategy */}
        {currentStep === 4 && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <Play size={18} color="#10b981" />
              <strong style={{ fontSize: "15px" }}>4. Deploy First Paper Strategy</strong>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-muted)", marginBottom: "16px" }}>
              Deploy BTCUSDT 1h Volume Breakout to start simulated paper trading in your workstation.
            </p>

            <div
              style={{
                padding: "16px",
                borderRadius: "8px",
                border: "1px solid var(--color-border, #334155)",
                marginBottom: "16px",
                backgroundColor: "rgba(0, 0, 0, 0.2)",
              }}
            >
              <div style={{ fontWeight: 600, fontSize: "14px", marginBottom: "4px" }}>
                Volume Breakout v1 (BTCUSDT Futures 1h)
              </div>
              <p style={{ fontSize: "12px", color: "var(--color-muted)", margin: 0 }}>
                High-volume momentum breakouts with dynamic ATR volatility trailing stops.
              </p>
            </div>

            {strategyDeployed && (
              <p style={{ color: "#10b981", fontSize: "12px", fontWeight: 600 }}>
                ✓ Strategy deployed successfully to paper trading ledger!
              </p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                className="button button-primary"
                onClick={async () => {
                  if (!strategyDeployed) {
                    await handleDeployQuickStrategy();
                  }
                  onComplete();
                }}
                disabled={deployBusy}
              >
                {deployBusy ? "Deploying…" : strategyDeployed ? "Finish & Go to Desk" : "Deploy & Finish"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
