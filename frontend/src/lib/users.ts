import { assertApiPath } from "./api";

export interface ManagedUser {
  id: number;
  username: string;
  email: string | null;
  role: "admin" | "trader" | "viewer";
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateUserData {
  username: string;
  email?: string | null;
  password: string;
  role: "admin" | "trader" | "viewer";
}

export class UserActionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "UserActionError";
  }
}

function tokenFromStorage(): string | null {
  try {
    const token = window.localStorage.getItem("token");
    if (!token) return null;
    const trimmed = token.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function isManagedUser(value: unknown): value is ManagedUser {
  return isRecord(value) &&
    Number.isSafeInteger(value.id) && (value.id as number) > 0 &&
    typeof value.username === "string" && value.username.trim().length > 0 &&
    (value.email === undefined || value.email === null || typeof value.email === "string") &&
    typeof value.role === "string" && ["admin", "trader", "viewer"].includes(value.role) &&
    (value.is_active === undefined || typeof value.is_active === "boolean") &&
    (value.must_change_password === undefined || typeof value.must_change_password === "boolean") &&
    (value.created_at === undefined || typeof value.created_at === "string") &&
    (value.updated_at === undefined || typeof value.updated_at === "string");
}

/** Admin-only user management API client with server authorization enforcement. */
async function userRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  assertApiPath(path);
  const token = tokenFromStorage();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Session expired reset */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
    throw new UserActionError(401, "Your session has expired. Sign in again.");
  }

  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`;
    if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.trim()) {
      message = payload.detail.trim();
    }
    throw new UserActionError(response.status, message);
  }

  return payload as T;
}

/** Fetch all user accounts (Admin only). */
export async function fetchUsers(signal?: AbortSignal): Promise<ManagedUser[]> {
  const data = await userRequest<{ users: unknown }>("/api/users", { method: "GET", signal });
  if (!isRecord(data) || !Array.isArray(data.users) || !data.users.every(isManagedUser)) {
    throw new UserActionError(502, "Server returned an invalid users catalog");
  }
  return data.users.map((u) => ({
    id: u.id,
    username: u.username,
    email: u.email ?? null,
    role: u.role,
    is_active: typeof u.is_active === "boolean" ? u.is_active : true,
    must_change_password: typeof u.must_change_password === "boolean" ? u.must_change_password : false,
    created_at: typeof u.created_at === "string" ? u.created_at : "",
    updated_at: typeof u.updated_at === "string" ? u.updated_at : "",
  }));
}

/** Create a new user account (Admin only). */
export async function createUser(data: CreateUserData): Promise<ManagedUser> {
  const payload = await userRequest<{ status: string; user: unknown }>("/api/users", {
    method: "POST",
    body: JSON.stringify({
      username: data.username.trim(),
      email: data.email?.trim() || null,
      password: data.password,
      role: data.role,
    }),
  });
  if (!isRecord(payload) || payload.status !== "success" || !isManagedUser(payload.user)) {
    throw new UserActionError(502, "Server created user but returned an invalid profile shape");
  }
  const u = payload.user;
  return {
    id: u.id,
    username: u.username,
    email: u.email ?? null,
    role: u.role,
    is_active: typeof u.is_active === "boolean" ? u.is_active : true,
    must_change_password: typeof u.must_change_password === "boolean" ? u.must_change_password : false,
    created_at: typeof u.created_at === "string" ? u.created_at : "",
    updated_at: typeof u.updated_at === "string" ? u.updated_at : "",
  };
}

/** Reset a user's password (Admin only). Target will be required to change password on next login. */
export async function resetUserPassword(userId: number, newPassword: string): Promise<string> {
  if (!Number.isSafeInteger(userId) || userId <= 0) {
    throw new TypeError("Invalid user ID");
  }
  const payload = await userRequest<{ status: string; message?: string }>(`/api/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ new_password: newPassword }),
  });
  return payload.message || "Password reset successfully";
}

/** Update user active status or role (Admin only). */
export async function updateUser(
  userId: number,
  updates: { role?: "admin" | "trader" | "viewer"; is_active?: boolean },
): Promise<string> {
  if (!Number.isSafeInteger(userId) || userId <= 0) {
    throw new TypeError("Invalid user ID");
  }
  const payload = await userRequest<{ status: string; message?: string }>(`/api/users/${userId}`, {
    method: "PUT",
    body: JSON.stringify(updates),
  });
  return payload.message || "User updated successfully";
}

/** Delete a user account (Admin only). */
export async function deleteUser(userId: number): Promise<string> {
  if (!Number.isSafeInteger(userId) || userId <= 0) {
    throw new TypeError("Invalid user ID");
  }
  const payload = await userRequest<{ status: string; message?: string }>(`/api/users/${userId}`, {
    method: "DELETE",
  });
  return payload.message || "User deleted successfully";
}
