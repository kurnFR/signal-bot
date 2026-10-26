export interface AuthUser { id: number; username: string; email: string | null; role: string; must_change_password: boolean }
export interface LoginResult { token: string; user: AuthUser }
export class AuthRequestError extends Error {
  constructor(readonly status: number, message: string) { super(message); this.name = "AuthRequestError"; }
}

const AUTH_PATHS = new Set(["/api/auth/login", "/api/auth/logout", "/api/auth/change-password"]);
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
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string" ? payload.detail.slice(0, 300) : `Authentication request failed (${response.status})`;
    throw new AuthRequestError(response.status, detail);
  }
  if (!payload || typeof payload !== "object") throw new AuthRequestError(response.status, "Authentication service returned an invalid response");
  return payload as T;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const result = await authPost<LoginResult>("/api/auth/login", { username, password });
  if (!result.token || !result.user || typeof result.user.id !== "number") throw new AuthRequestError(502, "Authentication service returned an invalid session");
  try { window.localStorage.setItem("token", result.token); } catch { throw new AuthRequestError(500, "Session storage is unavailable in this browser"); }
  return result;
}

export async function logout(): Promise<void> {
  try { if (tokenFromStorage()) await authPost("/api/auth/logout"); } finally {
    try { window.localStorage.removeItem("token"); } catch { /* Clear in-memory session even when storage is restricted. */ }
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await authPost("/api/auth/change-password", { old_password: oldPassword, new_password: newPassword });
}
