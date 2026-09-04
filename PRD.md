# Crypto Signal Bot — Product Requirements Document

**Status:** In progress — data pipeline, feature engine, and backtesting
framework are complete and heavily used. **Two strategies have now
passed holdout validation** (see [Current Status](#current-status)) — i wan
`confluence_ensemble_v1` (ETH spot 1d) and `pairs_ratio_v1` (BNB futures
1d, the stronger of the two). This is treated as a reasonable point to
shift focus from "find a strategy" to Phase B (paper trading) rather than
continuing to search for more candidates — see §9 for why.

**Audience:** This document is written so another engineer or AI agent
can pick up this project — continue backtesting, add strategies, build
the remaining phases, or modify the architecture — without needing the
original multi-week conversation history that produced it.

---

## 1. Vision

Build a systematic crypto trading signal system for the top cryptocurrencies
on Binance that:

1. Collects real-time and historical price/volume/derivatives data
2. Computes technical indicators and derived features from that data
3. Backtests trading strategies rigorously against historical data, with
   real discipline against overfitting (train/holdout separation,
   walk-forward validation, realistic transaction costs)
4. Once a strategy is genuinely validated (not just "looks good on one
   backtest"), paper-trades it live to confirm real-time behavior matches
   backtested expectations
5. Sends trading signals to Telegram
6. (Longer-term, explicitly deferred) Offers a web UI where a user can
   pick any symbol/timeframe, choose from a library of strategies
   (mimicking institutional/professional approaches), run backtests,
   compare results, and eventually take a validated strategy live —
   either as alerts or full auto-trading

**Core operating principle established early and never relaxed:** no
strategy goes live, or even gets seriously considered, without surviving
a disciplined backtest process. A strategy that "looks profitable" on a
single backtest run is not sufficient evidence — see
[§6 Engineering Principles](#6-engineering-principles-read-this-before-modifying-anything)
for why, and what standard to hold new work to.

---

## 2. Non-Goals (explicitly out of scope for now)

- **Web UI** — flagged early as a future phase; the codebase is
  structured (registry pattern, centralized params) to make this easier
  later, but no UI work has been done.
- **Auto-trading (order execution)** — not started. Requires a paper
  trading phase first (see [§8 Roadmap](#8-roadmap--remaining-work)).
- **More than 3 symbols with real data** — only `BTCUSDT`, `ETHUSDT`,
  `BNBUSDT` have been backfilled. 7 more symbols are configured
  (`SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, TONUSDT`)
  but have zero data — a known, deliberate gap (kept scope small while
  building out the backtesting framework).
- **Order book / market depth data** — not collected.
- **Machine learning models** — everything so far is rule-based
  (explicit, auditable entry/exit conditions), not ML-fitted.

---

## 3. Tech Stack

- **Language:** Python 3.8+
- **Database:** MySQL / MariaDB
- **Key Python libraries:** `pandas`, `numpy`, `SQLAlchemy` (for pandas
  DB reads — see §6 for why plain `mysql-connector` reads were replaced),
  `mysql-connector-python` (writes and most reads), `websockets`,
  `requests`
- **Data source:** Binance public REST + WebSocket APIs, both Spot and
  USDⓈ-M Futures
- **No external ML/backtesting framework** — the backtesting engine is
  custom-built (`backtest/simulate.py`), not `backtrader`/`freqtrade`/etc.
  (a deliberate choice made early to keep full control over exact
  no-lookahead semantics; see §6)

---

## 4. Architecture Overview

```
┌─────────────────────┐     ┌──────────────────────┐
│   Collectors         │────▶│   MySQL               │
│  (REST backfill +     │     │  ohlcv, funding_rate,  │
│   live WebSocket)      │     │  open_interest,        │
└─────────────────────┘     │  liquidations           │
                              └───────────┬───────────┘
                                          │
                              ┌───────────▼───────────┐
                              │  Feature Engine         │
                              │  (features/)             │
                              │  RSI, MACD, ATR, S/R,     │
                              │  Fibonacci, volume ratio  │
                              └───────────┬───────────┘
                                          │
                              ┌───────────▼───────────┐
                              │  features table          │
                              └───────────┬───────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │                                             │
        ┌───────────▼───────────┐                    ┌───────────▼───────────┐
        │  Backtester (backtest/) │                    │  [NOT BUILT YET]        │
        │  - 11 strategies ×       │                    │  Live signal engine /   │
        │    inverse = 22          │                    │  paper trading          │
        │  - shared simulate()     │                    │  (Step 6)                │
        │    engine                │                    └───────────┬───────────┘
        │  - train/holdout split   │                                │
        │  - walk-forward          │                    ┌───────────▼───────────┐
        │  - parameter optimizer   │                    │  [NOT BUILT YET]        │
        │  - $ PnL simulation       │                    │  Telegram bot           │
        └─────────────────────────┘                    │  (Step 7)                │
                                                          └─────────────────────────┘
```

**Critical design invariant:** the feature engine and the strategy logic
in `backtest/strategies/` are meant to be the *same code* the eventual
live signal engine uses. This is why every strategy is a pure function
`run(df, params) -> list[trade]` operating on a DataFrame that could
equally be historical or freshly-updated live data — there is no
backtest-only shortcut logic anywhere in the strategy modules. Preserve
this when building the live engine: import and call the existing
`STRATEGIES` registry, do not reimplement strategy logic.

---

## 5. Data Model

### 5.1 Core tables (see `db/schema.sql` for exact DDL)

| Table | Purpose | Notes |
|---|---|---|
| `ohlcv` | Candle data, spot + futures, all timeframes | PK `(symbol, market, timeframe, open_time)`. `open_time`/`close_time` in ms epoch; `open_time_wib`/`close_time_wib` generated columns for human-readable Asia/Jakarta time (see §6.3 for why these are computed the way they are) |
| `funding_rate` | Futures funding settlements | Full history available via REST (`collectors/backfill_funding.py`). `mark_price` is nullable — Binance sometimes omits it |
| `open_interest` | Futures OI snapshots | **Binance only retains ~30 days of history** via public API — this is a hard platform limitation, not a bug. Live poller (`collectors/oi_poller.py`) is the only way to accumulate more over time |
| `liquidations` | Futures liquidation events | **No historical REST endpoint exists at all** — only live WebSocket (`collectors/ws_liquidations.py`). Cannot be backtested on history; only usable once enough live data has accumulated |
| `features` | Computed indicators, one row per `ohlcv` row | RSI, MACD, ATR, volume ratio, rolling support/resistance, Fibonacci levels. Computed by `features/build_features.py` from `features/indicators.py`'s pure functions |
| `backtest_runs` | One row per backtest invocation | Includes `data_segment` (`train`/`holdout`) and `params_json` (full reproducibility) |
| `backtest_trades` | Every simulated trade from every run | `direction`, entry/exit price & time, `stop_loss`, `take_profit`, `exit_reason`, `net_return_pct`, `r_multiple` |
| `collector_heartbeat` | Liveness tracking for live collectors | Used by `validate_step1.py` |

### 5.2 Key data-layer functions (`db/db.py`)

- `fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=False)`
  — the main data-fetch function backtesting uses. Joins `ohlcv` + `features`
  ; optionally joins `funding_rate` via `merge_asof(direction='backward')`
  (no lookahead — a candle only ever sees funding settlements that had
  already occurred).
- `get_sqlalchemy_engine()` — used specifically for pandas reads (see §6.2
  for why a separate engine from the main `mysql-connector` pool is
  necessary).
- Various `upsert_*` functions — all batched (2,000 rows/chunk) to avoid
  exceeding `max_allowed_packet` on large datasets (see §6.4).

---

## 6. Engineering Principles (read this before modifying anything)

These aren't arbitrary style choices — each one exists because of a real
bug or a real methodological failure encountered during development.
Violating them will likely reintroduce a problem that was already paid
for once.

### 6.1 No lookahead, anywhere, ever

- A strategy's entry decision uses data up to and including the **current
  closed candle**; execution always happens at the **next candle's open**.
  This is enforced structurally in the shared `simulate()` engine
  (`backtest/simulate.py`) — every strategy delegates trade execution to
  it rather than implementing its own loop.
- Rolling indicators (support/resistance, Fibonacci swings, breakout
  levels) use `.shift(1)` before `.rolling()` so the current bar is
  excluded from its own lookback window.
- Cross-timeframe joins (`trend_alignment_v1`'s daily-trend filter,
  `funding_extreme_reversal_v1`'s funding join) use `pd.merge_asof(...,
  direction='backward')` with an explicit "availability" timestamp — e.g.
  a daily candle only becomes usable starting the *next* calendar day
  after it closes, never the same day.
- Every new strategy that was added was tested with an explicit lookahead
  check: compute results once, mutate *future* bars, recompute, and
  assert the earlier results are byte-identical. Do this for any new
  strategy.

### 6.2 Pandas + `mysql-connector` pooled connections don't mix reliably

`pd.read_sql()` on a raw `mysql-connector` pooled connection object
intermittently threw `MySQL Connection not available` — a real,
reproduced bug, not a fluke. Fixed by using a dedicated SQLAlchemy engine
(`db.get_sqlalchemy_engine()`, with `pool_pre_ping=True`) for all pandas
reads, while keeping the plain `mysql-connector` pool for regular
inserts/updates. **Do not** revert pandas reads to the raw connector pool.

### 6.3 Timestamp handling: store ms epoch, never trust session timezone for display math

`FROM_UNIXTIME()` in MySQL depends on the session's active timezone,
which caused a real, shipped bug (WIB timestamps double-offset by 7 hours
when the server's system timezone was already Asia/Jakarta). Fixed by
computing WIB display columns via pure calendar arithmetic —
`DATE_ADD('1970-01-01 00:00:00', INTERVAL ((epoch_ms DIV 1000) + 25200) SECOND)`
— which is timezone-independent and gives the same correct answer
regardless of server/session/client timezone settings. All raw storage
stays in millisecond epoch integers; only display columns use this
pattern. Follow this convention for any new timestamp display column.

### 6.4 Batch large inserts

A single `executemany()` inserting tens of thousands of rows (e.g. a full
2-year 15-minute feature backfill) can exceed `max_allowed_packet` and
silently fail the whole insert. All bulk upsert functions in `db/db.py`
batch in chunks of 2,000 rows with a commit per chunk.

### 6.5 Train/holdout discipline — the single most important process rule

- `HOLDOUT_FRACTION = 0.2` (in `config.py`): the most recent 20% of any
  symbol/timeframe's history is reserved.
- `run_backtest.py`, `walk_forward.py`, and `backtest/optimize.py`
  **only ever operate on the train (oldest 80%) portion**, by design —
  see `backtest/holdout.py`'s `compute_holdout_cutoff`/`split_by_cutoff`.
- `backtest/run_holdout_check.py` is the **only** script that touches
  holdout data. It requires an explicit `--confirm` flag and is meant to
  be run **once**, after a strategy/parameter combination has been fully
  decided on — not as another exploratory tool. Checking holdout,
  disliking the result, then trying another combination and checking
  again defeats the entire purpose (multiple-comparisons / p-hacking).
- **Empirical proof this matters**: multiple strategies that looked
  strongly profitable on train data (some after parameter optimization
  making them look *even better*) went on to fail holdout, in a couple of
  cases performing *worse* after optimization than the un-optimized
  defaults did on the same holdout data — a textbook overfitting
  signature. This happened repeatedly enough across this project that it
  should be treated as the expected outcome, not a surprise, for any new
  strategy.
- When testing a specific optimized parameter combination's stability
  before spending a holdout check, use `--override KEY=VALUE` (supported
  by both `walk_forward.py` and `run_holdout_check.py`,
  parsed by `backtest/params.py`'s `parse_overrides()`).

### 6.6 Realistic costs are not optional

`BACKTEST_FEE_PCT` and `BACKTEST_SLIPPAGE_PCT` (in `config.py`) are
applied on every trade. This is not a minor detail — it was the single
biggest driver of the project's most consistent finding: **15m and 1h
timeframes are structurally disadvantaged** because ATR-based stop
distances are often comparable in size to round-trip transaction costs at
those timeframes, meaning costs alone can erase most of a strategy's
apparent edge before its actual signal quality even matters. Any new
strategy or parameter search should expect this pattern to hold at fast
timeframes and should not be surprised by it.

### 6.7 Registry pattern for strategies — extend, don't special-case

Adding a new strategy means creating a module in `backtest/strategies/`
with a `NAME` constant, a `run(df, params) -> list[trade]` function, and
a `run_inverse(df, params) -> list[trade]` function (wherever the
original goes LONG, the inverse goes SHORT, and vice versa — reuses the
same `simulate()` engine by swapping which condition function is passed
as "long" vs "short"). Register it in `backtest/strategies/__init__.py`'s
`_MODULES` list — everything else (inverse auto-registration, the 4
driver scripts, the optimizer) picks it up automatically **except**:

- If the strategy needs extra joined data (a higher timeframe, a second
  symbol, funding rate), add its name to the relevant set in
  `backtest/strategies/__init__.py` (`FUNDING_STRATEGIES`,
  `TREND_ALIGNMENT_STRATEGIES`, `PAIRS_STRATEGIES`) and mirror the
  fetch/split logic already present in `run_backtest.py`,
  `walk_forward.py`, `run_holdout_check.py`, and `optimize.py` for the
  existing examples of each pattern. **This has been a real source of
  bugs twice** (a strategy's inverse not being recognized by the special-
  case check, and a strategy silently missing a required param default)
  — grep for the existing pattern name across all four scripts when
  adding a new cross-data strategy, don't assume one script's wiring is
  enough.
- If the strategy needs a config parameter not already in
  `backtest/params.py`'s `BASE_PARAMS`, add it there (imported from
  `config.py`) — a strategy silently crashing with a `KeyError` on a
  missing param has happened before (`pairs_ratio_v1` initially missing
  `ATR_PERIOD`).

### 6.8 Everything is tested against synthetic/hand-crafted data before being reported as working

Every strategy, every engine change (trailing stops, the equity curve
math, the double-pattern zone-tracking state machine), and every bug fix
in this project was verified with a small, targeted synthetic test before
being called "done" — not just "looks plausible." Several real bugs were
caught this way before ever reaching the user (a phantom same-bar entry
in `supply_demand_v1`'s zone tracking; a 500x units mismatch in
`funding_extreme_reversal_v1`'s threshold comparison). Continue this
practice for any new engine or strategy code.

---

## 7. Strategy Library (11 base strategies, 22 with inverses)

All strategies share `backtest/simulate.py`'s execution engine (entry at
next-bar open, ATR-based stop/target sizing, optional trailing stop,
fees/slippage applied, one position at a time). They differ only in entry
condition logic and how the stop/target is sized.

| # | `NAME` | File | Core idea |
|---|---|---|---|
| 1 | `confluence_reversal_v1` | `mean_reversion.py` | RSI extreme + price at support/resistance + volume + volatility + MACD momentum turning (fade the extreme) |
| 2 | `breakout_continuation_v1` | `breakout.py` | Price clears a rolling high/low by a minimum margin (own lookback, separate from the shared one) — trade *with* the break |
| 3 | `trend_ema_v1` | `trend_ema.py` | Fast/slow EMA crossover + MACD confirmation |
| 4 | `trend_alignment_v1` | `trend_alignment.py` | Same EMA crossover, but only when a higher timeframe's (daily) trend agrees |
| 5 | `fibonacci_retracement_v1` | `fibonacci_retracement.py` | EMA trend bias + pullback into the 61.8–78.6% retracement zone + reaction candle |
| 6 | `smc_liquidity_sweep_v1` | `smc_liquidity_sweep.py` | Wick sweeps beyond support/resistance then rejects back inside (stop-hunt reversal) |
| 7 | `supply_demand_v1` | `supply_demand.py` | Small-bodied "base" candle + strong "displacement" candle marks a zone; price retesting it (without breaking through) triggers entry |
| 8 | `double_pattern_v1` | `double_pattern.py` | Double top/bottom: two confirmed swing pivots within tolerance, entry on neckline break |
| 9 | `funding_extreme_reversal_v1` | `funding_extreme.py` | Extreme futures funding rate (crowded positioning) fades — works on spot price data using funding as a sentiment overlay |
| 10 | `confluence_ensemble_v1` | `confluence_ensemble.py` | Weighted combination of 6 components (RSI, trend, MACD, volume, S/R, funding) — trades only when enough agree |
| 11 | `pairs_ratio_v1` | `pairs_ratio.py` | Relative-value: z-score of the ratio between a symbol and a base leg (default `BTCUSDT`) reverting to its mean — market-neutral in spirit, needs a second symbol's data |

**Every strategy also has an `inverse_<name>` counterpart** (wherever the
original goes LONG, the inverse goes SHORT) — registered automatically,
useful for testing whether a strategy with a strong, statistically dense
*negative* edge might mean the opposite side has a real edge. (Empirical
finding so far: mostly not — see §9.)

---

## 8. Roadmap / Remaining Work

### Phase A — Strategy validation (largely complete)

Two candidates have passed holdout (see §9): `confluence_ensemble_v1`
(ETH spot 1d) and `pairs_ratio_v1` (BNB futures 1d, the stronger of the
two). Further searching is deliberately being deprioritized in favor of
Phase B — see §9's closing note on why continuing to search risks the
same overfitting trap the holdout process exists to prevent. If picking
this back up, reasonable next questions are in §12, not "test more
strategies by default."

### Phase B — Live signal engine / shadow (paper) trading — NEXT UP, NOT STARTED

This is the next major build. Requirements:

- A process that runs continuously (or is triggered whenever
  `run_collectors.py` closes a new candle) and:
  1. Computes/updates `features` for the new candle (extend
     `features/build_features.py`'s logic to run incrementally rather
     than always recomputing full history — noted as a known
     optimization not yet built, since it wasn't needed at backtest scale)
  2. Calls the chosen strategy's `run()` (or a live-specific evaluation
     of just the latest bar's condition — needs care to replicate
     `simulate()`'s exact entry-at-next-open semantics in a live,
     streaming context) using the **same strategy code**, no
     reimplementation
  3. Tracks open positions per symbol/strategy (avoid duplicate signals
     while a position is conceptually "open")
  4. Logs every hypothetical trade — entry, stop, target, eventual exit —
     to a new table (something like `paper_trades`, mirroring
     `backtest_trades`'s schema)
- **Purpose**: catch things a backtest cannot — real data latency, subtle
  timing bugs, whether live indicator values actually match what the
  backtest assumed. Compare live-forward performance against the
  strategy's backtested expectancy before trusting it further.
- Suggested minimum bar before connecting to Telegram: some period of
  paper-trading (weeks, not days) with performance broadly consistent
  with backtested expectations.

### Phase C — Telegram integration — NOT STARTED

Comparatively simple once Phase B works: format and send an alert
(symbol, direction, entry, stop, target, strategy name) to a Telegram
chat/channel whenever the paper-trading engine opens or closes a
position. A Telegram bot token and chat ID would need to be provisioned
(`.env` already has placeholder fields:
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, unused so far).

### Phase D — Ongoing re-validation — NOT STARTED

- Periodically re-run holdout checks / walk-forward as more live data
  accumulates, since a validated strategy can decay (regime change).
- Consider building automated drift detection: compare live-forward
  R-multiples against backtested expectancy, alert if diverging
  significantly.

### Phase E — Scale up data (partially blocked / deferred)

- Backfill the remaining 7 symbols (`SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT,
  AVAXUSDT, LINKUSDT, TONUSDT`) — `collectors/backfill_klines.py` already
  supports this, just hasn't been run for them.
- `open_interest` and `liquidations` history will only become usable for
  backtesting as the live collectors accumulate more of it over time (see
  §5.1's platform limitations) — revisit in a few months.

### Phase F — Web UI — explicitly deferred, not started

Symbol/strategy/parameter picker → run backtest → compare results →
(eventually) manage live signals. The codebase's registry pattern
(`STRATEGIES` dict) and centralized `BASE_PARAMS` were deliberately kept
as clean, introspectable single sources of truth specifically so a future
UI layer could enumerate available strategies/parameters without
restructuring backend logic — a UI would call into the existing
`backtest/run_backtest.py`/`optimize.py` functions rather than needing
new backend logic.

---

## 9. Current Status — Research Findings (as of this document)

**Read this before assuming any existing strategy is "ready."** None are,
yet.

- **Two strategies have now passed an actual holdout check** — the bar
  this project has held to throughout, and the reason to trust these two
  more than anything else tested:

  1. `confluence_ensemble_v1` on `ETHUSDT` spot `1d` — train +0.137R (38
     trades), a walk-forward pattern that improved into its most recent,
     largest fold, holdout +0.494R (9 trades, 44.4% win, PF=1.85). Thin
     sample; genuinely encouraging but not proof on its own.

  2. `pairs_ratio_v1` on `BNBUSDT` futures `1d`
     (`PAIRS_ZSCORE_LOOKBACK_BARS=50`, `PAIRS_ZSCORE_ENTRY_THRESHOLD=1.5`,
     `USE_TRAILING_STOP=True`, `TRAIL_ACTIVATION_R=1.0`,
     `TRAIL_DISTANCE_ATR_MULT=2.0`) — **the strongest result in the
     project.** Walk-forward: 4/4 folds profitable, and unlike every
     failed candidate, the trend *accelerated* into the most recent fold
     (+0.403R → +0.100R → +0.265R → +0.946R) rather than weakening.
     Holdout: +0.711R (16 trades, 43.75% win, PF=2.10, max_dd=6.67R) —
     nearly double the trade count and a better profit factor than
     candidate #1. The corresponding `ETHUSDT` futures 1d pairs test was
     walk-forward checked and correctly **not** pursued to holdout — it
     showed the well-established red-flag shape (strong middle folds,
     collapsing to -0.745R with an 8.7% win rate in the most recent fold),
     the same pattern that predicted real failures earlier in the project.

  Two independently-different signal sources (a multi-signal confluence
  score on spot price/indicators, and a relative-value ratio trade on
  futures) both surviving holdout is more convincing than either alone.
  Still: 9 and 16 trades respectively are real evidence, not proof — both
  should be monitored, not treated as guaranteed to keep working.

- **Search phase considered reasonably complete for now.** After 11
  strategies, extensive parameter optimization, and repeated holdout
  discipline, two validated candidates is treated as a sensible point to
  shift effort from "find a strategy" to Phase B (paper trading) rather
  than continuing to search — further searching risks reintroducing the
  exact overfitting/multiple-comparisons problem this whole process has
  been built to guard against. See §8 Phase A/B.

- **15m and 1h are decisively, consistently unprofitable across every
  single strategy tested** — large sample sizes (hundreds to thousands of
  trades), clearly negative expectancy. This is not noise; treat this as
  an established finding, not something to re-test from scratch for a
  new strategy without a specific reason to expect it'll be different.
- **Failed holdout (informative negative results, not wasted effort)**:
  `trend_ema_v1` (BTC 4h, BNB 1d — both flipped negative), `supply_demand_v1`
  (BNB 4h — flipped decisively negative, and *worse* after optimization),
  `smc_liquidity_sweep_v1` (BNB 1d — weakened from train but stayed
  marginally positive on only 9 holdout trades, inconclusive).
- **`funding_extreme_reversal_v1`**: promising on train, but its holdout
  window happened to have zero occurrences of extreme funding at all
  (confirmed via `check_funding_range.py`, not a bug) — genuinely
  untested, not proven or disproven. Revisit if/when the holdout window
  naturally shifts to include a more volatile period, or accept this
  signal is regime-dependent (dormant in calm markets).
- **Inversion experiment result**: testing every strategy's opposite-
  direction counterpart mostly produced similar-or-worse results, and
  notably *destroyed* the edge of strategies that already had one
  (e.g. inverting `trend_ema_v1` on `BNBUSDT` 1d turned +0.256R into
  -0.433R). Interpreted as: the market isn't predictably "backwards"
  relative to these signals (which would itself be exploitable) — closer
  to these signals mostly not carrying real directional information, a
  result consistent with reasonably efficient markets. Not pursuing
  further inversion work without a specific new reason to.

**Bottom line for whoever picks this up next**: the backtesting
infrastructure is solid and heavily battle-tested; the search for a
strategy worth deploying is still open. Continue from Phase A in the
roadmap above — don't skip straight to building the live engine around an
unvalidated strategy.

---

## 10. File/Module Map

```
config.py                    Single source of truth for all constants/params
db/
  schema.sql                 Full DDL
  migrate_*.sql               Migrations applied on top of schema.sql over time
  db.py                       Connection pooling, fetch/upsert functions
collectors/
  backfill_klines.py          Historical OHLCV backfill (spot + futures)
  backfill_funding.py         Historical funding rate backfill (full history)
  backfill_oi.py              Historical OI backfill (~30 day limit, platform constraint)
  ws_kline.py                 Live OHLCV via combined WebSocket streams
  ws_funding.py                Live funding rate stream
  ws_liquidations.py           Live liquidation event stream
  oi_poller.py                 Live OI polling (no WS stream exists for OI)
run_collectors.py             Orchestrates all live collectors
validate_step1.py             Data pipeline QA (gaps, heartbeats, freshness)
features/
  indicators.py                Pure indicator functions (RSI, MACD, ATR, etc.)
  build_features.py            Computes features table from ohlcv
validate_step2.py             Feature engine QA
backtest/
  simulate.py                  Shared trade execution engine (THE core piece)
  strategies/                  One module per strategy + __init__.py registry
  params.py                     BASE_PARAMS single source of truth + parse_overrides()
  holdout.py                    Train/holdout split utilities
  metrics.py                    Win rate / expectancy / profit factor / drawdown
  equity.py                     $ PnL / equity curve simulation
  run_backtest.py               Runs all strategies against train data
  walk_forward.py               Temporal-stability check (train data, N folds)
  optimize.py                   Grid-search parameter optimizer (train data)
  run_holdout_check.py          THE ONLY script touching holdout data
  report.py                     CLI reporting (detail, --compare, --top-trades)
  pnl_report.py                 Dedicated $ PnL report for one strategy/combo
check_futures_access.py       Standalone REST connectivity diagnostic
check_funding_range.py        Diagnostic: actual funding rate range in a time window
README.md                     Turn-by-turn build log (chronological, very detailed)
PRD.md                        This document
```

---

## 11. Setup

See `README.md` for exhaustive step-by-step instructions accumulated
throughout development (it reads as a chronological log — this PRD is
the synthesized, structured counterpart). Summary:

1. `pip install -r requirements.txt`
2. Configure `.env` from `.env.example` (MySQL credentials; `ENABLE_FUTURES`
   defaults to `false` — futures WebSocket was found geo-restricted for
   the original deployment environment, though REST access works fine)
3. `python3 -c "from db.db import init_schema; init_schema()"` — apply
   schema (and any `db/migrate_*.sql` files if working from an existing
   database)
4. `python3 -m collectors.backfill_klines` then
   `python3 -m features.build_features` — historical data + indicators
5. `python3 run_collectors.py` — start live collection (leave running)
6. `python3 -m backtest.run_backtest` then `python3 -m backtest.report --compare`
   — run and view backtests

---

## 12. Open Questions for Whoever Continues This

1. **Primary open question at time of writing**: start building Phase B
   (paper trading engine) around the two validated candidates
   (`confluence_ensemble_v1` on ETH spot 1d, `pairs_ratio_v1` on BNB
   futures 1d), or spend more effort strengthening evidence for them
   first (more history, more symbols)? Leaning toward starting Phase B —
   paper trading is itself a validation mechanism, and further backtest
   searching has diminishing (and increasingly risky) returns at this
   point.
2. Should both validated candidates be paper-traded simultaneously (as
   independent signal sources), or prioritize one first?
3. Should the project scale to more symbols now (widening the search,
   more independent tests of existing strategies) or focus depth-first on
   deploying what's validated on the current 3 symbols?
4. Given `open_interest`/`liquidations` can't be meaningfully backtested
   yet (platform history limits), is it worth building the live paper-
   trading engine now partly *in order to* start accumulating enough of
   that data to backtest it properly in a few months?
5. Is the mean-reversion/trend/pattern strategy family fundamentally
   under-powered on BTC/ETH/BNB (large, efficiently-arbitraged markets),
   while genuine information-edge approaches (funding, relative-value)
   fare better? Both validated candidates support this reading
   (`confluence_ensemble_v1` uses funding as one of six components;
   `pairs_ratio_v1` is pure relative-value) — worth keeping in mind if
   more strategy work happens later.
