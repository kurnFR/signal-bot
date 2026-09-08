# Signal Bot — Next Improvement Project

**Created:** 2026-09-08  
**Source of truth:** `PROJECT_STATUS_AUDIT.md`  
**Scope:** Trading Trust & Risk Infrastructure + ML Strategy Research  
**Status:** IMPLEMENTATION — P0.1 through P0.5 are being closed incrementally; P1.8 ML strategy design is locked for implementation

## Objective

Make backtest, paper-trading, AI, and operational APIs trustworthy and internally consistent before adding live exchange execution, while adding a controlled machine-learning research path that uses existing data, features, strategies, and configurable parameters.

## Current implementation checkpoint

- **P0.1 API authorization/RBAC:** implemented; runtime authorization matrix still needs verification.
- **P0.2 canonical accounting:** implemented in `backtest/accounting.py` and used by backtest/paper close accounting.
- **P0.3 paper sizing/accounting:** engine integration implemented; DB migration and runtime verification remain.
- **P0.4 futures funding:** canonical funding calculation and paper-engine event lookup implemented; runtime data verification remains.
- **P0.5 execution parity:** contract/tests/documentation exist; the paper state-machine integration is still pending.
- **P0.6/P0.7/P0.8:** partially covered by the P0.2/P0.3 changes, but final regression gates remain open.
- **P1.8 ML strategy:** architecture and leakage/validation contract documented in `ML_STRATEGY_PROJECT.md`; implementation starts with repository feature/strategy/optimizer audit.

## Non-negotiable gates

Live exchange execution MUST remain disabled until all P0 items are implemented, regression-tested, and reviewed.

ML models MUST remain research artifacts until they pass chronological validation, untouched OOS evaluation, cost-aware trading metrics, stability, and paper-eligibility gates.

## P0 — Must Fix First

### P0.1 API authorization / RBAC
- Protect all operational routers with explicit authentication and role checks.
- Public endpoints must be explicitly allowlisted.
- Viewer: read-only data/backtest access.
- Trader: paper trading operations.
- Admin: users, configuration, sensitive operational controls.
- Add authorization regression tests for every operational endpoint.

**Implementation status:** centralized API middleware and role helpers are implemented. Runtime 401/403/200 verification remains.

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

**Implementation status:** canonical accounting primitives are implemented. Paper close accounting now consumes the same module.

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

**Implementation status:** implemented in `paper/engine.py`; requires the P0 paper migration plus runtime verification.

### P0.4 Futures funding accounting
For every futures position, accrue actual funding payments for funding intervals crossed by the position.

Store funding separately from trading fees and slippage. Include funding in net P&L and equity.

**Implementation status:** implemented against the repository's `funding_rate` table. Runtime verification against stored funding events remains.

### P0.5 Paper/backtest execution parity
Define and enforce the lifecycle:

`signal candle closes -> pending entry -> next candle opens -> active position -> future observations -> exit`

Do not use historical future candles to make a real-time paper decision. If intrabar SL/TP cannot be known at the configured timeframe, use a documented lower-timeframe execution policy or conservative bar policy.

**Implementation status:** parity contract and accounting tests are implemented. The explicit pending-entry state machine is the next coding step.

### P0.6 Correct paper R/P&L
Paper R must be derived from the same canonical net-return/accounting definition as backtest. Fees, slippage and funding must be included consistently.

**Implementation status:** realized paper R/net P&L now use canonical accounting. Final synthetic parity tests remain.

### P0.7 Idempotent paper execution
Prevent duplicate OPEN positions for the same configuration using both application logic and database-level uniqueness/locking. Concurrent `/sync` calls must be safe.

**Implementation status:** DB uniqueness plus application duplicate handling and compare-and-set close logic are implemented. Concurrency verification remains.

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

**Implementation status:** canonical accounting and parity regression coverage exists; DB-backed paper lifecycle tests are still required.

## P1 — Validation, Risk, Security, and ML

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

### P1.8 Machine Learning Strategy & Optimization
Add ML as a controlled strategy family using the repository's existing data and infrastructure.

**Architecture:**

`existing market data -> shared feature engine -> existing strategy signal -> ML filter -> canonical backtest/accounting -> validation -> OOS -> paper eligibility`

**Phase 1 — Audit existing boundaries**
- Inspect actual feature columns and warmup behavior.
- Inspect strategy registry and signal schema.
- Inspect `backtest/params.py` and parameter types/ranges.
- Inspect optimizer search mechanics.
- Inspect holdout and temporal-stability inputs/outputs.
- Confirm dependency constraints before adding ML libraries.

**Phase 2 — Dataset and leakage-safe training**
- Build point-in-time training examples.
- Generate labels from the actual trading/execution rules.
- Prohibit future candles, future returns, future-derived indicators, future funding information, and test-period statistics as features.
- Use chronological train/validation/test splitting; never random-shuffle market observations.

**Phase 3 — Baseline models**
Start with:
- Logistic Regression;
- Random Forest;
- HistGradientBoosting.

Do not introduce LSTM/Transformer complexity until simpler tabular models demonstrate a repeatable edge.

**Phase 4 — Parameter optimization**
The ML system may search selected existing strategy parameters and ML hyperparameters, but must use experiment-specific validated copies rather than mutate global `BASE_PARAMS`.

Parameters are explicitly classified as:
- `FIXED`;
- `OPTIMIZABLE`;
- `ML_FEATURE`;
- `MODEL_HYPERPARAMETER`.

**Phase 5 — Trading-aware evaluation**
Optimize and compare through the real backtest simulator using canonical:
- fees;
- slippage;
- funding;
- net P&L;
- R/expectancy;
- drawdown;
- trade count.

Accuracy alone is never sufficient.

**Phase 6 — OOS and promotion gates**
A model must pass minimum trade count, cost-aware profitability/expectancy, acceptable drawdown, untouched OOS performance, chronological stability, leakage checks, accounting parity, artifact/version integrity, and portfolio-risk limits before becoming paper eligible.

**Phase 7 — Dashboard integration**
Add ML to the existing strategy/settings menu. Expose training configuration, model selection, hyperparameters, probability threshold, optimizable parameter ranges, and promotion gates without requiring source-code edits.

Training settings and locked paper-model settings must be distinct so that later experimentation cannot silently change the paper model.

**Detailed design:** `ML_STRATEGY_PROJECT.md`

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
- Strategy/settings menu to support controlled ML experiments and locked model versions.

### REWRITE / CENTRALIZE
- Financial accounting formulas into one shared accounting engine.
- Paper execution state machine.
- ML experiment/model boundary around existing feature, strategy, and backtest components.

### ADD
- Risk/portfolio layer.
- Regression/security tests.
- Funding accounting.
- Idempotency protection.
- OOS evidence tracking.
- Observability/audit trail.
- ML dataset/training/optimization/registry pipeline.

### REMOVE
- Hard-coded default admin credential.
- Wildcard credentialed CORS.
- Silent parameter fallbacks for required parameters.
- Any implication that heuristic AI/ranking output proves profitability.
- Any ML workflow that uses test data for tuning or future information in features.

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

ML models are additionally subject to the ML-specific promotion gates in `ML_STRATEGY_PROJECT.md`; ML profitability is never assumed from backtest ranking alone.
