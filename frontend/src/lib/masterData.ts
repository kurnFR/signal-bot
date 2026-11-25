import { assertApiPath, getJson, apiRequest, ApiError } from "./api";

function validCryptoTicker(value: unknown): value is CryptoMarketTicker {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.symbol === "string" && typeof item.baseAsset === "string" && typeof item.quoteAsset === "string" &&
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
  featureCount: number; hasFeatures: boolean; minOpenTime: number | string | null;
  maxCloseTime: number | string | null; startDate: string | null; endDate: string | null;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function isCount(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}
function validCoverageSummary(value: unknown): value is CoverageSummary {
  return isRecord(value) && isCount(value.totalCandles) &&
    isCount(value.distinctSymbols) && isCount(value.coverageEntries);
}
function validCoverageRow(value: unknown): value is CoverageRow {
  if (!isRecord(value) || !isNonEmptyString(value.symbol) || !isNonEmptyString(value.market) ||
      !isNonEmptyString(value.timeframe) || !isCount(value.candleCount) ||
      !isCount(value.featureCount) || typeof value.hasFeatures !== "boolean") return false;
  return ["minOpenTime", "maxCloseTime", "startDate", "endDate"].every((key) =>
    value[key] === null || typeof value[key] === "string" || isFiniteNumber(value[key]));
}
function validJobTimestamp(value: unknown): boolean {
  return typeof value === "string" || isFiniteNumber(value);
}
function validMasterDataJob(value: unknown): value is MasterDataJob {
  if (!isRecord(value) || !isNonEmptyString(value.type) || !isNonEmptyString(value.symbol) ||
      !isNonEmptyString(value.market) || !isNonEmptyString(value.timeframe) ||
      !isNonEmptyString(value.status) || !isFiniteNumber(value.progress) ||
      value.progress < 0 || value.progress > 100) return false;
  for (const key of ["id", "job_id"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "string") return false;
  }
  if (value.message !== undefined && typeof value.message !== "string") return false;
  if (value.error !== undefined && value.error !== null && typeof value.error !== "string") return false;
  if (value.created_at !== undefined && !validJobTimestamp(value.created_at)) return false;
  if (value.updated_at !== undefined && !validJobTimestamp(value.updated_at)) return false;
  return value.candles_fetched === undefined || isCount(value.candles_fetched);
}

/** Read dataset coverage and job status; malformed rows fail closed before rendering. */
export async function fetchMasterDataSnapshot(signal?: AbortSignal): Promise<MasterDataSnapshot> {
  const [coverage, jobs] = await Promise.all([
    getJson<unknown>("/api/master-data/coverage", signal),
    getJson<unknown>("/api/master-data/jobs", signal),
  ]);
  if (!isRecord(coverage) || !validCoverageSummary(coverage.summary) ||
      !Array.isArray(coverage.coverage) || !coverage.coverage.every(validCoverageRow)) {
    throw new Error("Master Data coverage returned an invalid response");
  }
  if (!isRecord(jobs) || !Array.isArray(jobs.jobs) || !jobs.jobs.every(validMasterDataJob)) {
    throw new Error("Master Data jobs returned an invalid response");
  }
  return {
    summary: coverage.summary,
    coverage: coverage.coverage,
    jobs: jobs.jobs,
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
  try {
    const payload = await apiRequest<unknown>(path, {
      method: "POST",
      json: body,
    });
    if (!payload || typeof payload !== "object" || !("id" in payload) || typeof payload.id !== "string") {
      throw new MasterDataActionError(200, "Master Data returned an invalid job response");
    }
    return payload as MasterDataJob;
  } catch (err) {
    if (err instanceof ApiError) {
      const detail = err.detail?.slice(0, 300) || err.message;
      throw new MasterDataActionError(err.status, detail);
    }
    throw err;
  }
}

/** Explicitly starts a backfill job; callers must gate this action by role and confirmation. */
export function triggerMasterDataBackfill(input: BackfillInput): Promise<MasterDataJob> {
  return postMasterDataJob("/api/master-data/backfill", input);
}

/** Explicitly starts a feature-build job; callers must gate this action by role and confirmation. */
export function triggerMasterDataFeatureBuild(input: FeatureBuildInput): Promise<MasterDataJob> {
  return postMasterDataJob("/api/master-data/build-features", input);
}

/** Fetch distinct symbols that have backfilled candle data (candleCount > 0) in the database. */
export async function fetchBackfilledSymbols(signal?: AbortSignal): Promise<string[]> {
  try {
    const data = await getJson<{ summary?: unknown; coverage?: CoverageRow[] }>("/api/master-data/coverage", signal);
    if (!data || !Array.isArray(data.coverage)) return ["BTCUSDT"];
    const symbols = Array.from(
      new Set(
        data.coverage
          .filter((row) => row && typeof row.symbol === "string" && row.symbol.trim() && (row.candleCount ?? 0) > 0)
          .map((row) => row.symbol.trim().toUpperCase())
      )
    ).sort();
    return symbols.length > 0 ? symbols : ["BTCUSDT"];
  } catch {
    return ["BTCUSDT"];
  }
}

export interface DeleteDatasetInput {
  symbol: string;
  market: "spot" | "futures";
  timeframe: string;
  /** "features" removes rebuildable computed features only; "all" also removes candles. */
  scope: "features" | "all";
}

export interface DeleteDatasetResult {
  features_deleted: number;
  candles_deleted: number;
}

/** Admin-only. The server refuses (409) while an active deployment or running job uses the dataset. */
export async function deleteMasterDataset(input: DeleteDatasetInput): Promise<DeleteDatasetResult> {
  const qs = new URLSearchParams({ symbol: input.symbol, market: input.market, timeframe: input.timeframe, scope: input.scope });
  try {
    const payload = await apiRequest<Partial<DeleteDatasetResult>>(`/api/master-data/dataset?${qs.toString()}`, { method: "DELETE" });
    return { features_deleted: Number(payload?.features_deleted ?? 0), candles_deleted: Number(payload?.candles_deleted ?? 0) };
  } catch (err) {
    if (err instanceof ApiError) throw new MasterDataActionError(err.status, err.detail?.slice(0, 300) || err.message);
    throw err;
  }
}
