# signal-bot — Professional Trader Review & Improvement Plan

**Repo:** `kurnFR/signal-bot` (cloned and inspected directly — not a Signal-messenger bot despite the name; it's a **crypto quant signal/backtesting/paper-trading platform** for Binance spot & futures, with a FastAPI web dashboard, MySQL storage, 24 backtestable strategies, an AI "insight engine," and Telegram alerts).

This review is scoped to what matters for a **professional trader**: execution realism, risk-management correctness, capital accounting, operational reliability, and security — not just code style. Findings below were confirmed by reading the actual source (`paper/engine.py`, `backtest/simulate.py`, `web/auth.py`, `db/schema.sql`, `config.py`), not guessed from the README.

> **Update (follow-up pass, re-reviewed against `master` after further development):** the two most consequential Critical findings — position sizing and funding-cost accounting — were fixed properly and verified in the current code, along with the duplicate-position race condition. Status per item is now tagged below as ✅ **Fixed**, 🟡 **Partially fixed**, or ⬜ **Open**. See §7 for the full re-review notes.

---

## 1. Executive Summary

The project is further along than the name suggests: it has a real bar-by-bar backtest engine with no-lookahead entry-at-next-open logic, walk-forward validation, a paper-trading shadow engine, and role-based web auth. That's a solid foundation. But for professional use, there are **four categories of gaps**:

| Category | Severity | Summary |
|---|---|---|
| Capital & risk accounting | 🔴 Critical | Configured position sizing (`allocated_capital`, `risk_per_trade_pct`) is stored but never used to size trades or compute dollar P&L |
| Perpetual futures cost model | 🔴 Critical | Funding rate is used as a *signal* but never charged as a *cost* against futures positions held across funding intervals |
| Execution/ops realism | 🟠 High | No live broker/exchange execution path exists at all (paper-only); polling-based re-simulation is inefficient and has race-condition exposure |
| Security & auditability | 🟠 High | Default `admin/admin123` auto-seeded, stateless tokens can't be revoked, no rate-limiting, no trade audit trail/immutability |

None of these are "the code doesn't run" bugs — they're the kind of gaps that make backtest/paper numbers **look better than what a real account would experience**, which is the most dangerous class of bug for a trading system.

---

## 2. Confirmed Bugs

### 2.1 Risk sizing is configured but silently ignored (Critical) — ✅ Fixed
`paper_configs` stores `allocated_capital` and `risk_per_trade_pct` per strategy, and the README advertises *"1-Click Deployment to Paper Trading with custom capital allocation and risk per trade."* However, in `paper/engine.py`, the position-opening logic (`INSERT INTO paper_positions ...`) never reads `cfg["allocated_capital"]` or `cfg["risk_per_trade_pct"]` — no position size / quantity field even exists in the `paper_positions` schema. Every position is tracked purely in **percent return and R-multiple**, regardless of the capital the user configured.

**Impact:** two strategies with identical R-multiples but wildly different configured capital/risk will report identical performance — the dashboard cannot show real dollar equity curves per strategy, and a trader cannot tell whether a "winning" strategy is winning at a risk size they actually specified.

**Fix:** compute `position_size = (allocated_capital * risk_per_trade_pct / 100) / initial_risk_price`, persist `quantity`/`notional` on `paper_positions`, and derive `realized_pnl_usd` on close from `quantity * price_delta`. Roll a per-strategy equity curve from that instead of only R-multiples.

**Resolved by:** new `backtest/accounting.py` module (`calculate_position_size`, `calculate_trade_accounting`) shared by both the backtester and `paper/engine.py`. `db/migrate_p0_paper_accounting.sql` adds real `quantity`, `notional_value`, `risk_amount`, `entry_fee`/`exit_fee` columns to `paper_positions` and `paper_trades`. Verified this is actually called at position-open and position-close time, not just defined and left unused.

### 2.2 Perpetual futures funding cost is not charged (Critical) — ✅ Fixed
`FUNDING_STRATEGIES` use funding rate as an *entry signal* (`backtest/optimize.py`, `run_backtest.py`, `walk_forward.py` all pass `include_funding=...`), but `backtest/simulate.py`'s P&L calculation only applies `BACKTEST_FEE_PCT` and `BACKTEST_SLIPPAGE_PCT` — there is no funding accrual for the number of 8‑hour funding intervals a futures position is held through. For any multi-day-hold futures strategy, this **overstates** backtested and paper-traded returns, sometimes materially (funding drag compounds over holding period and can flip a marginal strategy from profitable to unprofitable).

**Fix:** in `simulate()`, when `market == "futures"`, look up realized funding rate over `[entry_time, exit_time]` from the `funding_rate` table already being collected, and subtract `sum(funding_rate_i * notional)` from net P&L. This data already exists in the DB (`ws_funding.py`, `backfill_funding.py`) — it just isn't being used at settlement time.

**Resolved by:** `calculate_funding_cost(quantity, funding_events)` in `backtest/accounting.py`, wired into `paper/engine.py`'s position-close path, with a new `funding_pnl` column persisted on both `paper_positions` and `paper_trades` for auditability.

### 2.3 Re-evaluation loop re-simulates the entire history every cycle (High — performance/scale) — 🟡 Partially fixed
In `evaluate_paper_positions()` (`paper/engine.py`), for every active config with no open position, the code calls `strat_fn(df, run_params)` — running the **full backtest simulation over the entire fetched history** just to check whether the *most recent* candle produced a new signal. As symbol/timeframe/strategy count grows, this becomes O(n) per poll per config, which won't scale past a handful of configs on tight polling intervals (e.g. 1m/5m timeframes).

**Fix:** add an incremental "evaluate last N bars only" path for live signal checking, reserving the full simulate() for backtesting/optimization only.

**Status:** open positions are now updated against just the latest closed bar (no full re-sim) — the expensive path for existing positions is gone. New-entry signal detection (the `else` branch in `evaluate_paper_positions()`) still calls `STRATEGIES[strategy_name](df, run_params)` over the full fetched history for every active-but-flat config, every poll cycle. Still worth the incremental-entry-check fix for scale.

### 2.4 No locking around position open/close (High — correctness under concurrency) — ✅ Fixed
`get_active_positions()` is read without `SELECT ... FOR UPDATE`, and there's a read-then-insert gap between checking `open_pos_map` and inserting a new `paper_positions` row. If `evaluate_paper_positions()` is ever invoked concurrently (e.g., a scheduler double-fires, or a manual "re-run" button is clicked while a background poller is running), the same signal can open **duplicate positions** for the same `(symbol, market, timeframe, strategy)`.

**Fix:** wrap the check-then-insert in a transaction with `SELECT ... FOR UPDATE` on `paper_configs`/`paper_positions`, or enforce a DB-level unique constraint on `(symbol, market, timeframe, strategy_name, status='OPEN')`.

**Resolved by:** `db/migrate_p0_paper_accounting.sql` adds a MySQL *generated column* `open_position_key` (non-null only when `status='OPEN'`, else `NULL`) with a `UNIQUE INDEX` on it. This is arguably cleaner than the `FOR UPDATE` approach originally suggested — it makes duplicate opens impossible at the database layer regardless of application-level bugs or future concurrent callers, rather than relying on every call site remembering to take a lock.

### 2.5 Silent NaN swallowing on trailing-stop ATR (Low-Medium) — ⬜ Open
In both `paper/engine.py` and `backtest/simulate.py`, `atr` is pulled with `.get("atr", 0)` / accessed directly, and if the value is `NaN` (common at the start of a series before the ATR window fills), `atr > 0` silently evaluates to `False` in Python — trailing-stop activation is skipped with no warning logged. This can cause a paper strategy to quietly run in fixed-SL mode when the user believes trailing is active.

**Fix:** explicitly `if pd.isna(atr): log.warning(...); skip or fallback`, rather than relying on NaN comparison semantics.

### 2.6 Module-level DB side effects on import (Medium — operability) — ⬜ Open
`paper/engine.py` calls `seed_validated_paper_configs()` at **import time** (bottom of file), and `web/auth.py`'s `seed_default_admin()` presumably runs similarly at app startup. Any script that merely imports `paper.engine` (tests, a REPL, a different worker) triggers a live DB connection and write as a side effect. This makes the module hard to unit-test and can cause surprising writes in environments where the DB isn't ready yet (crashes on import instead of a clean startup-sequence error).

**Fix:** move seeding into an explicit `init_app()` / startup hook called once by `run_web.py`, not at import time.

---

## 3. Security Findings

*All four items below remain open as of the re-review. `web/app.py` and `web/auth.py` did receive a security pass (commits `48c1493`, `af25bf8` — "enforce authenticated API boundary and role policy"), which tightened which endpoints require auth and which roles can call them. That's a real improvement to authorization, but it's a different axis from the four items below (authentication hardening / credential hygiene), which are unchanged.*

1. **Default credentials auto-created and logged (`admin` / `admin123`)** — ⬜ Open, unchanged. `web/auth.py::seed_default_admin()` still runs unconditionally when the `users` table is empty and prints the password to stdout/logs. For anyone who forgets to rotate it (very common), this is an open admin account on any exposed dashboard. Recommend: generate a random default password and force a change on first login, or require an env var for the initial password.
2. **No login rate-limiting / lockout** — ⬜ Open, unchanged. Nothing in `web/auth.py` throttles repeated login attempts, so the login endpoint (wherever it lives in `web/routes`) is brute-forceable against the (often default) admin password.
3. **Stateless tokens can't be revoked** — ⬜ Open, unchanged. Sessions are self-contained HMAC tokens with a 24h expiry and no server-side blacklist. "Logout" can't actually invalidate a token before expiry, and there's no mechanism to force-expire all sessions after a suspected compromise short of rotating `SECRET_KEY` (which invalidates *everyone*).
4. **No audit log for trading actions** — ⬜ Open, unchanged. Activating/deactivating a paper config, changing allocated capital, or promoting a strategy from backtest to paper trading isn't recorded anywhere with a user/timestamp. For anything resembling professional use (even paper trading with real strategy decisions riding on it), you want an immutable action log.

---

## 4. Gaps Relative to "Professional Trader" Expectations

These aren't bugs in existing code — they're missing capabilities a professional would expect before trusting this beyond a hobby setup.

*Status vs. re-review: the first item below (execution path) is unchanged by design. The multiple-testing item got a heuristic sample-size confidence label in `ai/insight_engine.py`, which helps but doesn't fully close the gap (see §7). Portfolio exposure, slippage model, and circuit breakers are all still open.*

- **No live execution path.** — ⬜ Open, unchanged. Everything is backtest + paper (shadow) trading; there is no broker/exchange order-placement integration anywhere in the codebase (verified: no signed REST order calls exist). Moving from "signal generator" to "trading system" requires an execution adapter (even a simple Binance REST order-placement module gated behind explicit opt-in and hard position/notional limits).
- **Single-position-per-strategy only; no portfolio-level risk view.** — ⬜ Open, unchanged. `simulate()` deliberately holds one position at a time per strategy run, which is fine for isolated backtests, but the paper engine has no aggregate view of *total* exposure/margin usage across all active configs simultaneously (e.g., correlated BTC/ETH/BNB longs stacking risk). A professional needs a portfolio risk dashboard: gross/net exposure, per-asset concentration, and margin utilization for futures configs.
- **No slippage model beyond a flat constant.** — ⬜ Open, unchanged (`BACKTEST_SLIPPAGE_PCT = 0.0005` is still a single fixed value in `config.py`; `accounting.py`'s new slippage functions correctly *apply* it directionally, but the rate itself is still not volatility/size-aware). `BACKTEST_SLIPPAGE_PCT` is a single fixed percentage regardless of order size, volatility regime, or liquidity — unrealistic for anything beyond small size on BTC/ETH. A volume/ATR-scaled slippage model would materially improve backtest fidelity, especially for smaller-cap symbols in the `SYMBOLS` list.
- **Strategy selection risk (multiple-testing / overfitting) isn't explicitly guarded against.** — 🟡 Partially addressed. With 24 strategies "battling" for a Top-3 podium and only a single walk-forward/holdout split, the winner is subject to selection bias — the best of 24 backtests on a fixed holdout window will look better than its true expected edge. Recommend: report a multiple-testing-adjusted confidence measure (e.g., deflated Sharpe ratio / probability of backtest overfitting à la Bailey & López de Prado) alongside the ranking, not just raw expectancy/PF.
- **No circuit breakers.** — ⬜ Open, unchanged. There's no max-daily-loss, max-drawdown, or max-concurrent-positions kill switch in the paper engine — something a professional risk desk would require even in shadow mode, since it's the exact logic that will later be reused for live capital.
- **No alert delivery guarantees.** — ⬜ Open, unchanged. `notifications/telegram_notifier.py` failures are caught and logged (`logger.warning`) but not retried or queued — a transient Telegram outage silently drops a live entry/exit alert with no fallback channel (email/webhook) and no "missed alert" reconciliation.
- **No backtest/paper reproducibility metadata.** — ⬜ Open, unchanged. Trade records don't store the exact strategy parameter set or code version used to generate them, making it hard to explain a historical paper trade after `BASE_PARAMS` or a strategy file changes later.

---

## 5. Prioritized Improvement Roadmap

**P0 — Trust the numbers**
1. ~~Wire `allocated_capital` / `risk_per_trade_pct` into actual position sizing and dollar P&L (§2.1).~~ ✅ Done.
2. ~~Charge funding costs against futures paper/backtest P&L (§2.2).~~ ✅ Done.
3. Add a deflated/adjusted performance metric to the strategy ranking to counter multiple-testing bias (§4). 🟡 Still open — current fix is a per-strategy sample-size label, not a ranking-level adjustment.

**P1 — Don't lose or duplicate trades**
4. ~~Add transactional locking / unique constraint to prevent duplicate position opens (§2.4).~~ ✅ Done (unique generated-column index).
5. Add max-daily-loss / max-drawdown / max-concurrent-position circuit breakers to the paper engine. ⬜ Still open — now the top priority in this tier.
6. Add retry + secondary channel for Telegram alert delivery. ⬜ Still open.

**P2 — Security hardening before any wider deployment**
7. Remove auto-seeded default password; force first-login password reset. ⬜ Still open — recommend prioritizing this next given the repo is public.
8. Add login rate-limiting/lockout. ⬜ Still open.
9. Add a server-side session revocation list (even a simple DB table of invalidated token IDs) and an admin action audit log. ⬜ Still open.

**P3 — Scale & realism**
10. Replace full-history re-simulation with an incremental "last N bars" evaluation path for live polling. 🟡 Half done — open-position updates are incremental now; new-entry detection still re-simulates full history.
11. Move volatility/size-aware slippage model into `simulate()`. ⬜ Still open.
12. Add a portfolio-level exposure/margin dashboard aggregating all active paper configs. ⬜ Still open.

**P4 — Toward live trading (only after P0–P2 are solid)**
13. Build an execution adapter behind an explicit `LIVE_TRADING_ENABLED` flag with hard per-trade and daily notional caps, starting with a single symbol/strategy in small size.

---

## 7. Re-Review Findings (Follow-Up Pass)

Re-cloned and diffed `master` (116 commits, up from the initial review point) against every item in §2–§4 above by reading the actual diffs, not just commit messages. Summary:

**Verified fixed, not just claimed:**
- §2.1 Position sizing — `backtest/accounting.py::calculate_position_size()` is genuinely called from `paper/engine.py` at open time; `db/migrate_p0_paper_accounting.sql` adds the real `quantity`/`notional_value` columns to back it.
- §2.2 Funding cost — `calculate_funding_cost()` is wired into the close path with a persisted `funding_pnl` column.
- §2.4 Duplicate positions — solved at the database layer with a generated-column unique index (`open_position_key`), which is a stronger guarantee than the transactional-lock fix originally suggested, since it holds regardless of what future application code does.

**Partially fixed:**
- §2.3 Full-history re-simulation — the expensive path for *managing open positions* is gone (now checks only the latest bar). The expensive path for *detecting new entries* on flat configs is unchanged — still runs the full strategy function over the whole fetched history every poll.
- Multiple-testing/overfitting risk on the 24-strategy ranking — `ai/insight_engine.py` now attaches a sample-size-based "confidence" label per strategy, which is useful context but doesn't correct for the fact the reported winner was chosen as the best of 24 candidates (a proper fix would be a deflated Sharpe ratio or probability-of-backtest-overfitting statistic on the ranking itself, not a per-strategy trade-count label).

**Still open, unchanged:**
- Default `admin`/`admin123` seeding (§3.1) — highest-priority remaining item given the repo is public.
- Login rate-limiting/lockout, token revocation, audit log (§3.2–§3.4).
- Circuit breakers / max daily loss / max drawdown kill switch.
- Volatility/size-aware slippage model (the *application* of slippage was refactored into `accounting.py`, but the rate is still one flat constant).
- Portfolio-level exposure/margin dashboard across concurrent configs.
- Telegram alert retry/fallback channel and reproducibility metadata on trades.

**Net assessment:** the two Critical findings that most affected whether backtest/paper numbers could be *trusted* (risk sizing, funding cost) are fixed correctly and verified in code — this was the right thing to prioritize first. The next highest-leverage work, in order, is: (1) remove the default-admin footgun since the repo is public, (2) add basic circuit breakers before this is ever pointed at live capital, (3) close the remaining half of the re-simulation performance gap, (4) replace the sample-size heuristic with an actual overfitting-adjusted ranking metric.

## 8. Notes on Scope

This review focused on the parts of the codebase most consequential to a trader relying on this system's numbers: `paper/engine.py`, `backtest/simulate.py`, `web/auth.py`, and the schema. It did not do a line-by-line audit of every one of the 24 individual strategy files under `backtest/strategies/`, `ai/insight_engine.py`'s regime-detection heuristics, or the frontend JS in `web/static` — those would be reasonable next targets, particularly re-checking each strategy's condition functions for their own lookahead risks (the shared `simulate()` engine is lookahead-safe, but a strategy could still leak future information into its `long_condition`/`stop_target` if it isn't careful about which row it reads from).
