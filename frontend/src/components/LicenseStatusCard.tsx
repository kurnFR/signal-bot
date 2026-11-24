import { KeyRound } from "lucide-react";
import type { LicenseStatus } from "../lib/systemStatus";

interface LicenseStatusCardProps {
  license: LicenseStatus | null;
  activeUserCount?: number;
}

export function LicenseStatusCard({ license, activeUserCount }: LicenseStatusCardProps) {
  if (!license) return null;

  const badgeLabel = license.valid
    ? license.mode === "development"
      ? "DEV MODE"
      : "ACTIVE"
    : license.mode.toUpperCase();

  const seatSummary =
    license.maxUsers == null
      ? activeUserCount != null
        ? `${activeUserCount} active (Unlimited)`
        : "Unlimited"
      : activeUserCount != null
      ? `${activeUserCount} / ${license.maxUsers} seats`
      : `Up to ${license.maxUsers} seats`;

  return (
    <section className="panel" aria-labelledby="settings-license-title">
      <div className="panel-heading">
        <div>
          <h2 id="settings-license-title">
            <KeyRound size={17} className="inline-icon" /> Commercial License & Plan
          </h2>
          <p>Server-verified subscription tier, active seat capacity, and expiration status.</p>
        </div>
        <span className="panel-counter">{badgeLabel}</span>
      </div>

      <div className="settings-telegram-status">
        <div>
          <span>Status</span>
          <strong>{license.valid ? "Valid" : "Attention Required"}</strong>
        </div>
        <div>
          <span>Tier</span>
          <strong style={{ textTransform: "capitalize" }}>{license.tier}</strong>
        </div>
        <div>
          <span>Licensee</span>
          <strong>{license.customer || "Unassigned"}</strong>
        </div>
        <div>
          <span>User Seats</span>
          <strong>{seatSummary}</strong>
        </div>
        <div>
          <span>Expires</span>
          <strong>{license.expiresAt ? new Date(license.expiresAt).toLocaleDateString() : "Perpetual / None"}</strong>
        </div>
      </div>

      {!license.valid && license.reason && (
        <div className="api-error" role="alert" style={{ marginTop: 12 }}>
          <div>
            <strong>License check failed</strong>
            <p>{license.reason}</p>
          </div>
        </div>
      )}
    </section>
  );
}
