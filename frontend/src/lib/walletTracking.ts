import { assertApiPath, getJson } from "./api";

export class WalletActionError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "WalletActionError";
    this.status = status;
  }
}

function tokenFromStorage(): string | null {
  try {
    const token = window.localStorage.getItem("token");
    if (!token) return null;
    const trimmed = token.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

async function walletMutationRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  assertApiPath(path);
  const token = tokenFromStorage();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 401) {
    try { window.localStorage.removeItem("token"); } catch { /* Session expired reset */ }
    window.dispatchEvent(new Event("signal-bot:session-expired"));
    throw new WalletActionError(401, "Your session has expired. Sign in again.");
  }

  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`;
    if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.trim().length > 0) {
      message = payload.detail.trim();
    }
    throw new WalletActionError(response.status, message);
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new WalletActionError(502, "Server returned a non-JSON response");
  }

  return payload as T;
}

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
  id?: number;
  last_cycle_at?: string | null;
  last_cycle_seconds?: number | null;
  pools_scanned?: number;
  swaps_ingested?: number;
  candidates?: number;
  tracked_wallets?: number;
  swaps_24h?: number;
  enabled: boolean;
  networks: string[];
  birdeye_fallback_active: boolean;
  pool_discovery_sources: string[];
  trade_sources: string[];
}
export interface TrackedWallet {
  id: number;
  chain: string;
  wallet: string;
  label?: string | null;
  source?: string;
  status: string;
  first_flagged_at?: string | null;
  last_seen_at?: string | null;
  last_net_usd?: number | null;
  chain_label?: string;
  explorer_url?: string;
}
export interface WalletPosition {
  id: number;
  chain: string;
  wallet: string;
  pool_address: string;
  pair: string;
  period_hours: number;
  side: string;
  bias: string;
  buy_usd: number;
  sell_usd: number;
  net_usd: number;
  total_usd: number;
  buy_tokens: number;
  sell_tokens: number;
  net_tokens: number;
  avg_buy_price: number | null;
  avg_sell_price: number | null;
  avg_price: number | null;
  trade_count: number;
  buy_count: number;
  sell_count: number;
  first_trade_at: string;
  last_trade_at: string;
  updated_at: string;
  tracked_id?: number | null;
  tracked_label?: string | null;
  is_tracked: boolean;
  label?: string | null;
  chain_label?: string;
  explorer_url?: string;
}
export interface WalletTokenFlow {
  chain: string;
  pool_address: string;
  pair: string;
  accumulating_wallets: number;
  distributing_wallets: number;
  buy_usd: number;
  sell_usd: number;
  net_usd: number;
  signal: string;
  chain_label?: string;
}
export interface WalletEvent {
  id: number;
  tx_hash: string;
  chain: string;
  wallet: string;
  pair: string;
  pool_address: string;
  side: string;
  usd_value: number;
  token_amount: number;
  price_usd: number;
  block_time: string;
  label?: string | null;
  chain_label?: string;
  explorer_url?: string;
  tx_url?: string;
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isString(value: unknown): value is string {
  return typeof value === "string";
}
function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.trim().length > 0;
}
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function isCount(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}
function nullableString(value: unknown): boolean {
  return value === null || isString(value);
}
function nullableFiniteNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}
function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}
function validMeta(value: unknown): value is WalletMeta {
  return isRecord(value) && Array.isArray(value.periods_hours) &&
    value.periods_hours.every((item) => isCount(item) && item > 0) &&
    isRecord(value.chains) && Object.values(value.chains).every(isString) &&
    isFiniteNumber(value.min_net_usd) && value.min_net_usd >= 0 &&
    isFiniteNumber(value.auto_track_net_usd) && value.auto_track_net_usd >= 0;
}
function validSettings(value: unknown): value is WalletSettings {
  if (!isRecord(value) || typeof value.enabled !== "boolean" || !stringArray(value.networks) ||
      !isCount(value.pools_per_network) || value.pools_per_network < 1 ||
      !isFiniteNumber(value.min_trade_usd) || value.min_trade_usd < 0 ||
      !isFiniteNumber(value.min_net_usd) || value.min_net_usd < 0 ||
      !isFiniteNumber(value.auto_track_net_usd) || value.auto_track_net_usd < 0 ||
      !isCount(value.poll_interval_seconds) || value.poll_interval_seconds < 1 ||
      !isCount(value.retention_days) || value.retention_days < 1 ||
      !isFiniteNumber(value.api_delay_seconds) || value.api_delay_seconds < 0 ||
      (value.token_mode !== "all" && value.token_mode !== "selected") ||
      !stringArray(value.token_addresses)) return false;
  return (value.birdeye_api_key_masked === undefined || isString(value.birdeye_api_key_masked)) &&
    (value.has_birdeye_key === undefined || typeof value.has_birdeye_key === "boolean");
}
function validStatus(value: unknown): value is WalletStatus {
  if (!isRecord(value) || typeof value.enabled !== "boolean" || !stringArray(value.networks) ||
      typeof value.birdeye_fallback_active !== "boolean" ||
      !stringArray(value.pool_discovery_sources) || !stringArray(value.trade_sources)) return false;
  if (value.id !== undefined && !isCount(value.id)) return false;
  if (value.last_cycle_at !== undefined && !nullableString(value.last_cycle_at)) return false;
  if (value.last_cycle_seconds !== undefined && !nullableFiniteNumber(value.last_cycle_seconds)) return false;
  return ["pools_scanned", "swaps_ingested", "candidates", "tracked_wallets", "swaps_24h"]
    .every((key) => value[key] === undefined || isCount(value[key]));
}
function validTrackedWallet(value: unknown): value is TrackedWallet {
  if (!isRecord(value) || !isCount(value.id) || !isNonEmptyString(value.chain) ||
      !isNonEmptyString(value.wallet) || !isNonEmptyString(value.status)) return false;
  if (value.label !== undefined && !nullableString(value.label)) return false;
  if (value.source !== undefined && !isString(value.source)) return false;
  if (value.first_flagged_at !== undefined && !nullableString(value.first_flagged_at)) return false;
  if (value.last_seen_at !== undefined && !nullableString(value.last_seen_at)) return false;
  if (value.last_net_usd !== undefined && !nullableFiniteNumber(value.last_net_usd)) return false;
  return (value.chain_label === undefined || isString(value.chain_label)) &&
    (value.explorer_url === undefined || isString(value.explorer_url));
}
function validWalletPosition(value: unknown): value is WalletPosition {
  if (!isRecord(value) || !isCount(value.id) || !isNonEmptyString(value.chain) ||
      !isNonEmptyString(value.wallet) || !isNonEmptyString(value.pool_address) ||
      !isNonEmptyString(value.pair) || !isCount(value.period_hours) || value.period_hours < 1 ||
      !isNonEmptyString(value.side) || !isNonEmptyString(value.bias) ||
      !["buy_usd", "sell_usd", "net_usd", "total_usd", "buy_tokens", "sell_tokens", "net_tokens"]
        .every((key) => isFiniteNumber(value[key])) ||
      !["trade_count", "buy_count", "sell_count"].every((key) => isCount(value[key])) ||
      !isString(value.first_trade_at) || !isString(value.last_trade_at) || !isString(value.updated_at) ||
      typeof value.is_tracked !== "boolean") return false;
  if (!["avg_buy_price", "avg_sell_price", "avg_price"].every((key) => nullableFiniteNumber(value[key]))) return false;
  if (value.tracked_id !== undefined && value.tracked_id !== null && !isCount(value.tracked_id)) return false;
  for (const key of ["tracked_label", "label"] as const) {
    if (value[key] !== undefined && !nullableString(value[key])) return false;
  }
  return (value.chain_label === undefined || isString(value.chain_label)) &&
    (value.explorer_url === undefined || isString(value.explorer_url));
}
function validWalletTokenFlow(value: unknown): value is WalletTokenFlow {
  return isRecord(value) && isNonEmptyString(value.chain) && isNonEmptyString(value.pool_address) &&
    isNonEmptyString(value.pair) && isCount(value.accumulating_wallets) &&
    isCount(value.distributing_wallets) && isFiniteNumber(value.buy_usd) &&
    isFiniteNumber(value.sell_usd) && isFiniteNumber(value.net_usd) &&
    isNonEmptyString(value.signal) && (value.chain_label === undefined || isString(value.chain_label));
}
function validWalletEvent(value: unknown): value is WalletEvent {
  if (!isRecord(value) || !isCount(value.id) || !isNonEmptyString(value.tx_hash) ||
      !isNonEmptyString(value.chain) || !isNonEmptyString(value.wallet) ||
      !isNonEmptyString(value.pair) || !isNonEmptyString(value.pool_address) ||
      !isNonEmptyString(value.side) || !isFiniteNumber(value.usd_value) ||
      !isFiniteNumber(value.token_amount) || !isFiniteNumber(value.price_usd) ||
      !isString(value.block_time)) return false;
  if (value.label !== undefined && !nullableString(value.label)) return false;
  return (value.chain_label === undefined || isString(value.chain_label)) &&
    (value.explorer_url === undefined || isString(value.explorer_url)) &&
    (value.tx_url === undefined || isString(value.tx_url));
}

/** Read-only Wallet Tracking snapshot. Validate backend-shaped rows; every request uses GET. */
export async function fetchWalletTrackingSnapshot(signal?: AbortSignal): Promise<WalletTrackingSnapshot> {
  const [meta, settings, status, tracked, positions, flows, events] = await Promise.all([
    getJson<unknown>("/api/wallets/meta", signal),
    getJson<unknown>("/api/wallets/settings", signal),
    getJson<unknown>("/api/wallets/status", signal),
    getJson<unknown>("/api/wallets/tracked", signal),
    getJson<unknown>("/api/wallets/positions?period_hours=24&limit=20", signal),
    getJson<unknown>("/api/wallets/token-flow?period_hours=24&limit=10", signal),
    getJson<unknown>("/api/wallets/events?limit=20", signal),
  ]);
  if (!validMeta(meta)) throw new Error("Wallet Tracking meta returned an invalid response");
  if (!validSettings(settings)) throw new Error("Wallet Tracking settings returned an invalid response");
  if (!validStatus(status)) throw new Error("Wallet Tracking status returned an invalid response");
  if (!isRecord(tracked) || !Array.isArray(tracked.tracked) || !tracked.tracked.every(validTrackedWallet)) {
    throw new Error("Wallet Tracking tracked wallets returned an invalid response");
  }
  if (!isRecord(positions) || !Array.isArray(positions.positions) || !positions.positions.every(validWalletPosition)) {
    throw new Error("Wallet Tracking positions returned an invalid response");
  }
  if (!isRecord(flows) || !Array.isArray(flows.flows) || !flows.flows.every(validWalletTokenFlow)) {
    throw new Error("Wallet Tracking token flow returned an invalid response");
  }
  if (!isRecord(events) || !Array.isArray(events.events) || !events.events.every(validWalletEvent)) {
    throw new Error("Wallet Tracking events returned an invalid response");
  }
  return {
    meta,
    settings,
    status,
    tracked: tracked.tracked,
    positions: positions.positions,
    flows: flows.flows,
    events: events.events,
  };
}

export interface WalletSettingsRequest {
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
  birdeye_api_key?: string;
}

/** Add a wallet address to the monitored Smart Money checklist. */
export async function trackWallet(
  chain: string,
  wallet: string,
  label?: string | null,
  signal?: AbortSignal,
): Promise<{ status: string; chain: string; wallet: string }> {
  if (!isNonEmptyString(chain) || !isNonEmptyString(wallet)) {
    throw new WalletActionError(400, "Chain and wallet address are required");
  }
  const payload = await walletMutationRequest<unknown>("/api/wallets/tracked", {
    method: "POST",
    body: JSON.stringify({ chain: chain.trim().toLowerCase(), wallet: wallet.trim(), label: label?.trim() || null }),
    signal,
  });
  if (!isRecord(payload) || payload.status !== "ok" || typeof payload.chain !== "string" || typeof payload.wallet !== "string") {
    throw new WalletActionError(502, "Server returned an invalid wallet track response");
  }
  return { status: "ok", chain: payload.chain, wallet: payload.wallet };
}

/** Stop monitoring a wallet by removing it from the Smart Money checklist. */
export async function untrackWallet(
  trackedId: number,
  signal?: AbortSignal,
): Promise<{ status: string; tracked_id: number }> {
  if (!isCount(trackedId) || trackedId <= 0) {
    throw new WalletActionError(400, "Invalid tracked wallet ID");
  }
  const payload = await walletMutationRequest<unknown>(`/api/wallets/tracked/${encodeURIComponent(trackedId)}`, {
    method: "DELETE",
    signal,
  });
  if (!isRecord(payload) || payload.status !== "ok" || payload.tracked_id !== trackedId) {
    throw new WalletActionError(502, "Server returned an invalid wallet untrack response");
  }
  return { status: "ok", tracked_id: trackedId };
}

/** Save wallet scanner and alert threshold settings. Outbound alerts require explicit user confirmation. */
export async function saveWalletSettings(
  settings: WalletSettingsRequest,
  signal?: AbortSignal,
): Promise<WalletSettings> {
  if (!Array.isArray(settings.networks) || settings.networks.length === 0) {
    throw new WalletActionError(400, "Select at least one supported network");
  }
  if (!isFiniteNumber(settings.min_trade_usd) || settings.min_trade_usd <= 0) {
    throw new WalletActionError(400, "Minimum trade size must be greater than zero");
  }
  if (!isFiniteNumber(settings.min_net_usd) || settings.min_net_usd <= 0) {
    throw new WalletActionError(400, "Minimum net flow must be greater than zero");
  }
  if (!isFiniteNumber(settings.auto_track_net_usd) || settings.auto_track_net_usd < settings.min_net_usd) {
    throw new WalletActionError(400, "Auto-track threshold must be greater than or equal to minimum net flow");
  }
  const payload = await walletMutationRequest<unknown>("/api/wallets/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
    signal,
  });
  if (!validSettings(payload)) {
    throw new WalletActionError(502, "Server returned invalid wallet settings after save");
  }
  return payload;
}
