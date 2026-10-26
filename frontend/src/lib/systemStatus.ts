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
    lastBeat: string;
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
