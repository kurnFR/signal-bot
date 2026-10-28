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

/** Read the sanitized, authenticated status endpoint already exposed by FastAPI. */
export function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatusResponse> {
  return getJson<SystemStatusResponse>("/api/system/status", signal);
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
