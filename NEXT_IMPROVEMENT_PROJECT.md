# Signal Bot — Next Improvement Project

**Created:** 2026-09-08
**Source of truth:** `PROJECT_STATUS_AUDIT.md`
**Scope:** Trading Trust & Risk Infrastructure
**Status:** PLANNING — no application code changes in this phase

## Objective

Make backtest, paper-trading, AI, and operational APIs trustworthy and internally consistent before adding live exchange execution or more trading strategies.

## Non-negotiable gates

Live exchange execution MUST remain disabled until all P0 items are implemented, regression-tested, and reviewed.

## P0 — Must Fix First

### P0.1 API authorization / RBAC
- Protect all operational routers with explicit authentication and role checks.
- Public endpoints must be explicitly allowlisted.
- Viewer: read-only data/backtest access.
- Trader: paper trading operations.
- Admin: users, configuration, sensitive operational controls.
- Add authorization regression tests for every operational endpoint.

### P0.2 Shared trading accounting model
Create one canonical accounting module used by both backtest and paper trading for:
- quantity/notional calculation;
- risk-dollar calculation;
- gross P&L;
- fees;
- slippage;
- funding;
- net P&L;
- R-multiple;
- equity updates.

Backtest and paper must not maintain separate formulas for the same financial concepts.

### P0.3 Real paper position sizing
For each position calculate and persist:
- allocated capital;
- risk percentage;
- risk dollars;
- entry price;
- stop price;
- stop distance;
- quantity;
- notional;
- leverage/margin model where applicable.

Reject invalid zero/negative risk or stop distances.

### P0.4 Futures funding accounting
For every futures position, accrue actual funding payments for funding intervals crossed by the position.

Store funding separately from trading fees and slippage. Include funding in net P&L and equity.

### P0.5 Paper/backtest execution parity
Define and enforce the lifecycle:

`signal candle closes -> pending entry -> next candle opens -> active position -> future observations -> exit`

Do not use historical future candles to make a real-time paper decision. If intrabar SL/TP cannot be known at the configured timeframe, use a documented lower-timeframe execution policy or conservative bar policy.

### P0.6 Correct paper R/P&L
Paper R must be derived from the same canonical net-return/accounting definition as backtest. Fees, slippage and funding must be included consistently.

### P0.7 Idempotent paper execution
Prevent duplicate OPEN positions for the same configuration using both application logic and database-level uniqueness/locking. Concurrent `/sync` calls must be safe.

### P0.8 Accounting regression suite
Add automated tests covering:
- long/short position sizing;
- exact stop-loss risk;
- fee and slippage effects;
- funding payments;
- R calculation;
- compounding equity;
- next-open entry;
- same-bar SL/TP policy;
- paper/backtest parity;
- duplicate sync protection.

## P1 — Validation, Risk, and Security Hardening

### P1.1 Authentication security
- Remove hard-coded/default admin credentials.
- Bootstrap admin credentials from environment/one-time setup.
- Force password rotation after bootstrap.
- Add login rate limiting/progressive delay.
- Add session/token revocation.
- Restrict CORS to configured dashboard origins.
- Remove DB connection metadata from browser-facing status responses.

### P1.2 Portfolio risk layer
Add:
- max portfolio exposure;
- max per-symbol exposure;
- max correlated exposure;
- max simultaneous positions;
- daily loss limit;
- drawdown circuit breaker;
- strategy-level risk limits;
- global emergency stop.

### P1.3 Parameter validation
Keep centralized `BASE_PARAMS`. Add per-strategy required parameters, types, valid ranges, and explicit validation. Do not silently replace missing required parameters with defaults.

### P1.4 OOS validation discipline
- Preserve warmup/context for rolling indicators.
- Record holdout evaluations immutably.
- Label repeated holdout checks as secondary/contaminated validation when appropriate.
- Add multiple-testing/winner's-curse reporting.
- Add true rolling walk-forward optimization later; keep current temporal stability testing but rename it accurately.

### P1.5 Battle Royale ranking
Keep the existing UI/backend, but improve eligibility and evidence scoring with:
- minimum trade count;
- positive expectancy;
- cost-aware P&L;
- OOS evidence;
- drawdown;
- uncertainty/confidence intervals;
- parameter-search breadth;
- strategy correlation;
- portfolio concentration;
- futures funding costs.

A rank score is a decision aid, not proof of a genuine edge.

### P1.6 AI Insight corrections
- Calculate EMA20/EMA50/ADX through a shared feature source or explicitly calculate them in the AI layer.
- Remove unsupported statistical wording based solely on trade count.
- Derive Kelly from actual win/loss distribution.
- Never recommend positive Kelly risk when estimated edge is negative.
- Clearly distinguish heuristic AI diagnosis from validated statistical evidence.

### P1.7 Custom strategy hardening
- Move persistence from hard-coded filesystem path to configurable/database storage.
- Add ownership/versioning.
- Validate identifiers and supported operations.
- Escape/sanitize dynamic frontend content.
- Distinguish built-in strategy count from dynamic custom strategies.

## P2 — Reliability and Scalability

### P2.1 Incremental paper evaluation
Stop full-history re-simulation on every paper cycle. Maintain the minimum state/history required for each strategy.

### P2.2 Telegram delivery queue
Add durable notification events, retries, deduplication, delivery status, and dead-letter/error visibility.

### P2.3 Job execution model
Move long-running backfills, feature builds, and Battle Royale runs to controlled background jobs with status, cancellation, and concurrency limits.

### P2.4 Observability
Add structured logs, correlation IDs, job IDs, audit events, latency/error metrics, and operational health checks.

## P3 — Future

### P3.1 Live exchange execution
Only begin after all P0/P1 gates pass and paper results demonstrate stable execution/accounting parity.

Live execution must add exchange order state reconciliation, partial fills, order retries, position reconciliation, API-key isolation, kill switch, and strict exposure limits.

## KEEP / CHANGE / REWRITE / ADD / REMOVE

### KEEP
- Existing data collectors and backfill architecture.
- Existing feature engine as the shared base.
- Existing backtest simulator architecture.
- Existing 24 built-in strategy registry.
- Existing train/holdout concept.
- Existing temporal stability test, with terminology clarified.
- Existing dashboard and Battle Royale UI.

### CHANGE
- Paper accounting and execution lifecycle.
- Ranking/evidence methodology.
- API authorization.
- Authentication bootstrap/security.
- AI statistical claims and regime inputs.
- Custom strategy persistence/security.
- Telegram delivery reliability.

### REWRITE / CENTRALIZE
- Financial accounting formulas into one shared accounting engine.
- Paper execution state machine.

### ADD
- Risk/portfolio layer.
- Regression/security tests.
- Funding accounting.
- Idempotency protection.
- OOS evidence tracking.
- Observability/audit trail.

### REMOVE
- Hard-coded default admin credential.
- Wildcard credentialed CORS.
- Silent parameter fallbacks for required parameters.
- Any implication that heuristic AI/ranking output proves profitability.

## Completion standard

The project is ready to consider live execution only when:

1. all P0 items pass automated tests;
2. all operational API routes are authenticated/authorized;
3. paper and backtest accounting agree on identical synthetic scenarios;
4. futures funding is included in net performance;
5. paper execution does not consume future information;
6. portfolio risk controls and emergency stop are operational;
7. OOS evidence is recorded and reviewed;
8. security regression tests pass;
9. no unresolved P0/P1 financial or security findings remain.
