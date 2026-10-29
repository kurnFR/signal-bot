import { getJson } from "./api";

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
  system?: { timezone?: string };
}
/** Admin-only diagnostic endpoint; callers must gate this helper by role. */
export async function fetchAdminSystemStatus(signal?: AbortSignal): Promise<AdminSystemStatusResponse> {
  const status = await getJson<AdminSystemStatusResponse>("/api/settings/status", signal);
  if (!status || !status.database || typeof status.database.status !== "string" ||
      typeof status.database.host !== "string" ||
      !(typeof status.database.port === "number" || typeof status.database.port === "string") ||
      typeof status.database.database !== "string" ||
      !status.database.tables || typeof status.database.tables !== "object" ||
      !Array.isArray(status.heartbeats) || !status.binance) {
    throw new Error("Admin system status returned an invalid response");
  }
  return status;
}
