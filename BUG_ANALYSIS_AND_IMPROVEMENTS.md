# Bug Analysis & Improvements Review
## Crypto Signal Bot — Current Synchronized Status

**Last audited:** 2026-09-08  
**Audited HEAD:** `803f45712f3955dc2fafb218f1ed03410e4fac84`  
**Authoritative audit:** [`PROJECT_STATUS_AUDIT.md`](PROJECT_STATUS_AUDIT.md)

> This document is now a synchronized backlog. Historical implementation notes remain useful, but the current source code is the authority. Do not treat older P0/P1 labels below as proof that a feature is still missing.

---

## 1. Already Implemented

### `/api/backtest/run-all`

**Status: ✅ IMPLEMENTED**

The endpoint is active in `web/routes/backtest.py` and evaluates the registered strategy set, returns `top3`, `allResults`, profitability eligibility, ranking score, and AI insight.

The repository currently registers **12 built-in strategies + 12 inverse variants = 24 built-in strategy variants**.

The old test note saying "16 strategies" is historical and must not be used as the current count.

### Strategy Battle Royale UI

**Status: ✅ IMPLEMENTED**

The current repository includes the Top-3 Battle Royale/podium UI and deployment flow. The old website recommendation that this UI still needed to be created is stale.

### AI Insight Engine

**Status: ⚠️ IMPLEMENTED, NEEDS CORRECTION**

`ai/insight_engine.py` exists and generates regime, sample-size, risk and deployment guidance. However, its statistical confidence labels, Kelly calculation, and regime indicator inputs need correction before being described as institutional-grade.

### Paper Trading

**Status: ⚠️ IMPLEMENTED, NEEDS HARDENING**

Paper trading exists, but its accounting and execution lifecycle are not yet trustworthy enough for professional financial simulation.

---

## 2. Confirmed Critical Issues From Source Audit

### P0 — Paper position sizing is not actually applied

`paper_configs` stores `allocated_capital` and `risk_per_trade_pct`, but `paper_positions` has no quantity/notional/risk-dollar fields and the opening logic does not size the position from those settings.

**Required:** build a deterministic position-sizing/accounting layer.

### P0 — Futures funding is not charged

Funding data is collected and can be used as a signal, but funding payments are not included in trade settlement/P&L.

**Required:** accrue funding across the actual funding intervals crossed by each futures position.

### P0 — Paper R/P&L is inconsistent with backtest

Backtest R is based on net return after modeled costs, while paper close logic calculates `r_multiple` from raw return. Therefore paper R excludes the same costs that paper percentage return includes.

**Required:** use one shared accounting calculation for both engines.

### P0 — Paper execution lifecycle is not equivalent to real forward execution

The paper engine discovers a signal after a candle has closed and derives the entry from the next historical candle, but it cannot know the next candle's intrabar SL/TP outcome at that time. The current implementation therefore does not faithfully reproduce the live-forward lifecycle.

**Required:** explicit `SIGNAL -> PENDING ENTRY -> EXECUTED -> ACTIVE -> EXITED` state machine.

### P0 — Operational API authorization is incomplete

Authentication exists, but the current operational route modules do not consistently enforce it. The audit identified unprotected operational surfaces including paper trading, master-data jobs, settings diagnostics, Telegram diagnostics, custom strategy creation, and backtest operations.

**Required:** apply `get_current_user` / `require_admin` or appropriate trader/viewer dependencies to every non-public endpoint.

---

## 3. Confirmed High-Priority Issues

### P1 — Duplicate paper positions can race

The paper engine performs read/check followed by insert without database-level protection against concurrent evaluators.

**Required:** transaction/idempotency protection and a database uniqueness rule for one OPEN position per configuration.

### P1 — Full-history re-simulation during paper polling

New entries are detected by running the entire strategy simulation history for each active config.

**Required:** incremental last-N/stateful evaluation path.

### P1 — Holdout "one-time" rule is procedural only

`run_holdout_check.py` requires `--confirm`, but the application does not technically prevent repeated holdout checks.

**Required:** immutable validation records / explicit contamination state if the holdout is reused.

### P1 — Segment boundary state can differ from continuous execution

EMA/rolling strategy calculations can restart when only the holdout/fold dataframe is passed. This is not future leakage, but it can produce boundary artifacts versus continuous live state.

**Required:** preserve state or provide pre-period warmup while scoring only the requested evaluation window.

### P1 — Ranking is heuristic, not statistical proof

The current Battle Royale ranking correctly rejects non-positive expectancy and low-trade results, but it does not account for multiple-testing bias, uncertainty, parameter-search breadth, strategy correlation, or futures funding costs.

**Required:** eligibility gates + uncertainty metrics + multiple-testing-aware evidence score.

### P1 — AI confidence/Kelly/regime logic needs correction

The current AI engine calls 45+ trades "High" confidence, assumes a fixed 2R payoff for Kelly, clamps half-Kelly to a positive minimum, and expects `ema_20`, `ema_50`, and optional `adx` even though the shared feature engine does not currently produce those fields.

**Required:** correct the math and use indicators from a shared, auditable source.

### P1 — Security hardening

Confirmed gaps include default `admin/admin123`, no login rate limiting, stateless non-revocable tokens, broad CORS, and system diagnostics exposing database connection metadata.

### P1 — Telegram delivery reliability

Telegram failures are logged but not durably queued/retried or reconciled.

---

## 4. Lower-Priority / Structural Improvements

- Portfolio-level exposure and margin accounting.
- Max daily loss / max drawdown / max concurrent-position circuit breakers.
- Volatility/liquidity-aware slippage.
- Durable audit log for strategy deployment and configuration changes.
- Durable job queue instead of in-memory daemon threads.
- Custom strategy database persistence/versioning and ownership.
- Frontend HTML escaping for dynamic/custom strategy content.
- Remove tracked `__pycache__` artifacts.
- Automated lookahead, paper/backtest parity, and accounting regression tests.
- Clarify that `walk_forward.py` is currently a fixed-parameter temporal stability test, not walk-forward optimization.

---

## 5. Parameter Handling Decision

The previous recommendation to mass-rewrite strategies to `params.get("KEY", default)` is **rejected**.

`backtest/params.py` already provides a centralized `BASE_PARAMS` source, and strict parameter access is preferable for required parameters.

### New standard

```text
BASE_PARAMS
    ↓
validate required keys/types/ranges
    ↓
strategy execution
```

A missing required parameter should fail explicitly rather than silently falling back to an unrelated default.

---

## 6. Next Improvement Project

### P0 — Trust the numbers

1. Shared trade accounting model.
2. Real paper position sizing.
3. Dollar P&L/equity ledger.
4. Correct net R after costs.
5. Futures funding settlement.
6. Correct paper pending-entry state machine.
7. Backtest/paper parity tests.

### P0 — Close security exposure

8. Protect all operational APIs with RBAC.
9. Remove default credential exposure.
10. Restrict CORS.
11. Protect system/Telegram diagnostics.

### P1 — Safer strategy selection

12. Parameter contract validation.
13. Better ranking eligibility/evidence score.
14. Multiple-testing-aware statistics.
15. True rolling OOS validation.
16. Correct boundary warmup/state handling.

### P1 — Risk controls

17. Circuit breakers.
18. Duplicate/idempotency controls.
19. Portfolio exposure/margin limits.
20. Immutable audit trail.

### P1 — Correct AI decision support

21. Shared regime indicators.
22. Evidence-based confidence.
23. Correct Kelly/risk recommendation.
24. Deployment recommendation tied to OOS evidence.

### P2 — Reliability and scale

25. Telegram retry/queue/reconciliation.
26. Incremental signal evaluation.
27. Durable job management.
28. CI/regression/security test suite.

### P3 — Live execution

Not started and should remain disabled until the P0/P1 trustworthiness requirements pass.

---

## 7. Source of Truth

For the complete audit, implementation matrix, readiness criteria, and KEEP / CHANGE / REWRITE / ADD / REMOVE decisions, see **`PROJECT_STATUS_AUDIT.md`**.

**Current project verdict: strong development foundation, but NOT live-trading-ready.**
