# Wallet Tracker – Review, Round 2

**Repository:** `kurnFR/signal-bot`
**Reviewed:** 2026-10-15 · `master` @ `fd68f45` (merge of PR #21), plus open **PR #22** (`fix/wallet-tracker-provider-reliability`, reviewed but *not* merged)
**Scope:** `wallet_tracker/*`, `web/routes/wallet_routes.py`, Wallet Tracking UI, config/migration/docs, tests and CI.
**Status:** review only; no application code was changed. This document replaces the round-1 review and the 2026-10-14 follow-up, and keeps their still-valid content.

---

## 1. Verdict

The team fixed most of round 1 quickly and well. **All three High findings from round 1 are fixed and independently verified** (wrong token-flow signal, stale Telegram alerts, bot token in logs), as is the rate-limit starvation. The remaining weaknesses are no longer "wrong output"; they are **signal quality** (bots still get through, "large" is not "smart") and a few **new problems introduced by the fixes themselves**.

| Round-1 finding | Status today |
|---|---|
| H1 token-flow contradicts money flow | ✅ Fixed, verified |
| H2 stale trades sent as live alerts | ✅ Fixed, verified on real MariaDB |
| H3 Telegram token in logs | ✅ Fixed, verified (both HTTP-error and connection-error paths) |
| M1 one 429 starves other networks | ✅ Fixed, verified |
| M3 bot / high-frequency wallets followed | ⚠️ Only partly fixed (see §3) |
| H4 "smart" = size only, no skill measure | ❌ Open (UI wording toned down) |

**New problems found this round** (details in §4):

| # | Problem | Severity |
|---|---|---|
| N1 | Alert cooldown **drops swaps**: a wallet that bought **$1.26M in 4 swaps** produced one alert saying **$12,000** | High |
| N2 | Stablecoin filter probably has no symbols to work with (provider never asks for them) | Medium (verify) |
| N3 | Follow decision is per **pool**, not per **token**: $900K of one token split over 2 pools is missed | Medium |
| N4 | Startup now runs `ALTER TABLE`; a DB user without ALTER makes the worker crash on start | Medium |
| N5 | **PR #22:** single-instance lock silently lapses when its idle connection times out | Medium |
| N6 | **PR #22:** Birdeye fallback can double-count or silently return nothing | Medium |

---

## 2. How this was verified

| Method | Result |
|---|---|
| Read every wallet-tracker diff between `6387ebf` (round 1) and `fd68f45` | done |
| `pytest tests` on `master` (my sandbox, excluding `test_retailbot_accounting`, which needs a dependency I don't have) | **198 passed, 3 skipped** |
| `tests/test_wallet_tracker_mariadb.py` against a local MariaDB 10.11 with `WALLET_TRACKER_MARIADB_TESTS=1` | **3 passed** (matches the owner's CI result) |
| Wallet tests on PR #22 branch | **65 passed**; its CI checks (5) all green |
| Re-ran my round-1 reproduction scripts (analyzer probes) against current code | H1, M2 fixed; M3 still reproduces |
| Real-DB probes: stale alerts, cooldown, expiry, ALTER privilege | results below |
| Mock-provider run of `run_cycle` with `eth` returning 429, 4 cycles | all four networks still scanned, rotating |
| Telegram failure with a fake token, log capture | token absent from logs and from persisted error text |
| Idle-connection experiment for the PR #22 lock | lock released after idle timeout (see N5) |

**Not verified (cannot be from my environment):** live GeckoTerminal and Birdeye behaviour, real Telegram delivery, the page in a browser, anything on the CMS/production server. Passing CI means the code does what the tests say; it is not evidence of trading edge.

---

## 3. Status of every earlier finding

| ID | Finding | Status | Evidence / remaining gap |
|---|---|---|---|
| H1 | Token-flow signal vs net USD | ✅ Fixed | Conflicting case ($320K buys vs $3M sells) now `MIXED`; single wallet `MIXED`; two agreeing wallets `ACCUMULATION`. Rule: ≥2 wallets, majority, and net USD same sign. |
| H2 | Stale trades alerted as live | ✅ Fixed | Real DB: 22h-old and 15h-old swaps no longer queued; only the 3-minute-old one is. Window is hard-coded at 30 min in `worker.py` (not a setting). |
| H3 | Bot token in logs | ✅ Fixed | Logs now contain only exception type and HTTP status; persisted `last_error` is generic. **Still rotate the token if old logs were shared.** |
| H4 | Size is not skill | ❌ Open | No PnL / track-record / forward-return scoring. The table header was renamed from "Smart Money" to "Checklist", which stops over-promising. |
| M1 | 429 starves other networks | ✅ Fixed | Rotation + per-network cooldown. Caveat: the provider limit is per IP, not per network, so a cooldown on one network does not protect the others from the same limit; PR #22's Birdeye fallback helps here. |
| M2 | USD netted across tokens | ✅ Mostly fixed | Rotation A→B no longer followed. But see **N3** (per pool, not per token). |
| M3 | Bots / high-frequency wallets | ⚠️ Partial | Gate rejects only **>20 trades/hour *and* net/total <50%**. Re-run: 199 trades/24h (8.3/h) scores **85.2**; 450 trades (18.8/h) **83.3**; 500 trades (20.8/h, 60% directional) **90.0**. All still auto-followed. |
| M4 | Stablecoin / orientation | ⚠️ Partial | Stablecoin-**base** pools are excluded (quote-side stables like ARK/USDT stay eligible, as intended). See **N2**. `kind` semantics still not checked against a live transaction. Wrapped-native majors (WETH/WBNB/SOL) remain eligible. |
| M5 | Absolute USD thresholds | ❌ Open | Floors ($10K trade / $150K net / $500K follow; pools under ~$100K volume skipped) still favour large caps over early tokens. |
| M6 | "Live" feed limited to scanned pools | ❌ Open (honestly labelled) | UI now says "polled, not a real-time feed". Coverage unchanged. |
| M7 | Auto-follows never expire | ⚠️ Mostly fixed | Verified: a 10-day-old auto wallet became `INACTIVE`; a 1-day-old auto wallet and a 30-day-old **manual** wallet were untouched. **No cap** on active auto-follows yet. |
| M8 | Alert noise | ⚠️ Fixed with a side effect | Cooldown, retention and bounded 429 retries exist, but the cooldown loses information (**N1**). Transport retries for POST were deliberately disabled to avoid duplicate sends: good decision. |
| M9 | Settings validation | ✅ Mostly fixed | Zero values rejected; `auto_track ≥ min_net` enforced. Gap: no request-budget check (e.g. 100 pools × 6 networks at 0.5 s delay far exceeds a free API limit). |
| M10 | Side vs net USD confusing | ✅ Fixed | "Net Tokens" and "Net Cash Flow (USD)" columns, "Token Bias" label. |
| L1 | Inline `onclick` with HTML-escaped args | ❌ Open | Still at `app.js` lines 603 and 669. Not exploitable today (values are validated server-side), but fragile. Use `data-*` attributes. |
| L2 | Page re-fetches all 5 endpoints every 15 s | ❌ Open | Unchanged. Poll only events/status at 15 s; heavy tables at 60 s. |
| L3 | Duplicate event-query code / highlight logic | ❌ Not changed | |
| L4 | Style nits | ❌ Open | `store.py` still starts with `import config` before its docstring; no blank line before `hot_pools`. |
| L5 | Alert table never pruned | ✅ Fixed | `prune_wallet_alerts` (terminal rows only; pending retries preserved), retention configurable. Verified by reading the code and tests; I did not exercise the delete against a database. |
| L6 | Ineffective Telegram retry | ✅ Fixed | Explicit 429 (`retry_after`) and 5xx handling, no transport retry on POST. |
| L8 | Toggle race | ✅ Fixed | Advisory lock + transaction. Per the owner's 10-14 notes, their real-MariaDB test first *reproduced a deadlock* in the earlier version (I did not re-create that). The fix is covered by 3 integration tests, which I ran and which passed. Note: it is now merged to `master` (the 10-14 doc said it was not). |
| L9 | Error text leaked to clients | ✅ Fixed | Generic messages; details in server logs. |
| L10 | `.env.example` / README stale | ✅ Fixed | `.env.example` values now match `config.py` defaults. (Dashboard-saved settings still override env.) |
| L11 | Timestamp format | ✅ Fixed | `...Z` ISO strings; JS only appends `Z` when missing. |
| L12 | Branch clutter | ❌ Worse | **34 branches** (was 26), 13 wallet-related, including `do-not-use`, `ignore2`, `tmp-ignore`, `x`, `master-check`. |
| L13 | DB code untested in CI | ⚠️ Partial | A MariaDB CI job exists, but covers the toggle only. |

---

## 4. New findings

### N1 (High): the alert cooldown drops swaps and can badly under-report
`queue_new_wallet_alerts` keeps only the **lowest-id** qualifying swap per wallet per batch, then advances the cursor past everything else. Reproduced on a real DB:

```
wallet buys $1,262,000 in 4 swaps (12K, 400K, 350K, 500K)
alerts queued: 1  →  usd_value = 12,000
next cycle re-queues the other 3 swaps? 0  (cursor already advanced; they are never reported)
```
For an "early detection" alert, the largest moves are exactly what gets lost, and the message can understate a $1.26M buy by 100×.
**Fix:** send a **per-wallet digest**: number of swaps, total USD, net side, largest trade, average price, first/last time. Apply the alert threshold to the *aggregate*, not to one swap. Keep the cooldown, but accumulate during it instead of discarding.

### N2 (Medium, verify): stablecoin symbol check likely has no data
`hot_pools` calls the pool endpoints without an `include=base_token,quote_token` parameter, so the `included` token objects that carry `symbol` are probably absent and `base_token_symbol` stays empty. Only the short canonical-address list then protects you. I confirmed the function returns `False` for a pool named `USDC / WETH` when only the name is present, and `True` when a symbol or a listed address is supplied.
**Check:** log one pool dict at debug level and see whether `base_token_symbol` is populated.
**Fix:** request `include=base_token,quote_token`, and fall back to the first segment of the pool name (`"USDC / WETH 0.05%"` → `USDC`).

### N3 (Medium): per pool, not per token
The follow gate groups by `(chain, wallet, pool_address)`. A wallet buying the same token in `TOKEN/WETH` and `TOKEN/USDC` is split in two. Reproduced: $900K bought across two pools ($450K each) is **not** followed because each half is below the $500K floor.
**Fix:** group by `(chain, base_token_address)` (the pool dict already carries it) and keep per-pool detail for display.

### N4 (Medium): startup now needs `ALTER` privilege
`ensure_tables()` runs `ALTER TABLE tracked_wallets MODIFY status ENUM(...)` on every start. Before, only `CREATE` was needed. With a DB user lacking ALTER, MariaDB returns error 1142 (reproduced). The web app catches this and warns, but `worker.run()` calls `ensure_tables()` unguarded, so the worker would crash on every start (and `run_all.py` would keep restarting it).
**Fix:** read `INFORMATION_SCHEMA.COLUMNS.COLUMN_TYPE` first and `ALTER` only when needed, and wrap it so a failure logs a clear message instead of killing the process. Check what privileges your production DB user has before deploying.

### N5 (Medium, PR #22): single-instance lock lapses
The worker holds `GET_LOCK` on a connection that then sits idle for the life of the process. MariaDB closes idle connections after `wait_timeout` (default 28800 s = 8 h). Reproduced with a shortened timeout: after the idle connection was dropped, a second worker acquired the lock while the first was still running. In production this means the duplicate-worker protection silently expires after about 8 hours.
**Fix:** `SELECT 1` (or `ping`) on the lock connection every cycle, check `IS_USED_LOCK(...) = CONNECTION_ID()`, and exit or re-acquire if the lock is lost.

### N6 (Medium, PR #22): Birdeye fallback risks
- **Possible double counting.** Dedupe relies on the unique key `(chain, tx_hash, pool, side, wallet)`. Birdeye's `owner` can differ from GeckoTerminal's `tx_from_address` (router/relayer cases), so the same swap may be stored twice under two wallets, inflating flows.
- **Silent empty success.** `parse_birdeye_trade` tolerates many field spellings and returns `None` for rows it cannot map. If the real payload does not match, every row is dropped yet the scan counts as a success (`pools_scanned += 1`) with no warning.
- **Thin data.** One request returns the latest 50 swaps with no pagination and no server-side size filter, so on busy pools older large trades are missed.
**Fix:** warn when a response has N raw items but 0 parsed rows; add a counter for "same tx/pool/side but different wallet"; compare one real overlapping pool from both sources before trusting it. Keep it opt-in (it already is, via `BIRDEYE_API_KEY`).

### Smaller items
- Per-network cooldown skips are not shown in the dashboard status, so reduced coverage is invisible.
- `max_age_minutes=30` and the 300 s network cooldown are hard-coded.
- `if _is_rate_limited(exc): ...; continue` followed by another `continue` is redundant (harmless).

---

## 5. PR #22 summary (open; not on `master`)

**Good:** pool-discovery cache so a temporary outage does not erase the scan list; `Retry-After` parsing (numeric and HTTP-date); single-instance guard (needs N5 fixed); fallback is opt-in; defensive Birdeye parser rejects ambiguous rows instead of guessing; 65 wallet tests and all 5 CI checks pass.
**Before merging:** address N5 and the "0 parsed rows" warning from N6. Do not enable the fallback in production until one overlapping pool has been compared across both sources.

---

## 6. Still-open strategic work

1. **Measure whether the signal works (H4).** Now that H1 is fixed, the signal is trustworthy enough to evaluate. Record price at each ACCUMULATION/DISTRIBUTION signal and evaluate forward returns at +1h/+6h/+24h in a small `signal_outcomes` table. Add per-wallet unrealised PnL from data you already store (`(latest pool price − avg buy price) / avg buy price`). This is the only way to tell "large" from "smart".
2. **Stronger bot filtering (M3).** Add median inter-trade interval, buy/sell alternation, same-block multi-swaps, and a contract/router check. Consider an absolute cap such as >100 trades/24h with <60% directionality.
3. **Relative thresholds (M5).** Express "huge" as a share of pool liquidity or 24h volume, with absolute floors only as noise guards.
4. **Wallet-level feed (M6).** For the small followed set, use per-wallet sources (e.g. Helius for Solana, Alchemy/Moralis address activity for EVM) so a wallet is visible in every pool, with real latency.
5. **Cap on active auto-follows (M7)**, keeping the top-K by quality score.

---

## 7. Recommended order of work

1. **N1** alert digest (highest user-visible impact).
2. **N4** make `ensure_tables` safe without ALTER; **N2** request token metadata / parse pool name; **N3** group by token.
3. **PR #22:** fix N5, add the N6 safeguards, then merge.
4. **M3** stronger bot filter, then **H4** outcome tracking.
5. Hygiene: L1, L2, L4, branch cleanup, request-budget validation (M9).

---

## 8. Validation boundary

Not performed in this review: no CMS/production database was touched; no deployment or process restart; no live GeckoTerminal/Birdeye validation; no real Telegram delivery test; no browser testing. Passing CI is not a claim of predictive trading edge.
