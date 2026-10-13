# Wallet Tracker – Code Review & Findings

**Repo:** `kurnFR/signal-bot` · **Reviewed:** `master` @ `6387ebf` (2026-10-13)
**Scope:** everything under `wallet_tracker/`, `web/routes/wallet_routes.py`, the Wallet Tracking page (`index.html` / `app.js`), config, migration, tests and CI. That includes PR #10 (my original) and the later PRs #11–#14 (smart-money v2, follow-by-chain, quality gates, Telegram alerts).
**Status:** review only. No code in the repo was changed.

---

## 1. Summary

The feature has grown well beyond the first version: configurable scanner, quality-gated auto-follow, durable Telegram alert queue, adaptive rate-limit handling. The engineering foundations are good (idempotent ingest, atomic updates, retry state machine, input validation, 33 passing tests, full suite in CI).

The weaknesses are in the **signal quality and alert correctness**, which is exactly what matters for "early detection". Five problems were reproduced with real runs (not just read from code):

| # | Problem | Impact |
|---|---|---|
| H1 | Token Flow can say **ACCUMULATION while net flow is −$2.68M**, and one wallet is enough to trigger it | The headline early-detection output can point the wrong way |
| H2 | Telegram sends **22-hour-old trades as if they were live** | False "live" alerts after startup, and whenever a pool newly enters the scan |
| H3 | **Telegram bot token is written to the logs** when a send fails | Credential exposure in log files |
| M1 | One rate-limited network **starves the others forever** | Solana/Base/BSC may never be scanned |
| M3 | A wallet with **199 trades in 24h scores 85/100** and is auto-followed | Bots and market makers pollute the "smart money" list |

The biggest gap relative to your goal is conceptual: the system measures **how much money moved**, not **whether the wallet is actually smart** (no profit/track-record scoring). See H4 and §5.

---

## 2. How this was verified

| Method | Result |
|---|---|
| Read all changed files in PRs #11–#14 | done |
| `pytest tests/test_wallet_tracker.py tests/test_wallet_telegram.py` | 33 passed |
| Logic probes against `analyzer.py` (scripts with crafted swaps) | reproduced H1, M2, M3 |
| Real MariaDB 10.11 run of `store.queue_new_wallet_alerts` / `get_pending_wallet_alerts` | reproduced H2 |
| Mock-provider run of `worker.run_cycle` with a 429 on the first network | reproduced M1 |
| Fake-token send failure with log capture | reproduced H3 |
| CI config check | `test.yml` runs the whole suite, so Telegram tests are covered |

**Not verified:** behaviour against the live GeckoTerminal API (my environment cannot reach it), anything in a browser (layout, Safari date parsing), and the actual `kind` semantics for pools where a stablecoin is the base token (see M4).

---

## 3. What is done well

- **Idempotent ingest** (`INSERT IGNORE` on a unique key) – re-reading the same trades is safe.
- **Atomic position refresh** per period (single transaction), no half-written tables visible to the UI.
- **Durable alert queue**: cursor table, `PENDING/SENT/FAILED/CANCELLED`, bounded exponential retry, cancels alerts for wallets that were un-followed.
- **Rate-limit handling**: adaptive delay, `Retry-After` honoured, stops the cycle after a confirmed 429, slow recovery of pacing.
- **Telegram messages are HTML-escaped**, links only emitted for `https://` URLs, sends are throttled.
- **Validation**: Pydantic ranges on settings, chain/address validation, token-selector validation, mutations restricted to trader/admin.
- **Transparent scoring**: the quality score is documented as a ranking, not a probability. Zero-threshold division is guarded.
- Wallet columns use a binary collation, so Solana addresses are never case-folded together.

---

## 4. Findings

Severity: **High** = wrong results or credential exposure; **Medium** = significantly weakens the goal or reliability; **Low** = hygiene / defence in depth.

### High

#### H1. Token Flow signal contradicts the money flow – `analyzer.token_flow`
The signal is chosen only by wallet count (`a > d`), ignoring USD. Reproduced:

```
2 wallets buy $160k each, 1 wallet sells $3.0M  →
signal=ACCUMULATION  accumulating=2 distributing=1  buy=$320k sell=$3.0M  net=-$2,680,000
```
A single accumulating wallet (and no one else) is also labelled `ACCUMULATION`.
My original version required the net-USD sign to agree with the majority; that check was dropped in the later refactor.

**Fix:** require both (a) majority of wallets and (b) net USD in the same direction, plus a minimum wallet count (e.g. ≥ 2–3); otherwise `MIXED`. Consider weighting by USD instead of counting wallets equally.

#### H2. Stale trades are sent as live Telegram alerts – `store.queue_new_wallet_alerts`
The alert cursor works on `wallet_swaps.id`, not trade time. The provider returns up to 24h of history per pool, and those rows get brand-new IDs the first time a pool is scanned (every restart, and every time trending pools change). Reproduced on a real DB:

```
would send: buy  80000 | trade time: 2026-10-12 11:57 | age: 22.0h
would send: sell 60000 | trade time: 2026-10-12 18:57 | age: 15.0h
would send: buy  90000 | trade time: 2026-10-13 09:54 | age: 0.1h   <- the only genuinely fresh one
```
**Fix:** add `AND s.block_time >= UTC_TIMESTAMP() - INTERVAL %s MINUTE` to the queue query (configurable, e.g. 15–30 min given a 5-minute poll), and mark older swaps as skipped so they are not retried. Also show "trade age" in the message.

#### H3. Telegram bot token leaks into logs – `telegram.py` `send()`
`logger.exception(...)` prints the exception text, and `requests` includes the full URL, which contains the token:

```
requests.exceptions.HTTPError: 401 Client Error: None for url: https://api.telegram.org/bot123456789:AAF...xyz/sendMessage
```
Connection errors do the same (`Max retries exceeded with url: /bot<TOKEN>/sendMessage`).
**Fix:** catch the exception and log only `type(exc).__name__` and the HTTP status; or redact with a regex such as `re.sub(r"/bot[^/]+/", "/bot<redacted>/", msg)`. **If any current logs were pasted or shared, rotate the Telegram token.**

#### H4. "Smart money" is measured by size, not skill
Candidates and follow decisions use net USD, volume, trade count and directionality. Nothing measures whether the wallet **makes money**. The UI labels followed wallets "✓ Smart Money", which implies skill. A large wallet can be a loser, a fund treasury, a market maker or an exchange. Following such wallets produces signals with unknown (possibly zero) predictive value.
**Fix:** see §5 (unrealised/realised PnL per wallet, "early-ness", and a forward-return backtest of the signal). Until then, consider renaming the label to "Large Flow" so it does not overpromise.

### Medium

#### M1. One rate-limited network starves the others – `worker.run_cycle`
Networks are always scanned in the same order and the cycle stops at the first 429. Reproduced with `eth` returning 429: over 3 cycles only `['eth','eth','eth']` was queried; Solana/Base/BSC were never scanned.
**Fix:** rotate the starting network each cycle, keep a per-network cooldown instead of aborting everything, and show per-network "last scanned" in the status panel so gaps are visible.

#### M2. Follow decision nets USD across different tokens – `aggregate_wallet_flows`
Rotation is treated as a dump. Reproduced: a wallet sells $700k of token A and buys $170k of token B → net −$530k, score 60.9, **auto-followed as a big mover**.
**Fix:** decide per `(chain, base token)`: net flow in that token, with the thresholds applied per token.

#### M3. Bots and market makers are not filtered
Reproduced: 140 buys + 59 sells of $30k each (199 trades in 24h) → score **85.2**, auto-followed. `tx_from_address` is also frequently a router, relayer or MEV bot. Nothing checks for contracts, known routers/CEX wallets, or bot-like cadence.
**Fix (cheapest first):** cap trades/hour or trades/24h; measure round-trip ratio and median trade interval; penalise the score; add a small deny-list of router/CEX addresses; later, check whether the address is a contract (EVM `eth_getCode`).

#### M4. No stablecoin / wrapped-native handling (unverified, potentially inverting)
The code assumes `kind` is relative to the pool's **base** token (this assumption came from my original provider and was never confirmed against a live trade). If a pool's base is a stablecoin or WETH/WSOL, then "buy" means buying the stable/native token, which can invert the meaning, and the top-volume pool list is dominated by such majors.
**Fix:** verify the semantics against one known transaction on a block explorer, then skip pools whose base token is a stablecoin and treat wrapped-native/majors in a separate lens. The pool dict already carries `base_token_address`.

#### M5. Absolute USD thresholds work against "early detection"
Defaults: trade ≥ $10k, net ≥ $150k, auto-follow ≥ $500k, and pools below `max(10×min_trade, $100k)` 24h volume are skipped. Early-stage tokens rarely have that much flow, so the system mostly sees large caps, where "smart money" signals are weakest.
**Fix:** make "huge" relative: net flow as a % of pool liquidity (`reserve_in_usd`) or of 24h pool volume, with absolute floors only as noise guards.

#### M6. "Follow Live" is not live, and only sees scanned pools
Data is polled every 5 minutes, only from the pools the scanner picked, and alerts are dispatched at the end of each cycle. A followed wallet's trades in any other pool are invisible. For a small, bounded set of followed wallets, a per-wallet source (e.g. Helius webhooks for Solana, Alchemy/Moralis address activity for EVM) would give true low-latency feeds within free tiers.

#### M7. Auto-followed wallets are never demoted
Once flagged, a wallet stays `ACTIVE` until a human clicks Uncheck. There is no TTL, no cap and no ranking, so the list and the alert volume only grow.
**Fix:** expire auto-followed wallets that no longer meet the criteria for N days; cap the active auto-follow count (keep the top-K by score).

#### M8. Alert noise controls are missing
Every new swap ≥ `min_trade_usd` from any followed wallet becomes an alert. Bots would flood the chat (Telegram group limit is roughly 20 messages/min). **Fix:** per-wallet cooldown, a separate alert threshold (higher than the scan threshold), and a digest mode.

#### M9. Settings validation is too permissive – `WalletSettingsRequest`
`min_net_usd=0` or `auto_track_net_usd=0` is accepted (follow everyone); `pools_per_network=100` with `api_delay_seconds=0.5` implies ~120 requests/min against a free limit of ~30; no check that `auto_track_net_usd ≥ min_net_usd`.
**Fix:** lower bounds, a cross-field validator, and a computed "estimated requests/minute" warning in the UI.

#### M10. Position `side` can disagree with the displayed net USD
By design (and tested) `side` follows net **tokens**. A wallet that buys cheap and sells high can show a green BUY badge with a red negative net USD (reproduced: `side=BUY, net_usd=-200000, net_tokens=+500000`). It is defensible but confusing without explanation.
**Fix:** show a "Net tokens" column and rename the USD column "Net cash flow", or add a tooltip.

### Low

| # | Finding | Fix |
|---|---|---|
| L1 | `followWallet('${escapeHtml(wallet)}', …)` builds JS inside an inline `onclick`. HTML-escaping does not protect a JS string context. Not exploitable today (values are validated), but fragile. The earlier code used `data-*` attributes safely. | Use `data-*` + `this.dataset` again |
| L2 | The page refreshes all 5 endpoints every 15s while the scanner updates every 5 min | Poll only `events` (+ status) at 15s; the heavy tables at 60s |
| L3 | Event query building is duplicated in `fetchWalletData` and `fetchWalletEvents`; the "new row" highlight compares IDs across different filters | Extract one helper; reset `_wtLastEventId` when filters change |
| L4 | `store.py` starts with `import config` before the module docstring; missing blank line before `hot_pools`; `config_value` lazily imports a module already imported | Tidy |
| L5 | `wallet_telegram_alerts` has no FK/cleanup: rows (and orphans after `prune_swaps`) grow forever | Prune `SENT/FAILED/CANCELLED` older than N days |
| L6 | `Retry(status_forcelist=[429,5xx])` does not apply to POST (urllib3 default `allowed_methods`), so it is ineffective; retries effectively happen once per poll cycle | Handle `retry_after` from Telegram 429 explicitly |
| L7 | "Telegram alerts paused" warning is logged every cycle | Log once |
| L8 | `POST /tracked/toggle` is check-then-act (race on double-click) | Single atomic upsert with explicit target state |
| L9 | API errors return `f"...{e}"`, leaking internal error text to clients (repo-wide pattern) | Generic message to client, details to log |
| L10 | Settings now live in the DB and override env; `.env.example` still has old values (8 pools / $2k / $100k / $250k / 120s vs code 4 / $10k / $150k / $500k / 300s) and lacks `API_DELAY`; README does not mention settings panel, quality gate, follow-live or Telegram. (`.env.example` and README were my PR #10; later PRs did not update them.) | Update both |
| L11 | Wallet timestamps are emitted as `YYYY-MM-DD HH:MM:SS`; JS appends `Z`. Works in Chrome, may show "Invalid Date" in Safari (unverified) | Emit ISO `T…Z` from `_clean` |
| L12 | 26 branches, including `do-not-use`, `ignore2`, `tmp-ignore`, `x`, `master-check`, and merged feature branches | Delete merged/obsolete branches |
| L13 | No tests for `run_cycle` (429 path), the DB queue SQL, `PUT /settings` role enforcement, or the H1 contradiction case. All DB code is mocked, so SQL regressions would not be caught | Add an integration test against a MariaDB service container in CI |

---

## 5. How to get better results (improvement plan)

### Quick wins (small, low risk)
1. **H1** token-flow rule (USD sign + min wallet count).
2. **H2** alert freshness window.
3. **H3** log redaction.
4. **M1** network rotation + per-network cooldown.
5. **M9** settings cross-validation.
6. **L10** refresh `.env.example` and README.

### Signal quality (the main lever)
7. **Per-token follow decisions** (M2) and **bot/router filtering** (M3).
8. **Stablecoin / native-token pool handling** (M4), after confirming `kind` semantics on a known tx.
9. **Liquidity-relative thresholds** (M5).
10. **Wallet performance score (H4).** The data already exists in `wallet_swaps`:
    - Unrealised PnL per wallet+token: `(latest pool price − avg buy price) / avg buy price`, using the newest swap price as the mark.
    - Realised PnL via FIFO matching of buys and sells.
    - **Early-ness**: how far price moved in the hours after the wallet's buys (did it buy before the move?).
    - Follow only wallets whose recent calls were profitable, not just large.
11. **Measure whether the signal works.** Record price at detection time and evaluate forward returns at +1h / +6h / +24h for every ACCUMULATION / DISTRIBUTION signal. A small `signal_outcomes` table plus a dashboard card tells you if "smart money accumulation" has any edge, and lets you tune thresholds on evidence instead of guesses. This is the single most valuable addition.

### Coverage and "live" quality
12. **Wallet-level feed for followed wallets** (M6) so they are visible in every pool, with seconds-level latency.
13. **Alert policy** (M7, M8): TTL for auto-follow, cap, per-wallet cooldown, digest mode, separate alert threshold.

### Engineering hygiene
14. Integration test with a MariaDB service in CI (L13).
15. Branch cleanup (L12), small UI/code tidy-ups (L1–L4).

---

## 6. Honest notes on my own earlier work

- The `kind`-relative-to-base-token comment in `providers.py` came from my memory of the API docs and was **not verified live**; M4 depends on it.
- My original `.env.example` and README (PR #10) are now out of date (L10).
- My original `token_flow` required net-USD agreement; the later refactor dropped it (H1). I should have added a test that pins that behaviour.
- My own tests mocked the provider, so they could not catch the stale-trade backfill problem (H2) or starvation (M1).

---

## 7. Suggested order of work

1. H1 + H2 + H3 (one small PR, each with a regression test).
2. M1 + M9 (reliability).
3. M2 + M3 + M4 (signal quality), then H4 + signal-outcome tracking.
4. M6 (true live feed) once the signal is shown to be useful.
