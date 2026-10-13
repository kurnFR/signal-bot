# Wallet Tracker — Follow-up Review

**Repository:** `kurnFR/signal-bot`  
**Reviewed:** 2026-10-14  
**Baseline:** `master` at `9b070f3` (PR #20 merge)  
**Follow-up branch:** `test/wallet-tracker-mariadb-concurrency`, PR [#21](https://github.com/kurnFR/signal-bot/pull/21)  
**Scope:** Wallet Tracker analyzer, store, worker, routes, UI/docs, tests and CI.

## 1. Current status

The earlier review document had become stale: it listed several issues as open after fixes had already landed. This version separates fixes present on `master`, the additional fix proposed in PR #21, and risks that still need work or live validation.

### Verification on PR #21

- Full repository test workflow: **207 passed, 3 skipped, 15 warnings**.
- Wallet Tracker MariaDB 10.11 integration job: **3 passed**.
- Screener/unit workflow and RetailBot accounting regression workflow: passed on the reviewed PR head.
- The first real concurrency run reproduced MariaDB error **1213 (deadlock)** in the pre-fix toggle implementation. PR #21 replaces that path with a per-wallet advisory lock plus a transaction; the follow-up integration run passed.

These results validate the CI test environment only. They do not establish production readiness or validate live GeckoTerminal behavior.

## 2. Fixes verified in the current code

| Area | Current behavior | Verification / limitation |
|---|---|---|
| Token-flow direction (H1) | Requires a minimum wallet count and keeps the headline signal `MIXED` when the wallet majority conflicts with aggregate net USD. | Regression tests cover conflicting flow and one-wallet cases. |
| Freshness of Telegram alerts (H2) | Queueing filters old swaps rather than presenting provider backfill as live activity. | Covered by tests; live-provider timing remains unverified. |
| Telegram credential redaction (H3) | Send failures avoid logging the full bot-token URL. | Code/tests reviewed; rotate any token previously exposed in shared logs. |
| Network rate limits (M1) | Network rotation and per-network cooldown reduce the chance that one 429 starves all networks. | Unit/mock coverage; provider behavior still needs live observation. |
| Follow quality (M2/M3) | Candidate qualification uses pool/token-specific flows and cautiously rejects high-frequency churn when directionality is weak. | This is a heuristic, not reliable wallet/bot identification. |
| Stablecoin pools (M4) | Pools whose **base token** is a stablecoin are excluded; volatile-token pools quoted in USDT/USDC remain eligible. | Matches the requested trading scope. Live provider `kind` semantics are not independently verified. |
| Auto-follow lifecycle (M7) | Inactive auto-followed wallets can be marked `INACTIVE`; manual and user-ignored choices are preserved. | Code and unit tests reviewed. |
| Alert noise / retention (M8, L5, L6) | Per-wallet cooldown, pending-alert suppression, terminal-row retention and bounded Telegram 429 retry handling are present. | Pending retries are preserved during pruning. |
| Settings validation (M9) | Thresholds and related settings are validated, including inconsistent threshold combinations. | Covered by route tests. |
| USD/token presentation (M10) | UI distinguishes net token quantity from net cash flow and describes activity as polled/scanned-pool data rather than a truly live feed. | UI wording does not expand the underlying data coverage. |
| API errors (L9) | Client responses are generic while server logs retain diagnostic detail. | Route tests cover settings error redaction. |
| Timestamps (L11) | Naive database datetimes are serialized explicitly as UTC ISO-8601 strings ending in `Z`; aware values are normalized to UTC. | Serialization tests pass; database values are not shifted. |
| Environment/docs (L10) | `.env.example` and README defaults/explanations were aligned with the current configuration. | Dashboard-persisted settings can override environment defaults. |

## 3. Atomic Follow/Unfollow — important PR #21 change

The prior atomic-toggle implementation in `master` used `INSERT IGNORE` before locking the existing row. The mocked tests passed, but the real MariaDB concurrency test reproduced a deadlock when two requests toggled the same existing wallet.

PR #21 changes the operation to:

1. Acquire a MariaDB advisory lock derived from the chain and wallet.
2. Start a transaction and read the row with `SELECT ... FOR UPDATE`.
3. Insert a missing wallet or toggle the existing status.
4. Commit, then release the advisory lock.

The lock also serializes the first-time toggle when the row does not exist yet. Integration coverage now checks concurrent toggles of an existing wallet, concurrent first-time toggles, and rollback after a forced update failure. All three pass against MariaDB 10.11 in CI.

**Rollout note:** this change is on PR #21 and is **not yet part of `master` unless the PR is merged**. It has not been deployed to CMS. Do not treat the pre-PR #21 `master` implementation as having passed real concurrency testing.

## 4. Remaining risks / work

### Signal quality and coverage

- **Large flow is not proof of skill (H4).** The system ranks observed flow; it does not yet prove a wallet is profitable or predictive. There is no validated wallet PnL/track-record score or forward-return backtest for accumulation/distribution alerts.
- **Absolute thresholds may miss early opportunities (M5).** Current USD floors are not yet consistently calibrated to pool liquidity or volume.
- **Pool polling is not a wallet-wide live feed (M6).** A wallet is visible only when its trades occur in pools the scanner selected. Polling interval and provider limits constrain latency and coverage.
- **Bot/market-maker filtering remains heuristic (M3).** Churn filtering helps but does not identify every bot, router, exchange wallet or losing whale.
- **Provider semantics remain partly unverified.** Stablecoin-base filtering is implemented, but live transaction-level validation of provider buy/sell semantics has not been performed.

### Test coverage still missing

PR #21 adds a real MariaDB test for the atomic toggle only. Other database-backed paths—such as alert-queue cursor behavior, stale-swap suppression, retention, worker rate-limit handling, and settings persistence—still rely partly or wholly on mocked tests. Expand integration coverage selectively where SQL transaction or locking behavior is important.

### Hygiene to revisit separately

- Re-review the older UI hygiene findings (inline JavaScript event construction, polling cost and duplicated event-query logic) against the current frontend before claiming they are fixed.
- Remove obsolete local/remote feature branches only after checking that they are merged and no longer needed.

## 5. Production-validation boundary

Not performed as part of this review:

- No CMS database was used by CI or modified.
- No CMS deployment or Wallet Tracker process restart was performed.
- No live GeckoTerminal API validation or end-to-end Telegram delivery test was run.
- CI passing is not a claim that the strategy has predictive trading edge.

## 6. Recommended next steps

1. Review and merge PR #21 only after inspecting its diff and green checks; keep the production deployment separate.
2. If approved, deploy using the existing CMS procedure and verify the running Wallet Tracker process and logs before/after.
3. Add targeted database integration tests for alert queue/cursor and worker failure paths.
4. Measure signal quality with wallet performance and forward-return outcomes before describing the ranking as profitable or predictive.
