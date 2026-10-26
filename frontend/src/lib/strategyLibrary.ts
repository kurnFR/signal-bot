import { getJson } from "./api";

export interface StrategyCondition {
  indicator?: string;
  operator?: string;
  value?: string | number;
}
export interface CustomStrategy {
  name: string;
  displayName?: string;
  description?: string;
  rationale?: string;
  longConditions?: StrategyCondition[];
  shortConditions?: StrategyCondition[];
  slAtrMult?: number;
  riskRewardRatio?: number;
  useTrailingStop?: boolean;
  trailActivationR?: number;
  trailDistanceAtrMult?: number;
}
export interface StrategyLibraryResponse { strategies: CustomStrategy[] }
export interface StrategyIndicator {
  name: string;
  label?: string;
  category?: string;
  description?: string;
  default?: number;
  type?: string;
}
export interface StrategyIndicatorsResponse {
  indicators: StrategyIndicator[];
  operators: string[];
}
export function fetchCustomStrategies(signal?: AbortSignal) {
  return getJson<StrategyLibraryResponse>("/api/custom-strategy/list", signal);
}
export function fetchStrategyIndicators(signal?: AbortSignal) {
  return getJson<StrategyIndicatorsResponse>("/api/custom-strategy/indicators", signal);
}
