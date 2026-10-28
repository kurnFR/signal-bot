import { MessageCircle, Moon, RefreshCw, ShieldCheck, Sun, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "./lib/api";
import { fetchTelegramStatus, sendTelegramTestPing, TelegramActionError, type TelegramStatus } from "./lib/telegram";
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
  const isAdmin = (role ?? "").toLowerCase() === "admin";
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null);
  const [telegramLoading, setTelegramLoading] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [telegramRefreshAttempt, setTelegramRefreshAttempt] = useState(0);
  const [confirmTelegramTest, setConfirmTelegramTest] = useState(false);
  const [telegramTestBusy, setTelegramTestBusy] = useState(false);
  const [telegramTestError, setTelegramTestError] = useState<string | null>(null);
  const [telegramTestMessage, setTelegramTestMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) {
      setTelegramStatus(null);
      setTelegramLoading(false);
      setTelegramError(null);
      return;
    }
    const controller = new AbortController();
    setTelegramLoading(true);
    setTelegramError(null);
    fetchTelegramStatus(controller.signal).then((status) => {
      if (!controller.signal.aborted) setTelegramStatus(status);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setTelegramStatus(null);
        if (cause instanceof ApiError && cause.status === 401) setTelegramError("Your session has expired. Sign in again to view Telegram status.");
        else if (cause instanceof ApiError && cause.status === 403) setTelegramError("Your account cannot view Telegram status.");
        else setTelegramError("Telegram status could not be loaded. Check the server configuration, then retry.");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setTelegramLoading(false);
    });
    return () => controller.abort();
  }, [isAdmin, telegramRefreshAttempt]);

  const runTelegramTest = async () => {
    if (!isAdmin || !confirmTelegramTest || !telegramStatus?.hasToken) return;
    setTelegramTestBusy(true); setTelegramTestError(null); setTelegramTestMessage(null);
    try {
      const result = await sendTelegramTestPing();
      setConfirmTelegramTest(false);
      if (result.success) {
        setTelegramTestMessage(result.chat_id
          ? `Test ping sent to the configured Telegram chat${result.bot?.username ? ` via @${result.bot.username}` : ""}.`
          : result.note ?? "Bot token verified. No message was sent because a chat ID is not configured.");
      } else {
        setTelegramTestError(result.error || "Telegram test failed. Check bot and chat configuration.");
      }
    } catch (cause) {
      if (cause instanceof TelegramActionError && cause.status === 403) setTelegramTestError("Only admins can send a Telegram test ping.");
      else if (cause instanceof TelegramActionError && cause.status === 401) setTelegramTestError("Your session has expired. Sign in again before testing Telegram.");
      else setTelegramTestError(cause instanceof Error ? cause.message : "Telegram test failed. Please retry.");
    } finally { setTelegramTestBusy(false); }
  };
  return <div className="settings-view">
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Workspace preferences</strong><p>This preview exposes local appearance preferences, basic session identity, and an admin-only Telegram test control. Server configuration, user administration, and API credentials remain hidden; the bot token is never displayed.</p></div><span className="panel-counter">SAFE PREVIEW</span></section>
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
    <section className="panel" aria-labelledby="settings-telegram-title" aria-busy={telegramLoading || telegramTestBusy}>
      <div className="panel-heading"><div><h2 id="settings-telegram-title"><MessageCircle size={17} className="inline-icon" /> Telegram status and test</h2><p>Read-only configuration status and an explicit test-ping workflow. A confirmed test may send a real Telegram message.</p></div><span className="panel-counter">{isAdmin ? "ADMIN ONLY" : "RESTRICTED"}</span></div>
      {!isAdmin ? <p className="empty-state">Telegram status and test controls are restricted to admins. This role does not fetch or display the configured chat ID.</p> : <>
        {telegramError && <div className="api-error" role="alert"><div><strong>Telegram status unavailable</strong><p>{telegramError}</p></div><button className="button button-secondary" type="button" onClick={() => setTelegramRefreshAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
        {telegramLoading && <p className="empty-state">Loading Telegram status…</p>}
        {telegramStatus && <div className="settings-telegram-status">
          <div><span>Configuration</span><strong>{telegramStatus.configured ? "Configured" : "Incomplete"}</strong></div>
          <div><span>Bot token</span><strong>{telegramStatus.hasToken ? "Present (hidden)" : "Missing"}</strong></div>
          <div><span>Target chat ID</span><strong>{telegramStatus.chatId || "Not configured"}</strong></div>
          <button className="button button-secondary" type="button" disabled={telegramLoading || telegramTestBusy} onClick={() => setTelegramRefreshAttempt((value) => value + 1)}><RefreshCw size={15} /> Refresh status</button>
        </div>}
        {telegramTestError && <div className="api-error" role="alert"><div><strong>Telegram test failed</strong><p>{telegramTestError}</p></div><button className="button button-secondary" type="button" onClick={() => setTelegramTestError(null)}>Dismiss</button></div>}
        {telegramTestMessage && <p className="control-feedback control-success" role="status">{telegramTestMessage}</p>}
        <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={telegramLoading || telegramTestBusy || !telegramStatus?.hasToken} onClick={() => { setConfirmTelegramTest(true); setTelegramTestError(null); setTelegramTestMessage(null); }}>Review Telegram test</button></div>
        {confirmTelegramTest && <section className="panel" aria-labelledby="settings-telegram-confirm-title"><div className="panel-heading"><div><h3 id="settings-telegram-confirm-title">Confirm Telegram test ping</h3><p>{telegramStatus?.chatId ? "This sends a real test message to the configured Telegram chat. Continue only if you intend to send that message." : "No chat ID is configured. This will verify the bot token but should not send a message."}</p></div></div><div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={telegramTestBusy} onClick={() => setConfirmTelegramTest(false)}>Cancel test</button><button className="button button-primary" type="button" disabled={telegramTestBusy || !telegramStatus?.hasToken} onClick={() => void runTelegramTest()}>{telegramTestBusy ? "Testing…" : "Send test ping"}</button></div></section>}
      </>}
    </section>
    <section className="panel preview-placeholder" aria-labelledby="settings-not-migrated-title">
      <div className="panel-heading"><div><h2 id="settings-not-migrated-title">Administrative settings</h2><p>Intentionally not connected in this migration slice.</p></div><span className="panel-counter">LEGACY UI</span></div>
      <p className="empty-state">System connection details, user management, credential settings, and other consequential controls remain in the legacy dashboard until their permissions and side effects are migrated and tested separately.</p>
    </section>
  </div>;
}
