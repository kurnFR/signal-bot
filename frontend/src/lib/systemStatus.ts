import { getJson } from "./api";

export interface LoginRateLimiterStatus {
  mode: string;
  degraded: boolean;
}

export interface BrandingConfig {
  brandName: string;
  logoUrl: string | null;
}

export interface LicenseStatus {
  valid: boolean;
  mode: string;
  customer: string | null;
  tier: string;
  maxUsers: number | null;
  expiresAt: string | null;
  reason: string | null;
}

export interface UIFeatureFlags {
  showRetailbot2History?: boolean;
}

export interface SystemStatusResponse {
  database: {
    status: string;
    tables: Record<string, number | null>;
    error: string | null;
  };
  heartbeats: Array<{
    collector: string;
    status: string;
    lastBeat: string | null;
  }>;
  binance: {
    spotApi: string;
    futuresApi: string;
    enableFuturesConfig: boolean;
  };
  loginRateLimiter?: LoginRateLimiterStatus;
  branding?: BrandingConfig;
  license?: LicenseStatus;
  uiFlags?: UIFeatureFlags;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function isTableCounts(value: unknown): value is Record<string, number | null> {
  return isRecord(value) && Object.values(value).every((count) =>
    count === null || (typeof count === "number" && Number.isFinite(count) && count >= 0));
}
function isPublicSystemStatus(value: unknown): value is SystemStatusResponse {
  if (!isRecord(value) || !isRecord(value.database) || !isNonEmptyString(value.database.status) ||
      !isTableCounts(value.database.tables) ||
      !(value.database.error === null || typeof value.database.error === "string") ||
      !Array.isArray(value.heartbeats) || !value.heartbeats.every((heartbeat) =>
        isRecord(heartbeat) && isNonEmptyString(heartbeat.collector) &&
        isNonEmptyString(heartbeat.status) &&
        (heartbeat.lastBeat === null || typeof heartbeat.lastBeat === "string")) ||
      !isRecord(value.binance)) return false;
  const limiter = value.loginRateLimiter;
  if (limiter !== undefined &&
      (!isRecord(limiter) || !isNonEmptyString(limiter.mode) || typeof limiter.degraded !== "boolean")) return false;
  const uiFlags = value.uiFlags;
  if (uiFlags !== undefined &&
      (!isRecord(uiFlags) || (uiFlags.showRetailbot2History !== undefined && typeof uiFlags.showRetailbot2History !== "boolean"))) {
    return false;
  }
  return isNonEmptyString(value.binance.spotApi) &&
    isNonEmptyString(value.binance.futuresApi) &&
    typeof value.binance.enableFuturesConfig === "boolean";
}

/** Read the sanitized, authenticated status endpoint and reject malformed payloads before rendering. */
export async function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatusResponse> {
  const payload = await getJson<unknown>("/api/system/status", signal);
  if (!isPublicSystemStatus(payload)) {
    throw new Error("System status returned an invalid response");
  }
  return payload;
}

export interface AdminSystemStatusResponse {
  database: {
    status: string;
    host: string;
    port: number | string;
    database: string;
    tables: Record<string, number | null>;
    error: string | null;
  };
  heartbeats: Array<{
    collector: string;
    status: string;
    detail?: string | null;
    lastBeat: string | null;
  }>;
  binance: SystemStatusResponse["binance"];
  loginRateLimiter?: LoginRateLimiterStatus;
  system?: { timezone?: string };
  uiFlags?: UIFeatureFlags;
}
function isAdminSystemStatus(value: unknown): value is AdminSystemStatusResponse {
  if (!isRecord(value) || !isRecord(value.database) ||
      !isNonEmptyString(value.database.status) || !isNonEmptyString(value.database.host) ||
      !(typeof value.database.port === "number" && Number.isFinite(value.database.port) ||
        isNonEmptyString(value.database.port)) ||
      !isNonEmptyString(value.database.database) || !isTableCounts(value.database.tables) ||
      !(value.database.error === null || typeof value.database.error === "string") ||
      !Array.isArray(value.heartbeats) || !value.heartbeats.every((heartbeat) =>
        isRecord(heartbeat) && isNonEmptyString(heartbeat.collector) &&
        isNonEmptyString(heartbeat.status) &&
        (heartbeat.detail === undefined || heartbeat.detail === null || typeof heartbeat.detail === "string") &&
        (heartbeat.lastBeat === null || typeof heartbeat.lastBeat === "string")) ||
      !isRecord(value.binance) ||
      !isNonEmptyString(value.binance.spotApi) ||
      !isNonEmptyString(value.binance.futuresApi) ||
      typeof value.binance.enableFuturesConfig !== "boolean") return false;
  const limiter = value.loginRateLimiter;
  if (limiter !== undefined &&
      (!isRecord(limiter) || !isNonEmptyString(limiter.mode) || typeof limiter.degraded !== "boolean")) return false;
  return value.system === undefined ||
    (isRecord(value.system) &&
      (value.system.timezone === undefined || typeof value.system.timezone === "string"));
}

/** Admin-only diagnostic endpoint; callers must gate this helper by role. */
export async function fetchAdminSystemStatus(signal?: AbortSignal): Promise<AdminSystemStatusResponse> {
  const status = await getJson<unknown>("/api/settings/status", signal);
  if (!isAdminSystemStatus(status)) {
    throw new Error("Admin system status returned an invalid response");
  }
  return status;
}
