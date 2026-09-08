# Signal Bot — Project Status & Implementation Audit

**Audit date:** 2026-09-08
**Repository:** `kurnFR/signal-bot`
**Audited branch:** `master`
**Audited HEAD:** `803f45712f3955dc2fafb218f1ed03410e4fac84`

## Purpose

This document is the authoritative synchronization record between the actual repository implementation and the older planning/review Markdown files.

The audit was performed against the current source code, database schema/migrations, web UI, strategy registry, backtest engine, paper engine, AI insight engine, Telegram integration, authentication, and recent Git history.

No code changes were made as part of this audit.

---

## 1. Executive Status

| Area | Current status | Audit verdict |
|---|---|---|
| Raw market data collectors | Implemented | KEEP; operational QA still required |
| Historical backfill | Implemented | KEEP |
| Feature/indicator engine | Implemented | KEEP; extend regime indicators later |
| Backtest engine | Implemented | KEEP; accounting/cost model needs hardening |
| Strategy registry | 12 base + 12 inverse = 24 variants | IMPLEMENTED |
| Parameter centralization | `BASE_PARAMS` exists | KEEP; add validation rather than silent defaults |
| Train/holdout split | Implemented | KEEP; methodology needs stronger OOS discipline |
| Optimization | Implemented | KEEP; selection-bias safeguards needed |
| Walk-forward | Implemented as fixed-parameter temporal stability test | RENAME/CLARIFY; it is not walk-forward optimization |
| Web dashboard | Implemented | KEEP |
| Battle Royale / Top 3 | Backend + UI implemented | IMPLEMENTED; ranking methodology needs hardening |
| AI Insight Engine | Implemented | NEEDS CORRECTION/VALIDATION |
| Paper trading | Implemented | **NOT YET TRUSTWORTHY FOR FINANCIAL ACCOUNTING** |
| Telegram alerts | Implemented | NEEDS reliability/security hardening |
| Authentication | Implemented | NEEDS security hardening |
| RBAC | Partially implemented | **Operational routes are not consistently protected** |
| Portfolio risk controls | Missing | ADD |
| Futures funding accounting | Missing | **P0** |
| Dollar position sizing in paper | Missing | **P0** |
| Live exchange execution | Missing | Correctly deferred |

---

## 2. Documentation Synchronization Findings

### `README.md`

The README now describes the project as having Web Dashboard, Paper Trading, AI Insights, Telegram Alerts, and 24 strategy variants. This matches the current implementation and recent commits.

However, some older sections still describe earlier Step 1/2/3 scope and should eventually be consolidated into a current architecture guide.

### `BUG_ANALYSIS_AND_IMPROVEMENTS.md`

This document is now **partially stale**.

It correctly records that `/api/backtest/run-all` was implemented, but its historical test result says 16 strategies. The current registry contains 12 base strategies plus 12 inverse variants = 24 strategy variants.

It also recommends changing strategies to `params.get(..., default)`. This audit changes that recommendation: required strategy parameters should be validated centrally and then accessed strictly. Silent fallback defaults can hide configuration errors.

### `WEBSITE_IMPROVEMENT_RECOMMENDATIONS.md`

Most of the original feature plan has already been implemented. It should be treated as historical implementation planning, not as the current backlog.

### `PROFESSIONAL_TRADER_REVIEW.md`

Most critical findings are confirmed by the current code. This audit adds several findings that were not sufficiently covered there, especially API authorization, paper/backtest execution semantics, AI regime-data mismatch, and paper R/P&L inconsistencies.

---

## 3. Backtest Engine Audit

### 3.1 Shared simulation engine

`backtest/simulate.py` correctly centralizes:

- next-bar-open entry execution;
- SL/TP/timeout handling;
- conservative same-bar SL-before-TP assumption;
- fee and slippage deduction;
- R-multiple calculation;
- optional trailing stops;
- one-position-at-a-time simulation.

**Status: KEEP.**

### 3.2 Critical limitation: no futures funding cost

Funding is collected and can be used as a signal, but the shared simulator does not charge funding while a futures position is open.

**Severity: P0.**

Until fixed, futures backtest results can overstate net performance.

### 3.3 Dollar equity curve

`backtest/equity.py` converts R-multiples into compounded dollar equity using fixed-fractional risk. This is mathematically consistent with the current R model for isolated trades.

**Status: KEEP**, but later unify the accounting model with paper trading.

### 3.4 Metrics

`backtest/metrics.py` correctly computes win rate, expectancy, profit factor, and cumulative-R drawdown for normal trade lists.

Minor issue: `total_trades` counts trades even if `r_multiple` is `None`, while win-rate/expectancy exclude those entries. This should be made internally consistent.

**Severity: P2.**

---

## 4. Strategy Audit

### Registry

The current registry contains:

- 12 base strategies;
- 12 inverse variants;
- 24 registered strategy variants in total.

**Status: IMPLEMENTED.**

### Lookahead scan

The source scan found no obvious `shift(-...)`, `center=True`, or `bfill` usage in the strategy code.

`double_pattern.py` intentionally uses centered rolling pivots and delays their availability by the pivot confirmation period. The implementation documents and applies that confirmation delay, so this is not classified as a lookahead bug.

`trend_alignment.py` uses an explicit higher-timeframe availability timestamp and `merge_asof(direction='backward')`, which is the correct general pattern for avoiding cross-timeframe lookahead.

`pairs_ratio.py` explicitly documents that its ratio OHLC construction is a backtest approximation and that live implementation requires two real legs.

**Status: No confirmed strategy-level lookahead bug from source inspection.**

A dedicated automated regression suite should still be added to prove this continuously.

### Parameter handling

`BASE_PARAMS` is already centralized in `backtest/params.py` and the major strategy modules read required values from the supplied parameter dictionary.

**Decision:** do NOT mass-rewrite all strategies to `params.get()` defaults.

Instead add:

1. required-parameter declarations per strategy;
2. type/range validation;
3. one validated parameter object/dictionary before execution;
4. explicit failure for missing required parameters.

---

## 5. Train / Holdout / Optimization Audit

### Holdout

`run_holdout_check.py` deliberately requires `--confirm`, which is good process protection, but the code does not technically prevent repeated holdout checks.

Therefore the "one-time" discipline is currently procedural, not enforced.

**Severity: P1.**

Later add an immutable holdout evaluation record and prevent repeated exploratory evaluations for the same project/selection cycle, or clearly label every subsequent check as contaminated/secondary validation.

### Boundary-state issue

Several strategies calculate rolling/EMA state from the segment passed to them. When a holdout or walk-forward fold is passed as an isolated dataframe, indicators such as EMA can restart at the beginning of the segment instead of inheriting the state that existed immediately before that period.

This is not future leakage, but it can make out-of-sample results differ from continuous live execution.

**Severity: P1.**

Correct approach: preserve indicator state or provide sufficient pre-period warmup/context while scoring only trades inside the evaluation window.

### Walk-forward terminology

`walk_forward.py` explicitly performs fixed-parameter testing on sequential folds of the TRAIN data. It is a temporal stability test, not true walk-forward optimization where parameters are trained on a rolling window and evaluated on the following unseen window.

**Decision:** KEEP methodology, but rename/clarify documentation and add a true rolling OOS evaluator later.

### Optimization

`optimize.py` correctly searches TRAIN data and leaves the holdout untouched during optimization.

However, selecting the best combination from many combinations creates selection bias even when the holdout is untouched.

**P1:** add multiple-testing-aware reporting and require independent OOS confirmation before deployment.

---

## 6. Strategy Ranking / Battle Royale Audit

The current `/api/backtest/run-all` implementation is real and active. It evaluates the registered strategy set and returns `top3` plus `allResults`.

The current ranking requires:

- positive expectancy;
- profit factor >= 1;
- minimum 10 trades;
- composite rank score with expectancy, PF, win rate, trade count, and drawdown penalty.

**Status: IMPLEMENTED.**

### Important ranking limitations

The current ranking is still a heuristic score, not a statistical proof that the winner has a real edge.

It does not currently account for:

- multiple-testing / winner's curse;
- confidence intervals;
- parameter-search breadth;
- independent validation history;
- strategy correlation;
- portfolio concentration;
- funding costs in futures;
- execution-quality differences.

**Decision:** KEEP UI/backend; redesign ranking eligibility and evidence scoring in the next improvement project.

---

## 7. Paper Trading Audit — Critical

### 7.1 Configured capital/risk is not used for actual paper position accounting

`paper_configs` stores `allocated_capital` and `risk_per_trade_pct`, but `paper_positions` has no quantity/notional/risk-dollar fields and the opening path does not use those values to size the position.

**P0 — confirmed.**

### 7.2 Paper R-multiple is inconsistent with backtest R-multiple

Backtest R is based on `net_return_pct`, including modeled costs.

Paper close logic computes `net_ret` with fees/slippage, but then calculates `r_mult` from `raw_ret` instead of the net return. Therefore paper R excludes the modeled trading costs while paper percentage return includes them.

**P0/P1 — confirmed accounting inconsistency.**

### 7.3 Paper entry/exit semantics do not reproduce the backtest lifecycle

The paper engine discovers a signal only after the candle is closed, then records the position using the next candle's open price found by the historical simulation. It subsequently sets current price to the already-closed candle's close and does not reconstruct the intra-candle SL/TP outcome of that entry candle.

This means paper trading is not a true real-time execution simulator for the timeframe being monitored.

**P0 — execution-model mismatch.**

The next implementation should explicitly model:

`signal candle closes -> pending order -> next candle opens -> position becomes active -> future market observations update/exit position`.

For realistic intrabar SL/TP behavior, lower-timeframe/tick data or a defined bar-resolution execution policy is required.

### 7.4 Full-history re-simulation on every paper cycle

For configurations without an open position, the paper engine calls the full strategy simulation to determine whether the newest candle generated an entry.

**P1:** add an incremental signal evaluator using only the required recent state/history.

### 7.5 Duplicate position race

The application performs a read/check followed by an insert without database-level protection against concurrent evaluators.

**P1:** enforce uniqueness for one OPEN position per configuration and/or use transactional locking.

### 7.6 Paper metrics are global, not portfolio-aware

`get_paper_metrics()` aggregates every paper trade into one total. There is no per-strategy, per-symbol, per-market equity or exposure accounting.

**P1:** add portfolio ledger and per-config equity/risk views.

---

## 8. Futures Audit

Funding data exists in the database and is already used as a strategy signal.

What is missing is the settlement/accounting side:

`funding rate -> funding payment -> net trade P&L -> equity curve`.

**P0:** implement funding accrual using the actual funding intervals crossed by each futures position.

Also distinguish:

- signal funding data;
- realized funding payment;
- estimated funding at decision time.

---

## 9. AI Insight Engine Audit

`ai/insight_engine.py` is implemented, but several outputs should not currently be treated as institutional-grade statistical conclusions.

### 9.1 Sample-size labels are heuristic

The engine calls >=45 trades "High (Statistically Robust)". A raw trade count alone does not establish statistical robustness.

**P1:** replace with uncertainty-aware metrics and explicitly call the current label heuristic.

### 9.2 Kelly calculation is oversimplified

The engine assumes a fixed 2.0R payoff ratio rather than deriving payoff statistics from the actual trade distribution.

It also clamps half-Kelly to a minimum of 0.2%, which can recommend positive risk even when the calculated Kelly fraction is negative.

**P0/P1:** never recommend positive Kelly risk when the estimated edge is negative; use actual average win/loss or expectancy distribution and cap the recommendation conservatively.

### 9.3 Regime detector expects fields not produced by the shared feature engine

The insight engine reads `ema_20`, `ema_50`, and optionally `adx`. The shared `features/indicators.py` currently produces RSI, MACD, ATR, volume profile, S/R, and Fibonacci, but not those EMA columns or ADX.

Because missing values are converted through `float(last_bar.get(..., 0))`, the regime detector can silently fall back to zeros and report a generic regime rather than a genuinely calculated one.

**P0/P1:** either calculate these indicators through the shared feature engine or calculate them explicitly in the AI layer and label the source.

### 9.4 "Live paper" wording

The AI verdict can say "Highly Recommended for Live Paper Trading" based on heuristic thresholds. This should not be interpreted as a statistically validated investment recommendation.

**P1:** make the verdict evidence-based and include validation status, costs, sample size, drawdown, and OOS evidence.

---

## 10. Web/API Security Audit

This is the largest gap not adequately represented in the previous Markdown review.

Authentication exists, but authorization is not consistently applied to operational routes.

### Unprotected operational endpoints identified

The following route modules do not declare authentication dependencies in their current implementations:

- `web/routes/paper_routes.py`
- `web/routes/master_data.py`
- `web/routes/settings.py`
- `web/routes/telegram_routes.py`
- `web/routes/custom_strategy.py`
- `web/routes/backtest.py`

This means the existence of a login screen does not currently mean the corresponding backend operation is protected.

Examples of high-impact consequences:

- unauthenticated paper strategy configuration/deployment;
- unauthenticated paper position close;
- unauthenticated paper sync;
- unauthenticated backfill / feature-build job creation;
- unauthenticated custom strategy creation and persistence;
- unauthenticated system status access;
- unauthenticated Telegram test calls.

**P0 security issue.**

Required policy:

- public: only intentionally public health/search endpoints;
- viewer: read-only data/backtest views;
- trader: paper strategy operations;
- admin: user management, system configuration and sensitive operations.

### 10.1 System status leaks DB connection metadata

`/api/settings/status` returns DB host, port, database name, and username.

**P1:** protect the endpoint and remove unnecessary connection details from browser-visible responses.

### 10.2 Default admin credential

`admin/admin123` is hard-coded and also displayed in the login UI.

**P0/P1:** require an environment-provided bootstrap credential or random one-time bootstrap secret, force password rotation, and never display the credential in the UI.

### 10.3 Session revocation

Signed tokens are stateless and remain valid until expiry even after client-side logout.

**P1:** add token IDs/session records and revocation.

### 10.4 Login rate limiting

No login throttling/lockout was found.

**P1:** add IP/user-aware rate limiting and progressive delay/lockout.

### 10.5 CORS

`web/app.py` currently allows `*` origins with credentials.

**P1:** restrict origins to configured dashboard origins.

### 10.6 Custom strategy / frontend injection surface

Custom strategy metadata is persisted and later rendered through `innerHTML` in the web UI. User-controlled display names/descriptions should be HTML-escaped before rendering.

**P1:** sanitize/escape all dynamic values in frontend templates and validate custom strategy identifiers.

### 10.7 Public repository hygiene

`.env` has been removed from current tracking and `.gitignore` contains the expected environment/secret exclusions. The current `.env.example` uses placeholders.

If any real credential was ever committed in earlier history, it must be considered compromised and rotated even after file deletion.

---

## 11. Custom Strategy Audit

Custom strategies are implemented and persisted in a JSON file.

Current limitations:

- hard-coded filesystem path `/home/BIS/signal-bot/custom_strategies.json`;
- no authentication on creation endpoint;
- no explicit uniqueness/namespace ownership;
- no versioning;
- no validation that requested indicators/operators are supported;
- not included in the static strategy registry count, so "24 strategies" is no longer necessarily true after custom strategies are loaded.

**P1:** move persistence into the database or configurable application data directory, add ownership/versioning, and distinguish `24 built-in variants` from dynamically loaded custom strategies.

---

## 12. Telegram Audit

Telegram integration is implemented and correctly catches delivery exceptions so a failed alert does not crash the trading loop.

However:

- failed alerts are only logged;
- no durable queue/retry exists;
- no reconciliation for missed alerts exists;
- Telegram status/test endpoints are currently unprotected.

**P1:** durable notification queue + retry + delivery status + protected diagnostics endpoint.

---

## 13. Data / Operations Audit

The project has:

- OHLCV storage;
- funding rate history;
- open interest;
- liquidations;
- collector heartbeat;
- background backfill/feature jobs.

The web job system is in-memory (`JOBS` dictionary) and uses daemon threads.

Consequences:

- jobs disappear on process restart;
- multi-worker deployment will not share job state;
- no durable job history exists;
- long-running jobs are not managed by a real queue.

**P2:** move job execution/status to a durable task mechanism when deployment scale requires it.

---

## 14. Repository Hygiene

The current repository still contains tracked `__pycache__` directories/files even though `.gitignore` excludes them. `.gitignore` only prevents new files from being tracked; it does not remove already tracked artifacts.

**P2:** remove tracked bytecode artifacts from Git.

---

## 15. Definitive KEEP / CHANGE / REWRITE / ADD / REMOVE Matrix

### KEEP

- MySQL raw/derived data separation.
- Shared feature calculations.
- Shared backtest simulation architecture.
- 24 built-in strategy registry.
- Train/holdout separation concept.
- Parameter optimizer restricted to train data.
- Web dashboard foundation.
- Authentication cryptographic primitives.
- Telegram integration architecture.

### CHANGE

- Paper execution lifecycle.
- Paper R/P&L calculation.
- Futures cost model.
- Ranking eligibility/scoring.
- AI statistical labels and Kelly logic.
- Holdout boundary handling.
- API authorization/RBAC.
- CORS.
- Default admin provisioning.
- Telegram reliability.
- Custom strategy persistence/validation.

### REWRITE

- Paper accounting layer into a proper position/ledger model.
- Paper/backtest cost accounting into a shared accounting model.
- AI regime detector around indicators actually available from the shared feature engine.
- Authentication session lifecycle if revocation/audit requirements are adopted.

### ADD

- quantity/notional/risk-dollar fields;
- realized/unrealized dollar P&L;
- funding payments;
- portfolio exposure/margin;
- circuit breakers;
- transaction/idempotency controls;
- durable audit log;
- parameter validation;
- OOS evidence score;
- statistical uncertainty / multiple-testing adjustment;
- true rolling walk-forward OOS evaluation;
- notification queue/retry;
- security tests;
- lookahead regression tests;
- paper-vs-backtest parity tests.

### REMOVE / DEPRECATE

- hard-coded default admin password;
- unauthenticated operational endpoints;
- misleading "statistically robust" labels based only on trade count;
- tracked `__pycache__` artifacts;
- stale documentation claims that completed features are still future work.

---

## 16. Next Improvement Project — Recommended Order

### P0 — Make the numbers trustworthy

1. Shared trade accounting model.
2. Paper position sizing from allocated capital + risk %.
3. Dollar P&L and equity ledger.
4. Correct net R after fees/slippage.
5. Futures funding accrual.
6. Correct paper pending-entry lifecycle.
7. Backtest/paper parity tests.

### P0 — Close security exposure

8. Apply authentication/RBAC to all operational APIs.
9. Remove default credential exposure.
10. Restrict CORS.
11. Protect system/Telegram diagnostics.

### P1 — Make strategy selection statistically safer

12. Validated parameter contract.
13. Better ranking eligibility.
14. Multiple-testing-aware evidence metrics.
15. True rolling OOS validation.
16. Boundary-state/warmup handling.

### P1 — Make risk operationally safe

17. Circuit breakers.
18. Duplicate/idempotency protection.
19. Portfolio exposure and margin controls.
20. Audit log.

### P1 — Correct AI decision support

21. Shared regime indicators.
22. Evidence-based confidence labels.
23. Correct Kelly/risk recommendation.
24. Deployment recommendation tied to OOS evidence.

### P2 — Reliability and scale

25. Telegram durable queue/retry.
26. Incremental signal evaluation.
27. Durable job queue/status.
28. Repository hygiene and automated CI tests.

### P3 — Live execution

Only after P0/P1 items are complete and paper results demonstrate stable parity with backtest assumptions.

---

## 17. Definition of "Ready for Live Trading"

The project should **not** be considered live-trading-ready until all of the following are true:

- paper and backtest execution semantics are reconciled;
- dollar P&L reconciles from entry to exit;
- fees, slippage and futures funding are included;
- position sizing is deterministic and auditable;
- portfolio exposure limits exist;
- circuit breakers exist;
- duplicate execution is impossible through concurrency;
- every operational API is authenticated/authorized;
- strategy results have independent OOS evidence;
- ranking accounts for multiple testing;
- paper-vs-backtest parity tests pass;
- security tests pass;
- alert delivery is durable/reconciled;
- live execution remains disabled by default and requires explicit controls.

**Current readiness verdict: NOT LIVE-TRADING-READY.**

The project is a strong development foundation, but the next project should focus on **trustworthiness, accounting, security, and validation before adding more trading features.**
