export interface AuthUser { id: number; username: string; email: string | null; role: string; must_change_password: boolean }
export interface LoginResult { token: string; user: AuthUser }
export class AuthRequestError extends Error {
  constructor(readonly status: number, message: string) { super(message); this.name = "AuthRequestError"; }
}

const AUTH_PATHS = new Set(["/api/auth/login", "/api/auth/logout", "/api/auth/change-password"]);
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isAuthUser(value: unknown): value is AuthUser {
  return isRecord(value) && Number.isSafeInteger(value.id) && (value.id as number) > 0 &&
    typeof value.username === "string" && value.username.trim().length > 0 &&
    (value.email === null || typeof value.email === "string") &&
    typeof value.role === "string" && ["admin", "trader", "viewer"].includes(value.role) &&
    typeof value.must_change_password === "boolean";
}
export async function fetchAuthUser(signal?: AbortSignal): Promise<AuthUser> {
  const payload = await getJson<unknown>("/api/auth/me", signal);
  if (!isAuthUser(payload)) {
    throw new AuthRequestError(502, "Authentication service returned an invalid user profile");
  }
  return payload;
}
import { getJson } from "./api";
function tokenFromStorage(): string | null {
  try { return window.localStorage.getItem("token"); } catch { return null; }
}

async function authPost<T>(path: string, body?: unknown): Promise<T> {
  if (!AUTH_PATHS.has(path)) throw new TypeError("Unsupported authentication endpoint");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenFromStorage();
  if (token && path !== "/api/auth/login") headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { method: "POST", credentials: "same-origin", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }
  if (response.status === 401 && path === "/api/auth/change-password") {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = response.status === 401 && path === "/api/auth/change-password"
      ? "Your session has expired. Sign in again to continue."
      : payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
        ? payload.detail.slice(0, 300)
        : `Authentication request failed (${response.status})`;
    throw new AuthRequestError(response.status, detail);
  }
  if (!payload || typeof payload !== "object") throw new AuthRequestError(response.status, "Authentication service returned an invalid response");
  return payload as T;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const payload = await authPost<unknown>("/api/auth/login", { username, password });
  if (!isRecord(payload) || typeof payload.token !== "string" || !payload.token.trim() || !isAuthUser(payload.user)) {
    throw new AuthRequestError(502, "Authentication service returned an invalid session");
  }
  const result: LoginResult = { token: payload.token, user: payload.user };
  try { window.localStorage.setItem("token", result.token); } catch { throw new AuthRequestError(500, "Session storage is unavailable in this browser"); }
  return result;
}

export async function logout(): Promise<void> {
  try { if (tokenFromStorage()) await authPost("/api/auth/logout"); } finally {
    try { window.localStorage.removeItem("token"); } catch { /* Clear in-memory session even when server revocation fails. */ }
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await authPost("/api/auth/change-password", { old_password: oldPassword, new_password: newPassword });
}
