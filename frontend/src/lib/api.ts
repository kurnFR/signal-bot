/**
 * Same-origin JSON transport for verified API endpoints.
 * Mutations are exposed only through explicit domain helpers that call postJson.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: "http" | "invalid-json" | "invalid-content-type";

  constructor(message: string, status: number, code: ApiError["code"]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function assertApiPath(path: string): void {
  if (!path.startsWith("/api/") || path.startsWith("//") || path.includes("\\") || /^[a-z][a-z\d+.-]*:/i.test(path)) {
    throw new TypeError("API requests must use a same-origin /api/ path");
  }

  // URL parsers normalize literal and percent-encoded dot segments before
  // sending the request. Reject traversal and encoded separators so a path
  // that looks like /api/... cannot escape to /static or another same-origin
  // route while still carrying the caller's bearer token.
  const pathname = path.split(/[?#]/, 1)[0];
  for (const segment of pathname.split("/")) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      throw new TypeError("API request path contains invalid percent encoding");
    }
    if (decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\")) {
      throw new TypeError("API request path must not contain traversal or encoded separators");
    }
  }
}
function readAuthToken(): string | null {
  try {
    const token = window.localStorage.getItem("token");
    if (!token) return null;
    const trimmed = token.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

/** Request a JSON API resource without permitting mutation methods or external origins. */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = readAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    headers,
    signal,
  });

  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/me") {
      // An authenticated page can outlive its server session. Clear the stale
      // token and notify the auth gate so the user can sign in again.
      try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
      window.dispatchEvent(new Event("signal-bot:session-expired"));
    }
    throw new ApiError(`API request failed with status ${response.status}`, response.status, "http");
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new ApiError("API response was not JSON", response.status, "invalid-content-type");
  }

  try {
    return await response.json() as T;
  } catch {
    throw new ApiError("API returned invalid JSON", response.status, "invalid-json");
  }
}

/** Submit a same-origin JSON request to a verified mutation endpoint. */
export async function postJson<TResponse, TBody extends object>(
  path: string,
  body: TBody,
  signal?: AbortSignal,
): Promise<TResponse> {
  assertApiPath(path);
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  const token = readAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/me") {
      try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
      window.dispatchEvent(new Event("signal-bot:session-expired"));
    }
    throw new ApiError(`API request failed with status ${response.status}`, response.status, "http");
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new ApiError("API response was not JSON", response.status, "invalid-content-type");
  }

  try {
    return await response.json() as TResponse;
  } catch {
    throw new ApiError("API returned invalid JSON", response.status, "invalid-json");
  }
}
