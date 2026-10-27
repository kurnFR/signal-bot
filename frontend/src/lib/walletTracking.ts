import { getJson } from "./api";

export interface WalletMeta {
  periods_hours: number[];
  chains: Record<string, string>;
  min_net_usd: number;
  auto_track_net_usd: number;
}
export interface WalletSettings {
  enabled: boolean;
  networks: string[];
  pools_per_network: number;
  min_trade_usd: number;
  min_net_usd: number;
  auto_track_net_usd: number;
  poll_interval_seconds: number;
  retention_days: number;
  api_delay_seconds: number;
  token_mode: "all" | "selected";
  token_addresses: string[];
  birdeye_api_key_masked?: string;
  has_birdeye_key?: boolean;
}
export interface WalletStatus {
  enabled?: boolean;
  service_healthy?: boolean;
  last_scan?: string | null;
  tracked_count?: number;
  networks?: string[];
  birdeye_fallback_active?: boolean;
  pool_discovery_sources?: string[];
  trade_sources?: string[];
  [key: string]: unknown;
}
export interface TrackedWallet {
  id: number;
  chain: string;
  wallet: string;
  label?: string | null;
  status: string;
  created_at?: string | null;
}
export interface WalletPosition {
  token_address?: string;
  token_symbol?: string;
  chain: string;
  chain_label?: string;
  net_usd: number;
  trade_count: number;
  side?: string;
}
export interface WalletTokenFlow {
  token_symbol: string;
  chain: string;
  chain_label?: string;
  inflow_usd: number;
  outflow_usd: number;
  net_flow_usd: number;
}
export interface WalletEvent {
  id: number;
  tx_hash: string;
  chain: string;
  wallet: string;
  token_in: string;
  token_out: string;
  amount_usd: number;
  timestamp: string;
  side?: string;
}
export interface WalletTrackingSnapshot {
  meta: WalletMeta;
  settings: WalletSettings;
  status: WalletStatus;
  tracked: TrackedWallet[];
  positions: WalletPosition[];
  flows: WalletTokenFlow[];
  events: WalletEvent[];
}

/** Read-only Wallet Tracking snapshot. Every request uses GET; no tracking/settings mutations. */
export async function fetchWalletTrackingSnapshot(signal?: AbortSignal): Promise<WalletTrackingSnapshot> {
  const [meta, settings, status, tracked, positions, flows, events] = await Promise.all([
    getJson<WalletMeta>("/api/wallets/meta", signal),
    getJson<WalletSettings>("/api/wallets/settings", signal),
    getJson<WalletStatus>("/api/wallets/status", signal),
    getJson<{ tracked: TrackedWallet[] }>("/api/wallets/tracked", signal),
    getJson<{ positions: WalletPosition[] }>("/api/wallets/positions?period_hours=24&limit=20", signal),
    getJson<{ flows: WalletTokenFlow[] }>("/api/wallets/token-flow?period_hours=24&limit=10", signal),
    getJson<{ events: WalletEvent[] }>("/api/wallets/events?limit=20", signal),
  ]);
  return {
    meta,
    settings,
    status,
    tracked: Array.isArray(tracked.tracked) ? tracked.tracked : [],
    positions: Array.isArray(positions.positions) ? positions.positions : [],
    flows: Array.isArray(flows.flows) ? flows.flows : [],
    events: Array.isArray(events.events) ? events.events : [],
  };
}
