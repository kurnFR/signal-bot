/**
 * Same-origin JSON transport for verified API endpoints.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: "http" | "invalid-json" | "invalid-content-type";
  readonly detail?: string;

  constructor(message: string, status: number, code: ApiError["code"], detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
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

export function readAuthToken(): string | null {
  try {
    const token = window.localStorage.getItem("token");
    if (!token) return null;
    const trimmed = token.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  body?: BodyInit | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  assertApiPath(path);
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers ?? {}),
  };
  const token = readAuthToken();
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }

  let body = options.body;
  if (options.json !== undefined) {
    if (!headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    body = JSON.stringify(options.json);
  }

  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    body,
    signal: options.signal,
  });

  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/me") {
      try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
      window.dispatchEvent(new Event("signal-bot:session-expired"));
    }

    let parsedDetail: string | undefined;
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.toLowerCase().includes("application/json")) {
      try {
        const errPayload = await response.json();
        if (errPayload && typeof errPayload === "object") {
          if (typeof errPayload.detail === "string" && errPayload.detail.trim().length > 0) {
            parsedDetail = errPayload.detail.trim();
          } else if (Array.isArray(errPayload.detail)) {
            parsedDetail = errPayload.detail
              .map((item: any) => (item && typeof item === "object" && item.msg ? String(item.msg) : JSON.stringify(item)))
              .join("; ");
          } else if (typeof errPayload.message === "string" && errPayload.message.trim().length > 0) {
            parsedDetail = errPayload.message.trim();
          }
        }
      } catch {
        // Fallback to generic status message
      }
    }

    const message = parsedDetail || `API request failed with status ${response.status}`;
    throw new ApiError(message, response.status, "http", parsedDetail);
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

/** Request a JSON API resource without permitting mutation methods or external origins. */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(path, { method: "GET", signal });
}

/** Submit a same-origin JSON request to a verified API endpoint. */
export async function postJson<TResponse, TBody = unknown>(
  path: string,
  body?: TBody,
  signal?: AbortSignal,
): Promise<TResponse> {
  return apiRequest<TResponse>(path, { method: "POST", json: body, signal });
}
