# signal-bot — Professional Trader Review & Improvement Plan

**Repo:** `kurnFR/signal-bot` (cloned and inspected directly — not a Signal-messenger bot despite the name; it's a **crypto quant signal/backtesting/paper-trading platform** for Binance spot & futures, with a FastAPI web dashboard, MySQL storage, 24 backtestable strategies, an AI "insight engine," and Telegram alerts).

This review is scoped to what matters for a **professional trader**: execution realism, risk-management correctness, capital accounting, operational reliability, and security — not just code style. Findings below were confirmed by reading the actual source (`paper/engine.py`, `backtest/simulate.py`, `web/auth.py`, `db/schema.sql`, `config.py`), not guessed from the README.

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

### 2.1 Risk sizing is configured but silently ignored (Critical)
`paper_configs` stores `allocated_capital` and `risk_per_trade_pct` per strategy, and the README advertises *"1-Click Deployment to Paper Trading with custom capital allocation and risk per trade."* However, in `paper/engine.py`, the position-opening logic (`INSERT INTO paper_positions ...`) never reads `cfg["allocated_capital"]` or `cfg["risk_per_trade_pct"]` — no position size / quantity field even exists in the `paper_positions` schema. Every position is tracked purely in **percent return and R-multiple**, regardless of the capital the user configured.

**Impact:** two strategies with identical R-multiples but wildly different configured capital/risk will report identical performance — the dashboard cannot show real dollar equity curves per strategy, and a trader cannot tell whether a "winning" strategy is winning at a risk size they actually specified.

**Fix:** compute `position_size = (allocated_capital * risk_per_trade_pct / 100) / initial_risk_price`, persist `quantity`/`notional` on `paper_positions`, and derive `realized_pnl_usd` on close from `quantity * price_delta`. Roll a per-strategy equity curve from that instead of only R-multiples.

### 2.2 Perpetual futures funding cost is not charged (Critical)
`FUNDING_STRATEGIES` use funding rate as an *entry signal* (`backtest/optimize.py`, `run_backtest.py`, `walk_forward.py` all pass `include_funding=...`), but `backtest/simulate.py`'s P&L calculation only applies `BACKTEST_FEE_PCT` and `BACKTEST_SLIPPAGE_PCT` — there is no funding accrual for the number of 8‑hour funding intervals a futures position is held through. For any multi-day-hold futures strategy, this **overstates** backtested and paper-traded returns, sometimes materially (funding drag compounds over holding period and can flip a marginal strategy from profitable to unprofitable).

**Fix:** in `simulate()`, when `market == "futures"`, look up realized funding rate over `[entry_time, exit_time]` from the `funding_rate` table already being collected, and subtract `sum(funding_rate_i * notional)` from net P&L. This data already exists in the DB (`ws_funding.py`, `backfill_funding.py`) — it just isn't being used at settlement time.

### 2.3 Re-evaluation loop re-simulates the entire history every cycle (High — performance/scale)
In `evaluate_paper_positions()` (`paper/engine.py`), for every active config with no open position, the code calls `strat_fn(df, run_params)` — running the **full backtest simulation over the entire fetched history** just to check whether the *most recent* candle produced a new signal. As symbol/timeframe/strategy count grows, this becomes O(n) per poll per config, which won't scale past a handful of configs on tight polling intervals (e.g. 1m/5m timeframes).

**Fix:** add an incremental "evaluate last N bars only" path for live signal checking, reserving the full simulate() for backtesting/optimization only.

### 2.4 No locking around position open/close (High — correctness under concurrency)
`get_active_positions()` is read without `SELECT ... FOR UPDATE`, and there's a read-then-insert gap between checking `open_pos_map` and inserting a new `paper_positions` row. If `evaluate_paper_positions()` is ever invoked concurrently (e.g., a scheduler double-fires, or a manual "re-run" button is clicked while a background poller is running), the same signal can open **duplicate positions** for the same `(symbol, market, timeframe, strategy)`.

**Fix:** wrap the check-then-insert in a transaction with `SELECT ... FOR UPDATE` on `paper_configs`/`paper_positions`, or enforce a DB-level unique constraint on `(symbol, market, timeframe, strategy_name, status='OPEN')`.

### 2.5 Silent NaN swallowing on trailing-stop ATR (Low-Medium)
In both `paper/engine.py` and `backtest/simulate.py`, `atr` is pulled with `.get("atr", 0)` / accessed directly, and if the value is `NaN` (common at the start of a series before the ATR window fills), `atr > 0` silently evaluates to `False` in Python — trailing-stop activation is skipped with no warning logged. This can cause a paper strategy to quietly run in fixed-SL mode when the user believes trailing is active.

**Fix:** explicitly `if pd.isna(atr): log.warning(...); skip or fallback`, rather than relying on NaN comparison semantics.

### 2.6 Module-level DB side effects on import (Medium — operability)
`paper/engine.py` calls `seed_validated_paper_configs()` at **import time** (bottom of file), and `web/auth.py`'s `seed_default_admin()` presumably runs similarly at app startup. Any script that merely imports `paper.engine` (tests, a REPL, a different worker) triggers a live DB connection and write as a side effect. This makes the module hard to unit-test and can cause surprising writes in environments where the DB isn't ready yet (crashes on import instead of a clean startup-sequence error).

**Fix:** move seeding into an explicit `init_app()` / startup hook called once by `run_web.py`, not at import time.

---

## 3. Security Findings

1. **Default credentials auto-created and logged (`admin` / `admin123`)** — `web/auth.py::seed_default_admin()` runs unconditionally when the `users` table is empty and prints the password to stdout/logs. For anyone who forgets to rotate it (very common), this is an open admin account on any exposed dashboard. Recommend: generate a random default password and force a change on first login, or require an env var for the initial password.
2. **No login rate-limiting / lockout** — nothing in `web/auth.py` throttles repeated login attempts, so the login endpoint (wherever it lives in `web/routes`) is brute-forceable against the (often default) admin password.
3. **Stateless tokens can't be revoked** — sessions are self-contained HMAC tokens with a 24h expiry and no server-side blacklist. "Logout" can't actually invalidate a token before expiry, and there's no mechanism to force-expire all sessions after a suspected compromise short of rotating `SECRET_KEY` (which invalidates *everyone*).
4. **No audit log for trading actions** — activating/deactivating a paper config, changing allocated capital, or promoting a strategy from backtest to paper trading isn't recorded anywhere with a user/timestamp. For anything resembling professional use (even paper trading with real strategy decisions riding on it), you want an immutable action log.

---

## 4. Gaps Relative to "Professional Trader" Expectations

These aren't bugs in existing code — they're missing capabilities a professional would expect before trusting this beyond a hobby setup.

- **No live execution path.** Everything is backtest + paper (shadow) trading; there is no broker/exchange order-placement integration anywhere in the codebase (verified: no signed REST order calls exist). Moving from "signal generator" to "trading system" requires an execution adapter (even a simple Binance REST order-placement module gated behind explicit opt-in and hard position/notional limits).
- **Single-position-per-strategy only; no portfolio-level risk view.** `simulate()` deliberately holds one position at a time per strategy run, which is fine for isolated backtests, but the paper engine has no aggregate view of *total* exposure/margin usage across all active configs simultaneously (e.g., correlated BTC/ETH/BNB longs stacking risk). A professional needs a portfolio risk dashboard: gross/net exposure, per-asset concentration, and margin utilization for futures configs.
- **No slippage model beyond a flat constant.** `BACKTEST_SLIPPAGE_PCT` is a single fixed percentage regardless of order size, volatility regime, or liquidity — unrealistic for anything beyond small size on BTC/ETH. A volume/ATR-scaled slippage model would materially improve backtest fidelity, especially for smaller-cap symbols in the `SYMBOLS` list.
- **Strategy selection risk (multiple-testing / overfitting) isn't explicitly guarded against.** With 24 strategies "battling" for a Top-3 podium and only a single walk-forward/holdout split, the winner is subject to selection bias — the best of 24 backtests on a fixed holdout window will look better than its true expected edge. Recommend: report a multiple-testing-adjusted confidence measure (e.g., deflated Sharpe ratio / probability of backtest overfitting à la Bailey & López de Prado) alongside the ranking, not just raw expectancy/PF.
- **No circuit breakers.** There's no max-daily-loss, max-drawdown, or max-concurrent-positions kill switch in the paper engine — something a professional risk desk would require even in shadow mode, since it's the exact logic that will later be reused for live capital.
- **No alert delivery guarantees.** `notifications/telegram_notifier.py` failures are caught and logged (`logger.warning`) but not retried or queued — a transient Telegram outage silently drops a live entry/exit alert with no fallback channel (email/webhook) and no "missed alert" reconciliation.
- **No backtest/paper reproducibility metadata.** Trade records don't store the exact strategy parameter set or code version used to generate them, making it hard to explain a historical paper trade after `BASE_PARAMS` or a strategy file changes later.

---

## 5. Prioritized Improvement Roadmap

**P0 — Trust the numbers**
1. Wire `allocated_capital` / `risk_per_trade_pct` into actual position sizing and dollar P&L (§2.1).
2. Charge funding costs against futures paper/backtest P&L (§2.2).
3. Add a deflated/adjusted performance metric to the strategy ranking to counter multiple-testing bias (§4).

**P1 — Don't lose or duplicate trades**
4. Add transactional locking / unique constraint to prevent duplicate position opens (§2.4).
5. Add max-daily-loss / max-drawdown / max-concurrent-position circuit breakers to the paper engine.
6. Add retry + secondary channel for Telegram alert delivery.

**P2 — Security hardening before any wider deployment**
7. Remove auto-seeded default password; force first-login password reset.
8. Add login rate-limiting/lockout.
9. Add a server-side session revocation list (even a simple DB table of invalidated token IDs) and an admin action audit log.

**P3 — Scale & realism**
10. Replace full-history re-simulation with an incremental "last N bars" evaluation path for live polling.
11. Move volatility/size-aware slippage model into `simulate()`.
12. Add a portfolio-level exposure/margin dashboard aggregating all active paper configs.

**P4 — Toward live trading (only after P0–P2 are solid)**
13. Build an execution adapter behind an explicit `LIVE_TRADING_ENABLED` flag with hard per-trade and daily notional caps, starting with a single symbol/strategy in small size.

---

## 6. Notes on Scope

This review focused on the parts of the codebase most consequential to a trader relying on this system's numbers: `paper/engine.py`, `backtest/simulate.py`, `web/auth.py`, and the schema. It did not do a line-by-line audit of every one of the 24 individual strategy files under `backtest/strategies/`, `ai/insight_engine.py`'s regime-detection heuristics, or the frontend JS in `web/static` — those would be reasonable next targets, particularly re-checking each strategy's condition functions for their own lookahead risks (the shared `simulate()` engine is lookahead-safe, but a strategy could still leak future information into its `long_condition`/`stop_target` if it isn't careful about which row it reads from).
