import { Moon, ShieldCheck, Sun, UserRound } from "lucide-react";
import type { ReactNode } from "react";

type Theme = "dark" | "light";
interface SettingsPanelProps {
  theme: Theme;
  onToggleTheme: () => void;
  username?: string;
  role?: string;
}

function PreferenceRow({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return <div className="settings-preference-row"><div className="settings-preference-icon" aria-hidden="true">{icon}</div><div className="settings-preference-copy"><strong>{title}</strong><p>{description}</p></div><div className="settings-preference-control">{children}</div></div>;
}

/** Only local UI preferences and safe session metadata; server configuration stays on the legacy dashboard. */
export function SettingsPanel({ theme, onToggleTheme, username, role }: SettingsPanelProps) {
  return <div className="settings-view">
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Workspace preferences</strong><p>This preview exposes local appearance preferences and basic session identity only. Server configuration, user administration, API credentials, and Telegram controls are not exposed here.</p></div><span className="panel-counter">SAFE PREVIEW</span></section>
    <section className="panel" aria-labelledby="settings-appearance-title">
      <div className="panel-heading"><div><h2 id="settings-appearance-title">Appearance</h2><p>Personalize the dashboard without changing backend configuration.</p></div><span className="panel-counter">LOCAL PREFERENCE</span></div>
      <PreferenceRow icon={theme === "dark" ? <Moon size={19} /> : <Sun size={19} />} title="Color theme" description={theme === "dark" ? "Dark theme is active. Your preference is saved in this browser when storage is available." : "Light theme is active. Your preference is saved in this browser when storage is available."}>
        <button className="button button-secondary" onClick={onToggleTheme} aria-label={"Switch to " + (theme === "dark" ? "light" : "dark") + " theme"}>Switch to {theme === "dark" ? "light" : "dark"}</button>
      </PreferenceRow>
    </section>
    <section className="panel" aria-labelledby="settings-session-title">
      <div className="panel-heading"><div><h2 id="settings-session-title">Current session</h2><p>Identity information returned by the existing authentication gate.</p></div><span className="panel-counter">READ ONLY</span></div>
      <PreferenceRow icon={<UserRound size={19} />} title="Signed-in user" description="User identity is provided by the server-side authentication flow; no token or credential is displayed.">
        <div className="settings-session-value"><strong>{username || "Preview workspace"}</strong><span>{role || "Unauthenticated preview"}</span></div>
      </PreferenceRow>
      <PreferenceRow icon={<ShieldCheck size={19} />} title="Access control" description="The server remains authoritative for every route and action. Hiding a control in this UI is not a replacement for backend authorization.">
        <span className="settings-readonly-tag">Server enforced</span>
      </PreferenceRow>
    </section>
    <section className="panel preview-placeholder" aria-labelledby="settings-not-migrated-title">
      <div className="panel-heading"><div><h2 id="settings-not-migrated-title">Administrative settings</h2><p>Intentionally not connected in this migration slice.</p></div><span className="panel-counter">LEGACY UI</span></div>
      <p className="empty-state">System connection details, user management, credential settings, Telegram testing, and other consequential controls remain in the legacy dashboard until their permissions and side effects are migrated and tested separately.</p>
    </section>
  </div>;
}
