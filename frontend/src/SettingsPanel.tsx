import { Key, MessageCircle, Moon, Plus, RefreshCw, ShieldCheck, Sun, Trash2, UserPlus, UserRound, Users } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, apiRequest } from "./lib/api";
import { changePassword, AuthRequestError } from "./lib/auth";
import { fetchTelegramStatus, sendTelegramTestPing, TelegramActionError, type TelegramStatus } from "./lib/telegram";
import { fetchAdminSystemStatus, type AdminSystemStatusResponse } from "./lib/systemStatus";
import { AuditLogsSection } from "./components/AuditLogsSection";
import { ServicesSection } from "./components/ServicesSection";
import { LicenseStatusCard } from "./components/LicenseStatusCard";
import { RetailBot2ArchiveSection } from "./components/RetailBot2ArchiveSection";
import type { LicenseStatus } from "./lib/systemStatus";
import {
  createUser,
  deleteUser,
  fetchUsers,
  resetUserPassword,
  updateUser,
  UserActionError,
  type ManagedUser,
} from "./lib/users";
import type { ReactNode } from "react";

type Theme = "dark" | "light";
interface SettingsPanelProps {
  theme: Theme;
  onToggleTheme: () => void;
  username?: string;
  role?: string;
  license?: LicenseStatus | null;
}

function trapFocus(e: React.KeyboardEvent<HTMLDialogElement>) {
  if (e.key !== "Tab") return;
  const dialog = e.currentTarget;
  const focusable = dialog.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  );
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey) {
    if (document.activeElement === first) {
      e.preventDefault();
      last.focus();
    }
  } else {
    if (document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}

function PreferenceRow({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return <div className="settings-preference-row"><div className="settings-preference-icon" aria-hidden="true">{icon}</div><div className="settings-preference-copy"><strong>{title}</strong><p>{description}</p></div><div className="settings-preference-control">{children}</div></div>;
}

/** Settings & Users panel supporting appearance, profile, password change, and full admin user management. */
export function SettingsPanel({ theme, onToggleTheme, username, role, license }: SettingsPanelProps) {
  const isAdmin = (role ?? "").toLowerCase() === "admin";

  // Appearance & Session
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null);
  const [telegramLoading, setTelegramLoading] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [telegramRefreshAttempt, setTelegramRefreshAttempt] = useState(0);
  const [confirmTelegramTest, setConfirmTelegramTest] = useState(false);
  const [telegramCustomMessage, setTelegramCustomMessage] = useState("");
  const [telegramTestBusy, setTelegramTestBusy] = useState(false);
  const [telegramTestError, setTelegramTestError] = useState<string | null>(null);
  const [telegramTestMessage, setTelegramTestMessage] = useState<string | null>(null);
  const [editTelegramToken, setEditTelegramToken] = useState("");
  const [editTelegramChatId, setEditTelegramChatId] = useState("");
  const [editTelegramDelivery, setEditTelegramDelivery] = useState(false);
  const [savingTelegram, setSavingTelegram] = useState(false);
  const [saveTelegramSuccess, setSaveTelegramSuccess] = useState<string | null>(null);
  const [saveTelegramError, setSaveTelegramError] = useState<string | null>(null);
  const telegramDialogRef = useRef<HTMLDialogElement | null>(null);
  const telegramCancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const telegramTriggerRef = useRef<HTMLElement | null>(null);

  // Diagnostics
  const [adminDiagnostics, setAdminDiagnostics] = useState<AdminSystemStatusResponse | null>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [diagnosticsAttempt, setDiagnosticsAttempt] = useState(0);

  // Self Password Change
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordChangeBusy, setPasswordChangeBusy] = useState(false);
  const [passwordChangeError, setPasswordChangeError] = useState<string | null>(null);
  const [passwordChangeSuccess, setPasswordChangeSuccess] = useState<string | null>(null);

  // User Management (Admin Only)
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersAttempt, setUsersAttempt] = useState(0);
  const [userActionError, setUserActionError] = useState<string | null>(null);
  const [userActionSuccess, setUserActionSuccess] = useState<string | null>(null);
  const [togglingUserId, setTogglingUserId] = useState<number | null>(null);

  // Create User Form State
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState<"trader" | "admin" | "viewer">("trader");
  const [createUserBusy, setCreateUserBusy] = useState(false);
  const [createUserError, setCreateUserError] = useState<string | null>(null);
  const [createUserSuccess, setCreateUserSuccess] = useState<string | null>(null);

  // Action states and refs for Reset Password Dialog
  const [selectedUserForReset, setSelectedUserForReset] = useState<ManagedUser | null>(null);
  const [adminNewPassword, setAdminNewPassword] = useState("");
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const resetDialogRef = useRef<HTMLDialogElement | null>(null);
  const resetInputRef = useRef<HTMLInputElement | null>(null);
  const resetTriggerRef = useRef<HTMLElement | null>(null);

  // Action states and refs for Delete User Dialog
  const [selectedUserForDelete, setSelectedUserForDelete] = useState<ManagedUser | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deleteDialogRef = useRef<HTMLDialogElement | null>(null);
  const deleteCancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const deleteTriggerRef = useRef<HTMLElement | null>(null);

  // Action states and refs for Change Role Dialog
  const [selectedUserForRole, setSelectedUserForRole] = useState<ManagedUser | null>(null);
  const [editRoleSelection, setEditRoleSelection] = useState<"admin" | "trader" | "viewer">("trader");
  const [roleChangeBusy, setRoleChangeBusy] = useState(false);
  const [roleChangeError, setRoleChangeError] = useState<string | null>(null);
  const roleDialogRef = useRef<HTMLDialogElement | null>(null);
  const roleCancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const roleTriggerRef = useRef<HTMLElement | null>(null);

  // Focus management and modal lifecycle for Reset Password Dialog
  useEffect(() => {
    if (selectedUserForReset && resetDialogRef.current) {
      if (!resetDialogRef.current.open) {
        if (typeof resetDialogRef.current.showModal === "function") {
          resetDialogRef.current.showModal();
        } else {
          resetDialogRef.current.setAttribute("open", "");
        }
      }
      resetInputRef.current?.focus();
    }
  }, [selectedUserForReset]);

  // Focus management and modal lifecycle for Delete User Dialog
  useEffect(() => {
    if (selectedUserForDelete && deleteDialogRef.current) {
      if (!deleteDialogRef.current.open) {
        if (typeof deleteDialogRef.current.showModal === "function") {
          deleteDialogRef.current.showModal();
        } else {
          deleteDialogRef.current.setAttribute("open", "");
        }
      }
      deleteCancelButtonRef.current?.focus();
    }
  }, [selectedUserForDelete]);

  // Focus management and modal lifecycle for Change Role Dialog
  useEffect(() => {
    if (selectedUserForRole && roleDialogRef.current) {
      if (!roleDialogRef.current.open) {
        if (typeof roleDialogRef.current.showModal === "function") {
          roleDialogRef.current.showModal();
        } else {
          roleDialogRef.current.setAttribute("open", "");
        }
      }
      roleCancelButtonRef.current?.focus();
    }
  }, [selectedUserForRole]);

  // Focus management and modal lifecycle for Telegram Test Confirmation Dialog
  useEffect(() => {
    if (confirmTelegramTest && telegramDialogRef.current) {
      if (!telegramDialogRef.current.open) {
        if (typeof telegramDialogRef.current.showModal === "function") {
          telegramDialogRef.current.showModal();
        } else {
          telegramDialogRef.current.setAttribute("open", "");
        }
      }
      telegramCancelButtonRef.current?.focus();
    }
  }, [confirmTelegramTest]);

  const closeResetDialog = () => {
    if (resetDialogRef.current?.open) {
      if (typeof resetDialogRef.current.close === "function") {
        resetDialogRef.current.close();
      } else {
        resetDialogRef.current.removeAttribute("open");
      }
    }
    setSelectedUserForReset(null);
    setAdminNewPassword("");
    setResetError(null);
    const trigger = resetTriggerRef.current;
    if (trigger && document.body.contains(trigger)) {
      trigger.focus();
    }
  };

  const closeDeleteDialog = () => {
    if (deleteDialogRef.current?.open) {
      if (typeof deleteDialogRef.current.close === "function") {
        deleteDialogRef.current.close();
      } else {
        deleteDialogRef.current.removeAttribute("open");
      }
    }
    setSelectedUserForDelete(null);
    setDeleteError(null);
    const trigger = deleteTriggerRef.current;
    if (trigger && document.body.contains(trigger)) {
      trigger.focus();
    }
  };

  const closeTelegramDialog = () => {
    if (telegramDialogRef.current?.open) {
      if (typeof telegramDialogRef.current.close === "function") {
        telegramDialogRef.current.close();
      } else {
        telegramDialogRef.current.removeAttribute("open");
      }
    }
    setConfirmTelegramTest(false);
    setTelegramCustomMessage("");
    const trigger = telegramTriggerRef.current;
    if (trigger && document.body.contains(trigger)) {
      trigger.focus();
    }
  };

  const closeRoleDialog = () => {
    if (roleDialogRef.current?.open) {
      if (typeof roleDialogRef.current.close === "function") {
        roleDialogRef.current.close();
      } else {
        roleDialogRef.current.removeAttribute("open");
      }
    }
    setSelectedUserForRole(null);
    setRoleChangeError(null);
    const trigger = roleTriggerRef.current;
    if (trigger && document.body.contains(trigger)) {
      trigger.focus();
    }
  };

  // Load Telegram Status (Admin Only)
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

  useEffect(() => {
    if (telegramStatus) {
      if (telegramStatus.chatId) setEditTelegramChatId(telegramStatus.chatId);
      setEditTelegramDelivery((telegramStatus.delivery_authorized ?? telegramStatus.deliveryAuthorized) === true);
    }
  }, [telegramStatus]);

  const handleSaveTelegramConfig = async (e: FormEvent) => {
    e.preventDefault();
    setSavingTelegram(true);
    setSaveTelegramSuccess(null);
    setSaveTelegramError(null);
    try {
      await apiRequest("/api/telegram/config", {
        method: "PUT",
        body: JSON.stringify({
          bot_token: editTelegramToken ? editTelegramToken.trim() : undefined,
          chat_id: editTelegramChatId ? editTelegramChatId.trim() : undefined,
          delivery_authorized: editTelegramDelivery,
        }),
      });
      setSaveTelegramSuccess("Telegram credentials saved to encrypted database storage.");
      setEditTelegramToken("");
      setTelegramRefreshAttempt((v) => v + 1);
    } catch (err: any) {
      setSaveTelegramError(err.message || "Failed to save Telegram settings.");
    } finally {
      setSavingTelegram(false);
    }
  };

  // Load Diagnostics (Admin Only)
  useEffect(() => {
    if (!isAdmin) {
      setAdminDiagnostics(null);
      setDiagnosticsLoading(false);
      setDiagnosticsError(null);
      return;
    }
    const controller = new AbortController();
    setDiagnosticsLoading(true);
    setDiagnosticsError(null);
    fetchAdminSystemStatus(controller.signal).then((status) => {
      if (!controller.signal.aborted) setAdminDiagnostics(status);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setAdminDiagnostics(null);
        if (cause instanceof ApiError && cause.status === 401) setDiagnosticsError("Your session has expired. Sign in again to view detailed diagnostics.");
        else if (cause instanceof ApiError && cause.status === 403) setDiagnosticsError("Your account cannot view detailed diagnostics.");
        else setDiagnosticsError("Detailed system diagnostics could not be loaded. Retry the request.");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setDiagnosticsLoading(false);
    });
    return () => controller.abort();
  }, [isAdmin, diagnosticsAttempt]);

  // Load Users Catalog (Admin Only)
  useEffect(() => {
    if (!isAdmin) {
      setUsers([]);
      setUsersLoading(false);
      setUsersError(null);
      return;
    }
    const controller = new AbortController();
    setUsersLoading(true);
    setUsersError(null);
    fetchUsers(controller.signal).then((catalog) => {
      if (!controller.signal.aborted) setUsers(catalog);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setUsers([]);
        if (cause instanceof UserActionError && cause.status === 401) {
          setUsersError("Your session has expired. Sign in again to view user accounts.");
        } else if (cause instanceof UserActionError && cause.status === 403) {
          setUsersError("Only administrators can view user management accounts.");
        } else {
          setUsersError("User accounts could not be loaded. Please check backend connection and retry.");
        }
      }
    }).finally(() => {
      if (!controller.signal.aborted) setUsersLoading(false);
    });
    return () => controller.abort();
  }, [isAdmin, usersAttempt]);

  const handleSelfPasswordChange = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPasswordChangeError(null);
    setPasswordChangeSuccess(null);

    if (newPassword.length < 10) {
      setPasswordChangeError("New password must be at least 10 characters long.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordChangeError("New passwords do not match.");
      return;
    }

    setPasswordChangeBusy(true);
    try {
      await changePassword(oldPassword, newPassword);
      setPasswordChangeSuccess("Password updated successfully. Your new session is active.");
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (cause) {
      setPasswordChangeError(cause instanceof AuthRequestError ? cause.message : "Password change failed. Check your current password.");
    } finally {
      setPasswordChangeBusy(false);
    }
  };

  const handleCreateUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreateUserError(null);
    setCreateUserSuccess(null);

    if (!newUsername.trim()) {
      setCreateUserError("Username is required.");
      return;
    }
    if (newUserPassword.length < 10) {
      setCreateUserError("Password must be at least 10 characters.");
      return;
    }

    setCreateUserBusy(true);
    try {
      const created = await createUser({
        username: newUsername.trim(),
        email: newEmail.trim() || null,
        password: newUserPassword,
        role: newUserRole,
      });
      setCreateUserSuccess(`User '${created.username}' created successfully.`);
      setNewUsername("");
      setNewEmail("");
      setNewUserPassword("");
      setNewUserRole("trader");
      setUsersAttempt((v) => v + 1);
    } catch (cause) {
      setCreateUserError(cause instanceof UserActionError ? cause.message : "Could not create user account.");
    } finally {
      setCreateUserBusy(false);
    }
  };

  const handleToggleUserStatus = async (user: ManagedUser) => {
    setUserActionError(null);
    setUserActionSuccess(null);
    setTogglingUserId(user.id);
    try {
      const message = await updateUser(user.id, { is_active: !user.is_active });
      setUserActionSuccess(message);
      setUsersAttempt((v) => v + 1);
    } catch (cause) {
      setUserActionError(cause instanceof UserActionError ? cause.message : "Failed to update user status.");
    } finally {
      setTogglingUserId(null);
    }
  };

  const handleResetUserPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedUserForReset || resetBusy) return;
    setResetError(null);

    if (adminNewPassword.length < 10) {
      setResetError("Password must be at least 10 characters.");
      return;
    }

    setResetBusy(true);
    try {
      const message = await resetUserPassword(selectedUserForReset.id, adminNewPassword);
      setUserActionSuccess(message);
      setUsersAttempt((v) => v + 1);
      closeResetDialog();
    } catch (cause) {
      setResetError(cause instanceof UserActionError ? cause.message : "Failed to reset password.");
    } finally {
      setResetBusy(false);
    }
  };

  const handleDeleteUser = async () => {
    if (!selectedUserForDelete || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      const message = await deleteUser(selectedUserForDelete.id);
      setUserActionSuccess(message);
      setUsersAttempt((v) => v + 1);
      closeDeleteDialog();
    } catch (cause) {
      setDeleteError(cause instanceof UserActionError ? cause.message : "Failed to delete user.");
    } finally {
      setDeleteBusy(false);
    }
  };

  const handleRoleChangeSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!isAdmin || !selectedUserForRole || roleChangeBusy) return;
    const isSelf = (username ?? "").toLowerCase() === selectedUserForRole.username.toLowerCase();
    if (isSelf && editRoleSelection !== "admin") {
      setRoleChangeError("Cannot change your own role from admin");
      return;
    }
    setRoleChangeBusy(true);
    setRoleChangeError(null);
    setUserActionError(null);
    setUserActionSuccess(null);
    try {
      const message = await updateUser(selectedUserForRole.id, { role: editRoleSelection });
      closeRoleDialog();
      setUserActionSuccess(message || `User '${selectedUserForRole.username}' role updated to ${editRoleSelection}.`);
      setUsersAttempt((n) => n + 1);
    } catch (cause) {
      setRoleChangeError(cause instanceof UserActionError ? cause.message : "Failed to update user role.");
    } finally {
      setRoleChangeBusy(false);
    }
  };

  const runTelegramTest = async () => {
    if (!isAdmin || !confirmTelegramTest || telegramTestBusy || !telegramStatus?.hasToken) return;
    setTelegramTestBusy(true);
    setTelegramTestError(null);
    setTelegramTestMessage(null);
    try {
      const result = await sendTelegramTestPing(telegramCustomMessage);
      closeTelegramDialog();
      if (result.success) {
        setTelegramTestMessage(result.chat_id
          ? `Test ping sent to the configured Telegram chat${result.bot?.username ? ` via @${result.bot.username}` : ""}.`
          : result.note ?? "Bot token verified. No message was sent because a chat ID is not configured.");
      } else {
        setTelegramTestError(result.error || "Telegram test failed. Check bot and chat configuration.");
      }
    } catch (cause) {
      closeTelegramDialog();
      if (cause instanceof TelegramActionError && cause.status === 403) setTelegramTestError("Only admins can send a Telegram test ping.");
      else if (cause instanceof TelegramActionError && cause.status === 401) setTelegramTestError("Your session has expired. Sign in again before testing Telegram.");
      else setTelegramTestError(cause instanceof Error ? cause.message : "Telegram test failed. Please retry.");
    } finally {
      setTelegramTestBusy(false);
    }
  };

  return <div className="settings-view">
    <section className="panel paper-safety-banner"><ShieldCheck size={19} /><div><strong>Workspace preferences</strong><p>Manage workspace appearance, session credentials, password rotation, and administrator user controls. Server-side permissions are authoritative; credentials and raw database details remain protected.</p></div><span className="panel-counter">SETTINGS & USERS</span></section>

    {/* Appearance Preferences */}
    <section className="panel" aria-labelledby="settings-appearance-title">
      <div className="panel-heading"><div><h2 id="settings-appearance-title">Appearance</h2><p>Personalize the dashboard without changing backend configuration.</p></div><span className="panel-counter">LOCAL PREFERENCE</span></div>
      <PreferenceRow icon={theme === "dark" ? <Moon size={19} /> : <Sun size={19} />} title="Color theme" description={theme === "dark" ? "Dark theme is active. Your preference is saved in this browser when storage is available." : "Light theme is active. Your preference is saved in this browser when storage is available."}>
        <button className="button button-secondary" onClick={onToggleTheme} aria-label={"Switch to " + (theme === "dark" ? "light" : "dark") + " theme"}>Switch to {theme === "dark" ? "light" : "dark"}</button>
      </PreferenceRow>
    </section>

    {/* Current Session Identity */}
    <section className="panel" aria-labelledby="settings-session-title">
      <div className="panel-heading"><div><h2 id="settings-session-title">Current session</h2><p>Identity information returned by the existing authentication gate.</p></div><span className="panel-counter">READ ONLY</span></div>
      <PreferenceRow icon={<UserRound size={19} />} title="Signed-in user" description="User identity is provided by the server-side authentication flow; no token or credential is displayed.">
        <div className="settings-session-value"><strong>{username || "Preview workspace"}</strong><span>{role || "Unauthenticated preview"}</span></div>
      </PreferenceRow>
      <PreferenceRow icon={<ShieldCheck size={19} />} title="Access control" description="The server remains authoritative for every route and action. Hiding a control in this UI is not a replacement for backend authorization.">
        <span className="settings-readonly-tag">Server enforced</span>
      </PreferenceRow>
    </section>

    {/* Self Password Change */}
    <section className="panel" aria-labelledby="settings-password-title">
      <div className="panel-heading"><div><h2 id="settings-password-title"><Key size={17} className="inline-icon" /> Change My Password</h2><p>Update your account password with immediate session renewal.</p></div><span className="panel-counter">AUTHENTICATED</span></div>
      <form onSubmit={handleSelfPasswordChange} className="screener-control-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        <div className="screener-control-field">
          <label htmlFor="self-old-password">Current Password</label>
          <input id="self-old-password" type="password" required value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} placeholder="••••••••" />
        </div>
        <div className="screener-control-field">
          <label htmlFor="self-new-password">New Password (min 10 chars)</label>
          <input id="self-new-password" type="password" required minLength={10} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="••••••••" />
        </div>
        <div className="screener-control-field">
          <label htmlFor="self-confirm-password">Confirm New Password</label>
          <input id="self-confirm-password" type="password" required minLength={10} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="••••••••" />
        </div>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <button type="submit" className="button button-primary" disabled={passwordChangeBusy || !oldPassword || !newPassword}>
            {passwordChangeBusy ? "Updating…" : "Update Password"}
          </button>
        </div>
      </form>
      {passwordChangeError && <div className="api-error" role="alert" style={{ marginTop: 14 }}><div><strong>Password change failed</strong><p>{passwordChangeError}</p></div></div>}
      {passwordChangeSuccess && <p className="control-feedback control-success" role="status">{passwordChangeSuccess}</p>}
    </section>

    {/* Admin-Only User Management */}
    {isAdmin && <section className="panel" aria-labelledby="settings-users-title" aria-busy={usersLoading}>
      <div className="panel-heading">
        <div>
          <h2 id="settings-users-title"><Users size={17} className="inline-icon" /> User Accounts & Access Control</h2>
          <p>Manage operator credentials, role assignments, password resets, and account activation.</p>
        </div>
        <span className="panel-counter">{users.length} ACCOUNTS</span>
      </div>

      {usersError && <div className="api-error" role="alert"><div><strong>User management unavailable</strong><p>{usersError}</p></div><button className="button button-secondary" type="button" onClick={() => setUsersAttempt((v) => v + 1)}><RefreshCw size={15} /> Retry</button></div>}

      {userActionError && (
        <div className="api-error" role="alert" style={{ marginTop: 12 }}>
          <div>
            <strong>User action failed</strong>
            <p>{userActionError}</p>
          </div>
          <button className="button button-secondary" type="button" onClick={() => setUserActionError(null)}>Dismiss</button>
        </div>
      )}

      {userActionSuccess && (
        <p className="control-feedback control-success" role="status" style={{ marginTop: 12 }}>
          {userActionSuccess}
        </p>
      )}

      {/* Users Catalog Table */}
      <div className="status-table-wrap">
        <table className="status-table">
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Username</th>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              <th scope="col">Status</th>
              <th scope="col">Created</th>
              <th scope="col" style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {usersLoading && !users.length ? (
              <tr><td colSpan={7} className="empty-state" style={{ textAlign: "center" }}>Loading user accounts…</td></tr>
            ) : users.length ? users.map((u) => {
              const isSelf = (username ?? "").toLowerCase() === u.username.toLowerCase();
              return <tr key={u.id}>
                <td><strong>#{u.id}</strong></td>
                <td><span style={{ fontWeight: 600, color: "var(--color-accent)" }}>{u.username}</span></td>
                <td>{u.email || "—"}</td>
                <td><span className="status-pill" style={{ textTransform: "uppercase" }}>{u.role}</span></td>
                <td>
                  <span className={u.is_active ? "status-pill status-positive" : "status-pill status-warning"}>
                    {u.is_active ? "Active" : "Disabled"}
                  </span>
                  {u.must_change_password && <span className="status-pill" style={{ marginLeft: 6 }}>Must reset</span>}
                </td>
                <td><span className="heartbeat-time">{u.created_at.slice(0, 10)}</span></td>
                <td style={{ textAlign: "right" }}>
                  <div style={{ display: "inline-flex", gap: 6 }}>
                    <button
                      type="button"
                      className="button button-secondary"
                      style={{ padding: "4px 8px", fontSize: 11 }}
                      aria-label={`Reset password for ${u.username}`}
                      onClick={(e) => {
                        resetTriggerRef.current = e.currentTarget;
                        setAdminNewPassword("");
                        setResetError(null);
                        setSelectedUserForReset(u);
                      }}
                    >
                      Reset Pass
                    </button>
                    <button
                      type="button"
                      className="button button-secondary"
                      style={{ padding: "4px 8px", fontSize: 11 }}
                      aria-label={`Change role for ${u.username}`}
                      onClick={(e) => {
                        roleTriggerRef.current = e.currentTarget;
                        setSelectedUserForRole(u);
                        setEditRoleSelection(u.role);
                        setRoleChangeError(null);
                      }}
                    >
                      Change Role
                    </button>
                    {!isSelf && (
                      <button
                        type="button"
                        className="button button-secondary"
                        style={{ padding: "4px 8px", fontSize: 11 }}
                        aria-label={`${u.is_active ? "Disable" : "Enable"} user ${u.username}`}
                        disabled={togglingUserId === u.id}
                        onClick={() => void handleToggleUserStatus(u)}
                      >
                        {togglingUserId === u.id ? "Updating…" : u.is_active ? "Disable" : "Enable"}
                      </button>
                    )}
                    {!isSelf && (
                      <button
                        type="button"
                        className="button button-danger"
                        style={{ padding: "4px 8px", fontSize: 11 }}
                        aria-label={`Delete user ${u.username}`}
                        onClick={(e) => {
                          deleteTriggerRef.current = e.currentTarget;
                          setDeleteError(null);
                          setSelectedUserForDelete(u);
                        }}
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>;
            }) : (
              <tr><td colSpan={7} className="empty-state" style={{ textAlign: "center" }}>No user accounts found.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Accessible Native HTML Modal Dialog: Reset Password */}
      {selectedUserForReset && (
        <dialog
          ref={resetDialogRef}
          className="settings-dialog"
          aria-labelledby="reset-dialog-title"
          aria-describedby="reset-dialog-desc"
          onCancel={(e) => {
            e.preventDefault();
            closeResetDialog();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              closeResetDialog();
            } else {
              trapFocus(e);
            }
          }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="reset-dialog-title">Reset Password for '{selectedUserForReset.username}'</h3>
              <p id="reset-dialog-desc">Sets a temporary password. The user will be required to change it upon their next sign-in.</p>
            </div>
          </div>
          <form onSubmit={handleResetUserPassword} style={{ marginTop: 14 }}>
            <div className="screener-control-field">
              <label htmlFor="admin-reset-new-password">New Password (min 10 chars)</label>
              <input
                ref={resetInputRef}
                id="admin-reset-new-password"
                type="password"
                required
                minLength={10}
                value={adminNewPassword}
                disabled={resetBusy}
                onChange={(e) => setAdminNewPassword(e.target.value)}
                placeholder="Temporary password"
              />
            </div>
            {resetError && (
              <div className="api-error" role="alert" style={{ marginTop: 10 }}>
                <div><p>{resetError}</p></div>
              </div>
            )}
            <div style={{ display: "flex", gap: 10, marginTop: 16, justifyContent: "flex-end" }}>
              <button
                type="button"
                className="button button-secondary"
                disabled={resetBusy}
                onClick={closeResetDialog}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="button button-primary"
                disabled={resetBusy || adminNewPassword.length < 10}
              >
                {resetBusy ? "Resetting…" : "Confirm Password Reset"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {/* Accessible Native HTML Modal Dialog: Delete User */}
      {selectedUserForDelete && (
        <dialog
          ref={deleteDialogRef}
          className="settings-dialog"
          aria-labelledby="delete-dialog-title"
          aria-describedby="delete-dialog-desc"
          onCancel={(e) => {
            e.preventDefault();
            closeDeleteDialog();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              closeDeleteDialog();
            } else {
              trapFocus(e);
            }
          }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="delete-dialog-title">Delete Account '{selectedUserForDelete.username}'?</h3>
              <p id="delete-dialog-desc">This action permanently removes the user account and revokes active sessions. This cannot be undone.</p>
            </div>
          </div>
          {deleteError && (
            <div className="api-error" role="alert" style={{ marginTop: 10 }}>
              <div><p>{deleteError}</p></div>
            </div>
          )}
          <div style={{ display: "flex", gap: 10, marginTop: 16, justifyContent: "flex-end" }}>
            <button
              ref={deleteCancelButtonRef}
              type="button"
              className="button button-secondary"
              disabled={deleteBusy}
              onClick={closeDeleteDialog}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button button-danger"
              disabled={deleteBusy}
              onClick={() => void handleDeleteUser()}
            >
              {deleteBusy ? "Deleting…" : "Confirm Delete"}
            </button>
          </div>
        </dialog>
      )}

      {/* Accessible Native HTML Modal Dialog: Change Role */}
      {selectedUserForRole && (
        <dialog
          ref={roleDialogRef}
          className="settings-dialog"
          aria-labelledby="role-dialog-title"
          aria-describedby="role-dialog-desc"
          onCancel={(e) => {
            e.preventDefault();
            closeRoleDialog();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              closeRoleDialog();
            } else {
              trapFocus(e);
            }
          }}
        >
          <div className="panel-heading">
            <div>
              <h3 id="role-dialog-title">Change Role for '{selectedUserForRole.username}'</h3>
              <p id="role-dialog-desc">Select a new system role. Changing a role updates permissions immediately and revokes active sessions.</p>
            </div>
          </div>
          <form onSubmit={handleRoleChangeSubmit} style={{ marginTop: 14 }}>
            <div className="screener-control-field">
              <label htmlFor="admin-change-user-role">Role</label>
              <select
                id="admin-change-user-role"
                value={editRoleSelection}
                disabled={roleChangeBusy}
                onChange={(e) => setEditRoleSelection(e.target.value as "admin" | "trader" | "viewer")}
              >
                <option value="admin">Administrator (admin)</option>
                <option value="trader">Trader (trader)</option>
                <option value="viewer">Viewer (viewer)</option>
              </select>
            </div>
            {(username ?? "").toLowerCase() === selectedUserForRole.username.toLowerCase() && editRoleSelection !== "admin" && (
              <div className="api-error" role="alert" style={{ marginTop: 10 }}>
                <div><p>Cannot change your own role from admin. Self-demotion is restricted to prevent administrative lockout.</p></div>
              </div>
            )}
            {roleChangeError && (
              <div className="api-error" role="alert" style={{ marginTop: 10 }}>
                <div><p>{roleChangeError}</p></div>
              </div>
            )}
            <div style={{ display: "flex", gap: 10, marginTop: 16, justifyContent: "flex-end" }}>
              <button
                ref={roleCancelButtonRef}
                type="button"
                className="button button-secondary"
                disabled={roleChangeBusy}
                onClick={closeRoleDialog}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="button button-primary"
                disabled={roleChangeBusy || ((username ?? "").toLowerCase() === selectedUserForRole.username.toLowerCase() && editRoleSelection !== "admin")}
              >
                {roleChangeBusy ? "Updating…" : "Confirm Role Change"}
              </button>
            </div>
          </form>
        </dialog>
      )}

      {/* Create New User Form */}
      <div className="panel-subsection" style={{ marginTop: 20 }}>
        <div className="panel-heading">
          <div>
            <h3><UserPlus size={15} className="inline-icon" /> Create New User Account</h3>
            <p>Provision operator credentials with server-enforced role assignments.</p>
          </div>
        </div>
        <form onSubmit={handleCreateUser}>
          <div className="screener-control-grid">
            <div className="screener-control-field">
              <label htmlFor="create-username">Username</label>
              <input
                id="create-username"
                required
                maxLength={30}
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="trader_john"
              />
            </div>
            <div className="screener-control-field">
              <label htmlFor="create-email">Email (Optional)</label>
              <input
                id="create-email"
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="john@example.com"
              />
            </div>
            <div className="screener-control-field">
              <label htmlFor="create-password">Password (10–1024 chars)</label>
              <input
                id="create-password"
                type="password"
                required
                minLength={10}
                maxLength={1024}
                value={newUserPassword}
                onChange={(e) => setNewUserPassword(e.target.value)}
                placeholder="••••••••••••"
              />
            </div>
            <div className="screener-control-field">
              <label htmlFor="create-role">Assigned Role</label>
              <select
                id="create-role"
                value={newUserRole}
                onChange={(e) => setNewUserRole(e.target.value as typeof newUserRole)}
                style={{
                  minHeight: 38,
                  padding: "0 10px",
                  borderRadius: 7,
                  border: "1px solid var(--color-border)",
                  background: "var(--color-elevated)",
                  color: "var(--color-text)",
                }}
              >
                <option value="trader">Trader (Full Operations)</option>
                <option value="admin">Administrator (Full Access)</option>
                <option value="viewer">Viewer (Read Only)</option>
              </select>
            </div>
          </div>
          <div className="screener-control-footer">
            <button type="submit" className="button button-primary" disabled={createUserBusy || !newUsername.trim() || newUserPassword.length < 10}>
              <Plus size={15} />
              {createUserBusy ? "Creating User…" : "Add User Account"}
            </button>
          </div>
        </form>
        {createUserError && <div className="api-error" role="alert" style={{ marginTop: 14 }}><div><strong>Creation failed</strong><p>{createUserError}</p></div></div>}
        {createUserSuccess && <p className="control-feedback control-success" role="status">{createUserSuccess}</p>}
      </div>
    </section>}

    {/* Telegram Integration Panel (Admin Only) */}
    <section className="panel" aria-labelledby="settings-telegram-title" aria-busy={telegramLoading || telegramTestBusy}>
      <div className="panel-heading"><div><h2 id="settings-telegram-title"><MessageCircle size={17} className="inline-icon" /> Telegram status and test</h2><p>Read-only configuration status and an explicit test-ping workflow. A confirmed test may send a real Telegram message.</p></div><span className="panel-counter">{isAdmin ? "ADMIN ONLY" : "RESTRICTED"}</span></div>
      {!isAdmin ? <p className="empty-state">Telegram status and test controls are restricted to admins. This role does not fetch or display the configured chat ID.</p> : <>
        {telegramError && <div className="api-error" role="alert"><div><strong>Telegram status unavailable</strong><p>{telegramError}</p></div><button className="button button-secondary" type="button" onClick={() => setTelegramRefreshAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
        {telegramLoading && <p className="empty-state">Loading Telegram status…</p>}
        {telegramStatus && (telegramStatus.delivery_authorized === false ||
          telegramStatus.delivery_authorized === null || telegramStatus.outbox_blocked_reason) && (
          <div className="api-error" role="alert">Telegram delivery is not authorized; queued alerts are waiting.</div>
        )}
        {telegramStatus && <>
          <div className="settings-telegram-status">
            <div><span>Configuration</span><strong>{
              !telegramStatus.configured
                ? "Incomplete"
                : telegramStatus.deliveryAuthorized
                  ? "Configured · delivery enabled"
                  : "Configured · delivery disabled"
            }</strong></div>
            <div><span>RetailBot2 outbox</span><strong>{telegramStatus.outbox == null ? "Unavailable" :
              Object.entries(telegramStatus.outbox).map(([state, count]) => count + " " + state).join(", ") || "Empty"}</strong></div>
            <div><span>Bot token</span><strong>{telegramStatus.hasToken ? "Present (hidden)" : "Missing"}</strong></div>
            <div><span>Target chat ID</span><strong>{telegramStatus.chatId || "Not configured"}</strong></div>
            <button className="button button-secondary" type="button" disabled={telegramLoading || telegramTestBusy} onClick={() => setTelegramRefreshAttempt((value) => value + 1)}><RefreshCw size={15} /> Refresh status</button>
          </div>
          <p className="settings-preference-subtext" style={{ fontSize: "12px", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Delivery authorization reflects database settings when configured (app_telegram_settings) or environment fallback.
          </p>
          {isAdmin && (
            <form onSubmit={handleSaveTelegramConfig} style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10, padding: 12, backgroundColor: "var(--color-surface, #1e293b)", borderRadius: 8, border: "1px solid var(--color-border, #334155)" }}>
              <strong style={{ fontSize: 13 }}>Update Telegram Settings (Encrypted DB Storage)</strong>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div>
                  <label style={{ fontSize: 11, color: "var(--color-muted)", display: "block", marginBottom: 2 }}>Bot Token</label>
                  <input
                    type="password"
                    placeholder="Enter token (leave blank to keep)"
                    value={editTelegramToken}
                    onChange={(e) => setEditTelegramToken(e.target.value)}
                    className="text-input"
                    style={{ width: "100%", height: 32, fontSize: 12 }}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 11, color: "var(--color-muted)", display: "block", marginBottom: 2 }}>Target Chat ID</label>
                  <input
                    type="text"
                    placeholder="e.g. 1487656060"
                    value={editTelegramChatId}
                    onChange={(e) => setEditTelegramChatId(e.target.value)}
                    className="text-input"
                    style={{ width: "100%", height: 32, fontSize: 12 }}
                  />
                </div>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, cursor: "pointer", marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={editTelegramDelivery}
                  onChange={(e) => setEditTelegramDelivery(e.target.checked)}
                />
                <span>Authorize Telegram message delivery</span>
              </label>
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button type="submit" className="button button-primary button-sm" disabled={savingTelegram}>
                  {savingTelegram ? "Saving…" : "Save to Database"}
                </button>
              </div>
              {saveTelegramSuccess && <p className="control-feedback control-success" role="status">{saveTelegramSuccess}</p>}
              {saveTelegramError && <div className="api-error" role="alert"><div><p>{saveTelegramError}</p></div></div>}
            </form>
          )}
        </>}
        {telegramTestError && <div className="api-error" role="alert"><div><strong>Telegram test failed</strong><p>{telegramTestError}</p></div><button className="button button-secondary" type="button" onClick={() => setTelegramTestError(null)}>Dismiss</button></div>}
        {telegramTestMessage && <p className="control-feedback control-success" role="status">{telegramTestMessage}</p>}
        <div className="screener-control-footer"><button className="button button-secondary" type="button" disabled={telegramLoading || telegramTestBusy || !telegramStatus?.hasToken} onClick={(e) => { telegramTriggerRef.current = e.currentTarget; setConfirmTelegramTest(true); setTelegramTestError(null); setTelegramTestMessage(null); }}>Review Telegram test</button></div>
        {confirmTelegramTest && (
          <dialog
            ref={telegramDialogRef}
            className="settings-dialog"
            aria-labelledby="settings-telegram-confirm-title"
            aria-describedby="settings-telegram-confirm-desc"
            onCancel={(e) => {
              e.preventDefault();
              closeTelegramDialog();
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                closeTelegramDialog();
              } else {
                trapFocus(e);
              }
            }}
          >
            <div className="panel-heading">
              <div>
                <h3 id="settings-telegram-confirm-title">Confirm Telegram test ping</h3>
                <p id="settings-telegram-confirm-desc">
                  {telegramStatus?.chatId
                    ? "This sends a real test message to the configured Telegram chat. Continue only if you intend to send that message."
                    : "No chat ID is configured. This will verify the bot token but should not send a message."}
                </p>
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <label className="screener-control-field" htmlFor="telegram-custom-msg">
                Custom test message (optional)
              </label>
              <input
                id="telegram-custom-msg"
                type="text"
                maxLength={1000}
                value={telegramCustomMessage}
                placeholder="Enter optional test message text"
                disabled={telegramTestBusy}
                onChange={(e) => setTelegramCustomMessage(e.target.value)}
                style={{ width: "100%", marginTop: 4 }}
              />
            </div>
            <div style={{ display: "flex", gap: 10, marginTop: 16, justifyContent: "flex-end" }}>
              <button
                ref={telegramCancelButtonRef}
                type="button"
                className="button button-secondary"
                disabled={telegramTestBusy}
                onClick={closeTelegramDialog}
              >
                Cancel test
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={telegramTestBusy || !telegramStatus?.hasToken}
                onClick={() => void runTelegramTest()}
              >
                {telegramTestBusy ? "Testing…" : "Send test ping"}
              </button>
            </div>
          </dialog>
        )}
      </>}
    </section>

    {/* Admin-Only Detailed System Diagnostics */}
    {isAdmin && <section className="panel" aria-labelledby="settings-diagnostics-title" aria-busy={diagnosticsLoading}>
      <div className="panel-heading"><div><h2 id="settings-diagnostics-title">Detailed system diagnostics</h2><p>Admin-only connection metadata and collector health. Raw database exception text is never displayed.</p></div><span className="panel-counter">ADMIN ONLY</span></div>
      {diagnosticsError && <div className="api-error" role="alert"><div><strong>Diagnostics unavailable</strong><p>{diagnosticsError}</p></div><button className="button button-secondary" type="button" onClick={() => setDiagnosticsAttempt((value) => value + 1)}><RefreshCw size={15} /> Retry</button></div>}
      {diagnosticsLoading && !adminDiagnostics ? <p className="empty-state" role="status">Loading detailed diagnostics…</p> : adminDiagnostics && <>
        <div className="settings-telegram-status">
          <div><span>Database status</span><strong>{adminDiagnostics.database.status === "ok" ? "Connected" : "Unavailable"}</strong></div>
          <div><span>Database host</span><strong>{adminDiagnostics.database.host}</strong></div>
          <div><span>Database port</span><strong>{adminDiagnostics.database.port}</strong></div>
          <div><span>Database name</span><strong>{adminDiagnostics.database.database}</strong></div>
          <div><span>Table inventory</span><strong>{Object.keys(adminDiagnostics.database.tables).length} tables</strong></div>
          <div><span>Timezone</span><strong>{adminDiagnostics.system?.timezone ?? "Asia/Jakarta (WIB UTC+7)"}</strong></div>
          <div><span>Binance Spot API</span><strong>{adminDiagnostics.binance.spotApi}</strong></div>
          <div><span>Binance Futures API</span><strong>{adminDiagnostics.binance.futuresApi}</strong></div>
        </div>
        {adminDiagnostics.database.status !== "ok" && <p className="control-feedback" role="status">Database unavailable. Raw database error details are hidden from this view.</p>}
        <div className="panel-subsection"><div className="panel-heading"><div><h3>Collector health</h3><p>Heartbeat status only; diagnostic detail strings are intentionally omitted.</p></div><span className="panel-counter">{adminDiagnostics.heartbeats.length} RECORDS</span></div>
          {adminDiagnostics.heartbeats.length ? <div className="status-table-wrap"><table className="status-table"><thead><tr><th scope="col">Collector</th><th scope="col">Status</th><th scope="col">Last heartbeat</th></tr></thead><tbody>{adminDiagnostics.heartbeats.map((heartbeat, index) => <tr key={heartbeat.collector + "-" + index}><td>{heartbeat.collector}</td><td>{heartbeat.status}</td><td>{heartbeat.lastBeat || "Unavailable"}</td></tr>)}</tbody></table></div> : <p className="empty-state">No collector heartbeat records were returned.</p>}
        </div>
      </>}
      <div className="screener-control-footer" style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        <button className="button button-secondary" type="button" disabled={diagnosticsLoading} onClick={() => setDiagnosticsAttempt((value) => value + 1)}><RefreshCw size={15} /> Refresh diagnostics</button>
        <button className="button button-secondary" type="button" onClick={() => window.dispatchEvent(new CustomEvent("signal-bot:open-onboarding"))}>Launch Setup Wizard</button>
      </div>
    </section>}

    {/* Commercial License & Plan (Admin Only) */}
    {isAdmin && license && <LicenseStatusCard license={license} activeUserCount={users.filter((u) => u.is_active).length} />}

    {/* Admin-Only Operator Audit Logs */}
    {isAdmin && <ServicesSection isAdmin={isAdmin} />}

    {isAdmin && <AuditLogsSection />}

    <RetailBot2ArchiveSection />
  </div>;
}
