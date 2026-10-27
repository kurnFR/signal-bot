import { getJson } from "./api";

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

/** Read-only dataset coverage and job status. No backfill or feature-build actions. */
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
