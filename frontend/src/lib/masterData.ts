import { assertApiPath, getJson } from "./api";

function validCryptoTicker(value: unknown): value is CryptoMarketTicker {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.symbol === "string" &&
    (item.market === "spot" || item.market === "futures") &&
    ["lastPrice", "priceChangePercent", "highPrice", "lowPrice", "volume", "quoteVolume"].every((key) =>
      typeof item[key] === "number" && Number.isFinite(item[key]));
}
export async function searchCryptoSymbols(
  query: string,
  market: "all" | "spot" | "futures" = "all",
  signal?: AbortSignal,
): Promise<CryptoSearchResponse> {
  const params = new URLSearchParams({ q: query.trim(), market, limit: "20" });
  const path = `/api/crypto/search?${params.toString()}`;
  assertApiPath(path);
  const response = await getJson<CryptoSearchResponse>(path, signal);
  if (!response || !Array.isArray(response.results) || !response.results.every(validCryptoTicker)) {
    throw new Error("Crypto search returned an invalid response");
  }
  return response;
}
export async function fetchPopularCrypto(
  market: "spot" | "futures" = "spot",
  signal?: AbortSignal,
): Promise<PopularCryptoResponse> {
  const path = `/api/crypto/popular?market=${market}`;
  assertApiPath(path);
  const response = await getJson<PopularCryptoResponse>(path, signal);
  if (!response || !Array.isArray(response.items) || !response.items.every(validCryptoTicker)) {
    throw new Error("Popular crypto markets returned an invalid response");
  }
  return response;
}


export interface CryptoMarketTicker {
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  market: "spot" | "futures";
  lastPrice: number;
  priceChangePercent: number;
  highPrice: number;
  lowPrice: number;
  volume: number;
  quoteVolume: number;
}
export interface CryptoSearchResponse {
  query: string;
  market: string;
  count: number;
  results: CryptoMarketTicker[];
}
export interface PopularCryptoResponse {
  market: string;
  items: CryptoMarketTicker[];
}
export interface CoverageRow {
  symbol: string; market: string; timeframe: string; candleCount: number;
  featureCount: number; hasFeatures: boolean; minOpenTime: string | null;
  maxCloseTime: string | null; startDate: string | null; endDate: string | null;
}
export interface CoverageSummary {
  totalCandles: number; distinctSymbols: number; coverageEntries: number;
}
export interface MasterDataJob {
  id?: string; job_id?: string; type: string; symbol: string; market: string;
  timeframe: string; status: string; progress: number; message?: string;
  error?: string | null; created_at?: number | string; updated_at?: number | string;
  candles_fetched?: number;
}
export interface MasterDataSnapshot {
  summary: CoverageSummary; coverage: CoverageRow[]; jobs: MasterDataJob[];
}

/** Read dataset coverage and job status; explicit job mutations are defined separately below. */
export async function fetchMasterDataSnapshot(signal?: AbortSignal): Promise<MasterDataSnapshot> {
  const [coverage, jobs] = await Promise.all([
    getJson<{ summary: CoverageSummary; coverage: CoverageRow[] }>("/api/master-data/coverage", signal),
    getJson<{ jobs: MasterDataJob[] }>("/api/master-data/jobs", signal),
  ]);
  return {
    summary: coverage.summary,
    coverage: Array.isArray(coverage.coverage) ? coverage.coverage : [],
    jobs: Array.isArray(jobs.jobs) ? jobs.jobs : [],
  };
}

export interface BackfillInput {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
  years: number;
  auto_compute_features: boolean;
}

export interface FeatureBuildInput {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
}

export class MasterDataActionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "MasterDataActionError";
  }
}

async function postMasterDataJob(
  path: "/api/master-data/backfill" | "/api/master-data/build-features",
  body: BackfillInput | FeatureBuildInput,
): Promise<MasterDataJob> {
  assertApiPath(path);
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  try {
    const token = window.localStorage.getItem("token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // The server returns an authentication error when no token is available.
  }

  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(body),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = null; }

  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Auth state still resets via the event. */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail.slice(0, 300)
      : `Master Data job could not be started (${response.status})`;
    throw new MasterDataActionError(response.status, detail);
  }
  if (!payload || typeof payload !== "object" || !("id" in payload) || typeof payload.id !== "string") {
    throw new MasterDataActionError(response.status, "Master Data returned an invalid job response");
  }
  return payload as MasterDataJob;
}

/** Explicitly starts a backfill job; callers must gate this action by role and confirmation. */
export function triggerMasterDataBackfill(input: BackfillInput): Promise<MasterDataJob> {
  return postMasterDataJob("/api/master-data/backfill", input);
}

/** Explicitly starts a feature-build job; callers must gate this action by role and confirmation. */
export function triggerMasterDataFeatureBuild(input: FeatureBuildInput): Promise<MasterDataJob> {
  return postMasterDataJob("/api/master-data/build-features", input);
}
