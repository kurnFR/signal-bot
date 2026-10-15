# Wallet Tracking

Finds on-chain wallets that are accumulating or distributing large USD amounts of a token, lets you monitor them, and shows (and optionally alerts on) what they do next. It is a **research signal**, not a trade recommendation.

## 1. How it works

```
GeckoTerminal ─▶ hot pools ─▶ large swaps ─▶ wallet_swaps ─▶ aggregate per wallet/pool ─▶ candidates
(Birdeye fallback)                                                     │
                                                                       ├▶ token flow (per pool)
                                                                       └▶ quality-gated follow ─▶ monitored wallets ─▶ live feed + Telegram
```

Run it with `python3 run_all.py` (process `wallet_tracker`) or `python3 run_wallet_tracker.py`. Tables are created automatically at startup. Only one worker runs at a time: it holds a database lock, verifies it every cycle, and stops if the lock is lost.

**Data source.** GeckoTerminal's public API (no key). Each cycle, for each network (default `eth`, `solana`, `base`, `bsc`; `arbitrum` and `polygon_pos` also supported) it picks trending and top pools, skips pools whose base token is a stablecoin and pools below the volume floor (the larger of $100K and 10× the minimum trade size), then reads recent trades at or above the minimum trade size. Requests are paced and a 429 backs off (honouring `Retry-After`) with a per-network cooldown, and networks are scanned in rotating order so one rate-limited chain cannot starve the rest. Pool lists are cached so a short outage does not empty the scan.

**Optional fallback.** If `BIRDEYE_API_KEY` is set, Birdeye's free-tier pair trades are used for a network that GeckoTerminal cannot serve. See the limitations below before enabling it.

## 2. Outputs

All aggregates are computed in Python from stored swaps for 1h, 6h, 24h and 7d windows (stored swaps are kept 8 days; a 7d window is only complete after 7 days of uptime).

| Output | Definition |
|---|---|
| **Candidate wallets** | Per wallet and pool: buy and sell USD and tokens, volume-weighted average buy/sell price, trade counts, first and last trade time. A wallet is a candidate when \|buys − sells\| reaches the minimum net USD. "Token Bias" follows net tokens; "Net Cash Flow" is net USD, and the two can differ when prices moved within the window. |
| **Token flow** | Per pool: wallets buying vs selling and combined USD. Labelled `ACCUMULATION` or `DISTRIBUTION` only with at least 2 wallets, a wallet majority **and** net USD pointing the same way; otherwise `MIXED`. |
| **Live feed** | Latest swaps by monitored wallets, refreshed in the dashboard every 15 s. Activity is polled (about every 5 minutes), not real time. |
| **Follow (monitored list)** | Wallets you add by hand, plus wallets that pass the quality gate below. |

**Quality gate for auto-follow** (24h and 7d windows, per wallet and pool, never netting across different pools): total volume at least the larger of the auto-follow threshold and 2× the minimum net; at least 3 trades; net/total ratio at least 0.25; rejected if above 20 trades per hour *and* ratio under 0.5; quality score at least 50, where the score is 30·net strength + 25·volume strength + 25·net ratio + 20·activity. It ranks candidates and is not a probability of profit.

**Follow lifecycle.** Auto-followed wallets that stop qualifying become `INACTIVE` after `WALLET_TRACKER_AUTO_INACTIVE_DAYS` (default 7) and are re-activated if they qualify again. Manually followed wallets never expire. A wallet you remove stays removed (`IGNORED`); toggling is atomic.

## 3. Telegram alerts

Needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Alerts go out for new swaps by monitored wallets that are at least the minimum trade size and **no older than 30 minutes** (backfilled history is never alerted). At most one alert per wallet per cooldown window (default 10 minutes). Delivery uses a durable queue (`PENDING → SENT/FAILED`) with bounded exponential retry, Telegram's `retry_after` is honoured, messages are HTML-escaped, and failures log only the error type and HTTP status, never the bot token. Sent and failed rows are pruned after 30 days.

## 4. Settings

Stored in the `wallet_tracker_settings` table and edited in the dashboard (**Wallet Tracking → Settings**, trader/admin role). Once saved there, they override environment values; `WALLET_TRACKER_*` variables only provide the starting defaults.

| Setting | Default | Notes |
|---|---|---|
| `networks` | eth, solana, base, bsc | |
| `pools_per_network` | 4 | |
| `min_trade_usd` | 10,000 | Smaller swaps are ignored |
| `min_net_usd` | 150,000 | Candidate threshold; must be above 0 |
| `auto_track_net_usd` | 500,000 | Must be at least `min_net_usd` |
| `poll_interval_seconds` | 300 | |
| `api_delay_seconds` | 4.0 | Pause between API calls |
| `retention_days` | 8 | Swap retention |
| token filter | all | Optionally restrict to selected token addresses |

## 5. API

All under `/api/wallets`, authenticated; changing data requires the trader or admin role.

`GET /meta`, `/settings`, `/status`, `/positions`, `/token-flow`, `/events`, `/tracked` · `PUT /settings` · `POST /tracked`, `/tracked/toggle` · `DELETE /tracked`, `/tracked/{id}`.

## 6. Known limitations (verified open)

- **Alert digest.** The cooldown keeps only the first swap of a burst and drops the rest. A wallet that bought $1.26M in four swaps produced one alert reporting $12,000.
- **Per-pool, not per-token.** The same token bought across two pools is counted separately, so a wallet can fall below the follow threshold that its combined position would pass.
- **Stablecoin filter metadata.** Pool requests do not ask for token symbols, so the filter probably relies only on a short list of known stablecoin addresses. Unlisted stablecoins may pass.
- **Bots and market makers.** The churn gate rejects only more than 20 trades per hour with weak directionality; bot-like wallets at 8–20 trades per hour can still score 83–90 and be followed. The tracked address is the transaction sender, which can be a bot or aggregator.
- **Size is not skill.** There is no profit-and-loss or track-record scoring; large flow does not mean a wallet is profitable.
- **Absolute thresholds.** Dollar floors favour large caps; "huge" is not relative to pool liquidity, and pools under the volume floor are skipped.
- **Coverage.** A wallet is only visible in pools the scanner picks; its trades elsewhere are invisible.
- **Birdeye fallback is unvalidated against the live API.** The same swap may arrive under a different wallet (owner vs transaction sender) and be counted twice, rows the parser cannot map are dropped without a warning, and only the latest 50 swaps per pool are read. Leave it off until one overlapping pool has been compared across both sources.
- **Startup privilege.** `ensure_tables()` runs an `ALTER TABLE`, so a database user without ALTER makes the worker exit at start.
