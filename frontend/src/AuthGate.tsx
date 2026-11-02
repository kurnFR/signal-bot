import { createContext, useCallback, useContext, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Activity, ShieldCheck } from "lucide-react";
import { ApiError } from "./lib/api";
import { AuthRequestError, changePassword, fetchAuthUser, login, logout, type AuthUser } from "./lib/auth";

interface AuthContextValue { user: AuthUser; logout: () => Promise<void>; logoutError: string | null }
const AuthContext = createContext<AuthContextValue | null>(null);
export function useAuth() { return useContext(AuthContext); }

export function AuthGate({ children }: { children: ReactNode }) {
  const [screen, setScreen] = useState<"checking" | "login" | "authenticated" | "password-change">("checking");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    const handleSessionExpired = () => {
      try { window.localStorage.removeItem("token"); } catch { /* In-memory auth state still resets. */ }
      setUser(null);
      setPassword("");
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setError("Your session has expired. Sign in again to continue.");
      setLogoutError(null);
      setScreen("login");
    };
    window.addEventListener("signal-bot:session-expired", handleSessionExpired);
    return () => window.removeEventListener("signal-bot:session-expired", handleSessionExpired);
  }, []);

  useEffect(() => {
    let active = true;
    let token: string | null = null;
    try { token = window.localStorage.getItem("token"); } catch { token = null; }
    if (!token) { setScreen("login"); return () => { active = false; }; }
    fetchAuthUser().then((profile) => {
      if (!active) return;
      setUser(profile);
      setScreen(profile.must_change_password ? "password-change" : "authenticated");
    }).catch((cause) => {
      if (!active) return;
      // Only an explicit 401 proves the stored session is invalid. A timeout,
      // network failure, or server error must not destroy a valid local session.
      if (cause instanceof ApiError && cause.status === 401) {
        try { window.localStorage.removeItem("token"); } catch { /* Session state still resets below. */ }
        setError(null);
      } else {
        setError("Unable to verify your session. The server may be temporarily unavailable. Please try signing in again.");
      }
      setScreen("login");
    });
    return () => { active = false; };
  }, []);

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(null); setLogoutError(null);
    try {
      const result = await login(username.trim(), password);
      setUser(result.user); setPassword("");
      setScreen(result.user.must_change_password ? "password-change" : "authenticated");
    } catch (cause) {
      setError(cause instanceof AuthRequestError ? cause.message : "Unable to sign in. Please try again.");
    } finally { setBusy(false); }
  };

  const handlePasswordChange = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setError(null);
    if (newPassword.length < 10) { setError("New password must be at least 10 characters."); return; }
    if (newPassword !== confirmPassword) { setError("New password and confirmation do not match."); return; }
    setBusy(true);
    try {
      await changePassword(oldPassword, newPassword);
      const profile = await fetchAuthUser();
      setUser(profile); setOldPassword(""); setNewPassword(""); setConfirmPassword("");
      setScreen(profile.must_change_password ? "password-change" : "authenticated");
    } catch (cause) {
      setError(cause instanceof AuthRequestError || cause instanceof ApiError ? cause.message : "Unable to change password. Please try again.");
    } finally { setBusy(false); }
  };

  const handleLogout = useCallback(async () => {
    setLogoutError(null);
    try {
      await logout();
      setUser(null); setPassword(""); setScreen("login");
    } catch {
      setLogoutError("The server could not confirm session revocation. You remain signed in; retry sign out when the API is available.");
    }
  }, []);
  const contextValue = useMemo(
    () => user ? ({ user, logout: handleLogout, logoutError }) : null,
    [user, handleLogout, logoutError],
  );

  if (screen === "checking") return <main className="auth-loading" role="status"><Activity size={20} /> Checking session…</main>;
  if (screen === "authenticated" && user && contextValue) return (
    <AuthContext.Provider value={contextValue}>
      {logoutError && <div className="api-error" role="alert">
        <div><strong>Unable to revoke session</strong><p>{logoutError}</p></div>
        <button className="button button-secondary" onClick={() => void handleLogout()}>Retry sign out</button>
      </div>}
      {children}
    </AuthContext.Provider>
  );

  return <main className="auth-page">
    <section className="auth-card" aria-labelledby="auth-title">
      <div className="auth-brand"><span className="auth-brand-icon"><Activity size={22} /></span><span><strong>Signal Bot</strong><small>QUANTITATIVE PLATFORM</small></span></div>
      <div className="auth-intro"><span className="auth-eyebrow">SECURE WORKSPACE</span><h1 id="auth-title">{screen === "password-change" ? "Change your password" : "Sign in"}</h1><p>{screen === "password-change" ? "Your account must set a new password before continuing." : "Sign in with your existing Signal Bot account to access the migration preview. Some role-gated controls can persist configuration or start background jobs when explicitly used."}</p></div>
      {error && <div className="auth-error" role="alert">{error}</div>}
      {logoutError && <div className="auth-error" role="alert">
        <p>{logoutError}</p>
        <button className="auth-secondary" type="button" disabled={busy} onClick={() => void handleLogout()}>
          Retry sign out
        </button>
      </div>}
      {screen === "password-change" ? <form onSubmit={handlePasswordChange} className="auth-form">
        <label>Current password<input type="password" autoComplete="current-password" maxLength={1024} value={oldPassword} onChange={(event) => setOldPassword(event.target.value)} required /></label>
        <label>New password<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
        <label>Confirm new password<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></label>
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Updating…" : "Change password"}</button>
        <button className="auth-secondary" type="button" disabled={busy} onClick={() => void handleLogout()}>Sign out</button>
      </form> : <form onSubmit={handleLogin} className="auth-form">
        <label>Username<input autoComplete="username" maxLength={30} value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label>Password<input type="password" autoComplete="current-password" maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>}
      <p className="auth-safety"><ShieldCheck size={15} /> Authentication and permissions remain enforced by the existing server.</p>
    </section>
  </main>;
}
