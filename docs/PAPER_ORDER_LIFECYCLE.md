# Paper Order Lifecycle — Design and Acceptance Criteria

**Status:** Ledger, engine integration, authenticated API, dashboard order history, and disposable-MariaDB concurrency/crash tests are merged in PRs #243, #245, #247, #248, and #250. Manual browser/accessibility checks and populated-database upgrade verification remain.  
**Scope:** Paper/shadow execution only. This document does not authorize exchange orders or live trading.

## 1. Why this phase exists

The position engine records durable order intent before a simulated fill. PR #245 connects the signal loop to `paper_orders`, recovers pending orders after restarts, and atomically creates a paper position with the `PENDING -> FILLED` transition. PR #247 adds authenticated order history and pending-only cancellation; PR #248 exposes the state and cancellation action in the dashboard. End-to-end crash/concurrency and manual accessibility validation remain.

PR #239 also corrected a timing defect: a signal is evaluated on a fully closed candle, and a simulated entry is only permitted on the immediately following, currently forming candle. Missing, stale, future-dated, or gapped candle data must not cause an entry to be backfilled at a historical open.

## 2. State model

`paper_orders.status` should use these states:

- `PENDING`: signal accepted for evaluation at its intended next-candle open; no position has been opened yet.
- `FILLED`: simulated fill completed and linked to a `paper_positions` row.
- `EXPIRED`: intended entry candle was missed, stale, or unavailable before a valid fill.
- `CANCELLED`: an authorized user cancelled an order while it was pending.
- `REJECTED`: a risk, ML, or validation gate rejected the order before a fill.

Allowed transitions:

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> FILLED
    PENDING --> EXPIRED
    PENDING --> CANCELLED
    PENDING --> REJECTED
    FILLED --> [*]
    EXPIRED --> [*]
    CANCELLED --> [*]
    REJECTED --> [*]
```

Terminal states are immutable. Retrying a poll must never resurrect a terminal order or fill the same order twice.

## 3. Timing and signal rules

1. Build signal conditions from fully closed candles only.
2. Identify the signal candle by `signal_open_time`; identify the intended entry candle by `entry_time`.
3. Persist a `PENDING` order with a stable idempotency key derived from strategy configuration and signal time.
4. Fill only when the current raw candle has `is_closed=0`, its open time exactly equals the order's intended entry time, and the engine observes it within `PAPER_ENTRY_MAX_DELAY_SECONDS` (default 120 seconds) of the candle open. The delay window is capped at the timeframe interval.
5. If the intended candle is already closed, the configured entry-delay window is missed, a later candle is active, or the data is missing/gapped/stale/future-dated, transition to `EXPIRED`. Never backfill at a past candle open.
6. Evaluate ML and risk gates fail-closed before the fill. An active or unverifiable emergency stop, unknown exposure, or failed required ML filter must never produce a fill.
7. Do not use the forming candle's incomplete indicators to make a signal. Its raw open is execution data only; signal features remain from the prior closed candle.

## 4. Persistence and concurrency invariants

PR #243 adds migration `016_paper_orders` and the initial `paper.orders` service; PR #245 wires order filling to the engine using a row lock and a single transaction for position insertion plus the `FILLED` transition. The schema includes, at minimum:

- order ID and strategy identity: symbol, market, timeframe, strategy name;
- signal candle time and intended entry candle time;
- direction, requested entry, stop, target, and ATR-at-signal;
- optional ML model ID/probability;
- state, state reason, created/updated timestamps;
- fill timestamp/price and linked paper position ID;
- a unique idempotency key for the same strategy configuration and signal candle;
- indexes for pending-order lookup and recent order history.

Use compare-and-set transitions (`UPDATE ... WHERE id=? AND status='PENDING'`) so concurrent pollers cannot fill or cancel the same order twice. PR #245 locks the pending order and performs the position insert plus the `FILLED` transition on the same connection/transaction. The entry window and shared emergency stop are rechecked before filling. Automated tests cover the transaction boundary; concurrent-worker and crash-recovery behavior still require MariaDB integration validation.

## 5. API and dashboard behavior

- `GET /api/paper/orders` lists recent orders with an optional state filter; authenticated viewers may read history.
- `POST /api/paper/orders/{order_id}/cancel` is limited to trader/admin roles and can cancel only a `PENDING` order. Terminal/missing orders return a conflict and remain unchanged.
- Order responses expose state and reason but never database credentials, tokens, or secrets.
- The dashboard shows order state separately from open positions and closed trades, with status filtering and pending-only cancellation controls. A pending order is never presented as an open position.
- Every path remains paper-only. No exchange order endpoint or exchange credentials are added.

## 6. Required regression and integration tests

- A closed-candle signal creates one pending order with the correct signal/entry timestamps.
- Duplicate polls and concurrent workers cannot create duplicate orders or positions.
- A valid current next candle fills once and links exactly one position.
- A missed, stale, future-dated, or gapped entry candle expires rather than backfilling.
- Cancel succeeds only from `PENDING`; repeated cancel/fill attempts cannot mutate terminal states.
- Circuit-breaker, exposure, and required ML failures reject/block fills.
- A crash between order creation and fill is recoverable; a crash after position creation cannot leave an indefinitely pending order.
- Migration succeeds on a fresh database and adopts a populated legacy-style database without changing historical OHLCV rows; verify separately against a representative deployed schema without rewriting historical positions/trades.
- Route authorization tests verify anonymous users cannot list/cancel and viewers cannot mutate.

## 7. Implementation phases

1. **Timing guard (merged PRs #239 and #241):** use the immediate next open candle and skip stale/missing/late data.
2. **Durable ledger and transition service (merged PR #243):** migration `016_paper_orders`, validated/idempotent order creation, bounded listing, and guarded terminal transitions.
3. **Engine integration (merged PR #245):** eligible signals persist as pending orders; pending orders recover after restart; valid fills atomically create a position and transition to `FILLED`; missed windows expire; the entry deadline and shared emergency stop are rechecked.
4. **API and dashboard (merged PRs #247 and #248):** authenticated order history, trader/admin pending-only cancellation, status filter, and a separate dashboard ledger with safely escaped values.
5. **Release verification (still open):** validate migrations, concurrent workers and crash windows against MariaDB; run browser, keyboard, screen-reader, responsive, and forced-colors checks; verify deployed documentation only if deployment is explicitly requested.

The execution-lifecycle phase is implemented in code and has automated disposable-MariaDB concurrency/crash coverage, but it is not release-verified until phase 5 is complete. CI does not replace manual browser/accessibility checks or validation against a representative populated database. No live exchange execution is included.
