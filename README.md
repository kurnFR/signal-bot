# Crypto Signal Bot

Progress: Step 1 (data pipeline) ✅ — Step 2 (indicators) ✅ — Step 3 (backtester, multi-strategy) ✅ — Step 4 (walk-forward validation) — see below.

**Flagged for later (after Step 7):** a web UI where you can pick any
symbol/timeframe, run backtests across a library of pro/institutional-style
strategies, then take the best-performing one live (signals or full auto-
trading). Not built yet — but design choices along the way (strategies as
pluggable modules in a registry, the backtester callable as a function not
just a CLI, params always in `config.py` rather than hardcoded) are made
with that in mind so it isn't a rewrite later.

## What this step builds

- MySQL schema: `ohlcv`, `funding_rate`, `open_interest`, `liquidations`, `collector_heartbeat`
- Historical backfill (default 2 years) for 10 symbols x 6 timeframes x 2 markets
- Real-time collectors:
  - Kline (spot + futures, all timeframes) via combined WS stream
  - Funding rate / mark price via `!markPrice@arr`
  - Liquidations via `!forceOrder@arr`
  - Open interest via REST polling (no WS stream exists for OI)
- A validation script to confirm everything is working before you move to Step 2

## Applying updates to an already-running setup

If you already ran `backfill_klines.py` / `run_collectors.py` once and are
pulling in these fixes, do this:

1. **Stop `run_collectors.py`** (Ctrl+C).
2. **Add the WIB (Asia/Jakarta) timestamp columns** to your existing tables:
   ```bash
   mysql -u your_user -p < db/migrate_add_wib_columns.sql
   ```
   This adds `open_time_wib` / `close_time_wib` / `event_time_wib` generated
   columns (UTC+7, no DST math needed) to every table — your existing
   `open_time`/`close_time` ms-epoch columns are untouched, this just adds
   a human-readable view alongside them. Query example:
   ```sql
   SELECT symbol, open_time_wib, close_time_wib, open, high, low, close
   FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h'
   ORDER BY open_time DESC LIMIT 10;
   ```
3. **Futures WS (fstream.binance.com) may be geo-restricted** for your
   location (this is a known issue for some jurisdictions, unrelated to
   your code/config). If `run_collectors.py` shows `ws_funding`,
   `ws_kline_futures`, or `ws_liquidations` erroring repeatedly while
   `ws_kline_spot` stays healthy, leave `ENABLE_FUTURES=false` in `.env`
   (the default) — this cleanly skips those futures-only collectors
   instead of endlessly retrying a blocked connection. Only set it to
   `true` if you've separately confirmed futures WS works from your network.
4. **Restart collectors**: `python3 run_collectors.py`
5. **Re-run backfill** to patch any real gaps in your historical data —
   safe to re-run any time, it upserts:
   ```bash
   python3 -m collectors.backfill_klines --skip-schema
   ```
6. **Re-validate**: `python3 validate_step1.py` — it now prints the exact
   WIB timestamps around any detected gap, so you can tell whether it's a
   real (rare) exchange event or something re-running backfill will fix.

## Setup (fresh install)

```bash
cd crypto-signal-bot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your real MySQL credentials
```

Make sure MySQL is running and the user in `.env` has permission to
`CREATE DATABASE` (the schema script creates it if missing).

## Run order

**1. Backfill historical data** (run once, takes a while — 10 symbols x 6
timeframes x 2 markets x 2 years, respects Binance rate limits so it's not
instant):

```bash
python -m collectors.backfill_klines
```

Optional flags for testing on a smaller slice first (recommended before
committing to the full run):

```bash
python -m collectors.backfill_klines --symbol BTCUSDT --timeframe 1h --years 0.1
```

**2. Start real-time collectors** (leave running continuously):

```bash
python run_collectors.py
```

**3. After ~30-60 minutes of collectors running, validate:**

```bash
python validate_step1.py
```

This checks:
- Every symbol/timeframe/market has data with no gaps
- All collectors have a recent heartbeat (i.e., they're alive, not silently dead)
- Funding rate and open interest tables are being updated
- Liquidation table is receiving events (count will naturally be low/sporadic)

**Fix any `[MISSING]`, `[GAPS]`, `[STALE]`, or `[ERROR]` lines before telling
me you're ready for Step 2.** Common causes:
- `[MISSING]` on ohlcv → backfill didn't complete for that combo, re-run
  `backfill_klines.py` (it's safe to re-run, it upserts)
- `[GAPS]` → collector was down for a period; re-run backfill to patch,
  since it will overwrite/fill the same rows
- `[STALE]` heartbeat → that collector's WS connection likely died and
  isn't reconnecting; check the terminal logs for that collector's errors
- Empty liquidations table after an hour is *not* necessarily a bug —
  liquidations are event-driven and sporadic, especially in low volatility

## Notes on what's intentionally deferred to later steps

- **Raw aggTrades** are not stored (kline taker-buy-volume covers volume-delta
  needs). Can be added later if you want order-flow/footprint features.
- **Resampled timeframes**: not needed — every timeframe in `TIMEFRAMES` is
  pulled directly from Binance as an exact kline, not derived from 1m.
- **Indicators (RSI, MACD, ATR, Fibonacci, support/resistance)**: Step 2,
  computed from this raw data into a separate `features` table so backtest
  and live signal generation always share identical calculation code.

## When you're done

Run `validate_step1.py`, fix anything it flags, and let it show a clean
"STEP 1 VALIDATION: PASSED" — then tell me and we'll move to Step 2 (the
indicator/feature engine).

---

## Step 2: Indicator / Feature Engine

Computes RSI, MACD, ATR, volume ratio, rolling support/resistance, and
Fibonacci retracement levels from `ohlcv` into a new `features` table.

**This code (`features/indicators.py`) will be reused unmodified by the
backtester (Step 3) and the live signal engine (Step 6+)** — that's what
guarantees backtested performance actually predicts live behavior. Never
duplicate this math elsewhere.

### Run order

```bash
# 1. add the new `features` table (safe to re-run, only adds what's missing)
python3 -c "from db.db import init_schema; init_schema()"

# 2. compute indicators for all your configured symbols/timeframes
python3 -m features.build_features

# 3. validate
python3 validate_step2.py
```

Useful during testing:
```bash
python3 -m features.build_features --symbol BTCUSDT --timeframe 1h
```

### What "good" looks like

- No `[MISSING]`, `[BAD RSI]`, `[BAD ATR]`, `[BAD S/R]`, `[BAD FIB]`, or `[GAP]` lines
- RSI between 0-100, ATR non-negative, support ≤ resistance, fib levels monotonic
- Row count in `features` should be close to `ohlcv`'s row count minus the
  indicator warm-up period (the first ~50 bars, since `HTF_S_R_LOOKBACK_BARS=50`
  is your longest lookback — those rows legitimately have NULL indicators
  because there isn't enough history yet, that's expected, not a bug)

### Notes

- `build_features.py` recomputes the **full** history every run (simple,
  correct, and fine at this scale — 3 symbols x 4 timeframes). Once you
  scale back up to 10 symbols x 6 timeframes, this should move to
  incremental computation (only new candles + enough lookback), but don't
  build that until it's actually slow.
- Fibonacci levels are anchored high-to-low over the same rolling window as
  support/resistance (`HTF_S_R_LOOKBACK_BARS`), so both describe the same
  recent price range consistently.
- All indicator math is hand-implemented with pandas (no pandas-ta/talib
  dependency) so it's transparent and stable across library versions —
  verified against synthetic data before delivery.

### When you're done

Run `validate_step2.py`, fix anything it flags, and let it show "STEP 2
VALIDATION: PASSED" — then tell me and we'll move to Step 3 (backtester).

---

## Step 3: Backtester

Tests whether a strategy has a real edge on your historical data — before
any signal ever reaches Telegram. Three strategies are implemented and
compared, since a single strategy showing no edge doesn't mean the whole
approach is broken (see the mean-reversion result discussion below):

| Strategy | Philosophy | Entry logic |
|---|---|---|
| `confluence_reversal_v1` | Mean-reversion / fade extremes | RSI oversold/overbought + price at support/resistance + volume + volatility + MACD momentum turning |
| `breakout_continuation_v1` | Trade with a decisive break | Close breaks beyond rolling resistance/support + volume + volatility |
| `trend_ema_v1` | Trade with the trend | Fast/slow EMA crossover + MACD confirmation |

**`backtest/simulate.py` is the shared execution engine** (SL/TP/timeout
handling, no-lookahead entry timing, fee/slippage modeling) used by all
three — each strategy only supplies its own entry conditions and stop/target
sizing. This is also **the same engine the live signal engine (Step 6+)
will use**, so backtest results actually predict live behavior.

### Run order

```bash
# 1. add the backtest_runs / backtest_trades tables
python3 -c "from db.db import init_schema; init_schema()"

# 2. run all three strategies against all configured symbols/timeframes
python3 -m backtest.run_backtest

# 3. view results
python3 -m backtest.report              # full detail, every strategy
python3 -m backtest.report --compare    # compact side-by-side comparison table
```

Useful during testing:
```bash
python3 -m backtest.run_backtest --symbol BTCUSDT --timeframe 1h
python3 -m backtest.run_backtest --strategy breakout_continuation_v1
python3 -m backtest.report --symbol BTCUSDT --timeframe 1h --compare
```

### How to read the report

- **Win rate**: % of trades that closed with a positive R-multiple
- **Expectancy (R per trade)**: average result per trade, in units of risk
  (R=1 means "won an amount equal to what was risked"). This is the single
  most important number — positive expectancy over enough trades is what
  "having an edge" actually means. A high win rate with negative expectancy
  is still a losing strategy if losses are bigger than wins.
- **Profit factor**: gross winning R ÷ gross losing R. Above 1 = profitable
  in aggregate; below 1 = not, regardless of win rate.
- **Max drawdown (R)**: worst peak-to-trough dip in the cumulative R-multiple
  curve — gives a sense of how much pain the strategy would put you through
  even if it's net profitable.
- **Exit reason breakdown**: how many trades hit SL vs TP vs timed out. If
  almost everything times out, the strategy rarely reaches its target — a
  sign the RR ratio or hold period may be mismatched to this market/timeframe.

**A report showing 0 trades, or a small handful, is a valid and useful
result** — it means these conditions rarely or never occur together in your
data, which is itself information. Don't be alarmed by it.

### What the first backtest (mean-reversion only) found

`confluence_reversal_v1` showed **negative expectancy on every combo with a
meaningful trade count** (15m/1h across all 3 symbols). Root cause: at these
timeframes, ATR (the risk unit) is only ~0.2-0.3% of price for something
like BTC — comparable in size to the assumed round-trip trading cost
(~0.3%), so fees/slippage eat a large fraction of every trade's risk unit
before the signal's actual quality is even considered. That's why SL-hit
trades averaged worse than -1R (should be ~-1R exactly on a clean stop) and
TP-hit trades averaged well under the 2R target. This is a genuine,
correctly-modeled result, not a bug — and it's exactly why realistic costs
were included in the engine from the start.

Rather than only tuning that one strategy's parameters, `breakout_continuation_v1`
and `trend_ema_v1` were added to test fundamentally different entry
philosophies (trading *with* momentum/structure rather than fading extremes)
before deciding where to focus Step 4's tuning effort.

### What was verified before shipping

Before handing this off, I ran the simulation engine against hand-crafted
synthetic data and confirmed:
- A signal decided on bar *i* executes at bar *i+1*'s open, never bar *i*'s
  own close (no lookahead)
- Mutating *future* bars never changes the outcome of an *earlier* trade
  (a direct test against lookahead bias)
- Stop-loss/take-profit levels and which one triggers first are computed
  correctly
- Win rate, expectancy, profit factor, and max drawdown all compute correctly
  against a known trade list
- After extracting the shared engine, `confluence_reversal_v1` produces the
  **exact same trade** as the original single-strategy version (regression
  test — the refactor didn't silently change behavior)
- `breakout_continuation_v1` fires on a genuine break and doesn't re-fire
  repeatedly while price simply stays beyond an already-broken level
- `trend_ema_v1` correctly detects an EMA crossover and enters in the
  trend's direction

### Important caveats before trusting any result

- **These are backtests on fixed parameter sets — not yet validated
  out-of-sample.** A good-looking result here could still be overfit to
  this specific 2-year window. That's what Step 4 (walk-forward validation)
  exists to check — don't treat Step 3's numbers as final for whichever
  strategy looks best.
- Only 3 symbols / limited timeframes are in scope right now (by your
  choice, to move faster) — results won't yet reflect your original "top
  10 crypto" target.
- Fees/slippage are modeled but are estimates (`BACKTEST_FEE_PCT=0.1%`,
  `BACKTEST_SLIPPAGE_PCT=0.05%` per side) — adjust in `config.py` to match
  your actual expected trading costs (spot vs futures, your fee tier, etc.)

### When you're done

Run `backtest.report --compare` for each symbol/timeframe, share the output
with me, and we'll decide together where to go next.

### Update: breakout was tightened after the first multi-strategy comparison

The first comparison showed `breakout_continuation_v1` firing 900+ times on
15m over 2 years — far too often to be genuine breakouts. Cause: it reused
the shared 50-bar support/resistance (same one `mean_reversion` fades
against), which is reactive enough that price marginally clears it
constantly in normal chop. Fixed by giving breakout its **own** longer
lookback (`BREAKOUT_LOOKBACK_BARS=100`) and a **minimum margin**
(`BREAKOUT_MIN_MARGIN_PCT=0.15%`) that price must clear beyond the level to
count as a genuine break — verified against synthetic data that a trivial
poke no longer fires but a real margin-clearing break still does.
`mean_reversion` is untouched by this (it still uses the shared 50-bar
level from `features`).

Re-run `python3 -m backtest.run_backtest` and `backtest.report --compare`
after pulling this update — trade counts on breakout should drop
substantially and be more meaningful.

---

---

## Update: scaling up for statistical confidence

`trend_ema_v1` on `1d` was the most consistent lead so far — positive
expectancy independently on all 3 symbols — but only 6-12 trades per
symbol, too thin to trust. To get a real sample size before validating
further, `config.py` now scales back up:

- **Symbols**: back to the full original 10 (`BTC, ETH, BNB, SOL, XRP,
  DOGE, ADA, AVAX, LINK, TON`)
- **History**: `BACKFILL_YEARS` 2 → 5 (BTC/ETH/BNB have traded far longer
  than 2 years on Binance, so this is safely within available history —
  roughly 2.5x's the `1d` sample size specifically)
- **Timeframes**: still `15m/1h/4h/1d` only — 1m/5m intentionally left out
  for now, since scaling to 10 symbols x 5 years already multiplies data
  volume substantially, and 1m/5m aren't needed for the current
  `trend_ema_v1`/`1d` investigation (1m alone would be ~2.6M candles per
  symbol over 5 years — a large, currently-unneeded backfill)

### What this means to re-run

This is a much bigger pull than before — roughly **2.3M candles** across
`ohlcv` (vs. the ~250K you had with 3 symbols/2 years), so expect the
backfill to take noticeably longer (rate-limited REST calls to Binance —
patience, not a bug if it takes a while). Re-run the full pipeline in order:

```bash
# 1. re-backfill with the new scope (10 symbols, 5 years, 4 timeframes)
python3 -m collectors.backfill_klines

# 2. rebuild indicators for the new data
python3 -m features.build_features

# 3. re-run all strategies across the full scope
python3 -m backtest.run_backtest

# 4. compare
python3 -m backtest.report --compare
```

`run_collectors.py` will also now subscribe to 40 live streams (10 symbols
x 4 timeframes) instead of 12 — well within Binance's per-connection
limits, no changes needed there, just restart it after backfill completes
so live data keeps flowing for the new symbols too.

---

---

---

---

## Update: inversion results + pairs/relative-value strategy (11th strategy)

### Inversion verdict: mostly worse, not a useful direction

Across all 3 inverted strategies × 12 combos, results were mostly similar
or worse than the originals — and notably, inverting strategies that
already had real edge (`trend_ema_v1` on BTC 4h and BNB 1d) **destroyed**
that edge rather than improving on it. This is informative: the market
isn't predictably backwards relative to these signals (which would be
exploitable) — closer to these signals mostly not carrying real
directional information, consistent with a reasonably efficient market.
Closed as a direction; not pursuing further.

### `pairs_ratio_v1`: relative-value / statistical arbitrage

Every strategy before this bet on one asset's **absolute** price
direction. This is structurally different: trades the **ratio** between a
symbol and a base leg (default `BTCUSDT`) reverting to its recent mean —
a classic institutional stat-arb technique, market-neutral in spirit
(doesn't care which way the whole market moves).

- Builds a synthetic ratio OHLC series (this symbol's O/H/L/C ÷ the base
  leg's, at each aligned bar) — the standard ratio-chart convention
- Entry: z-score of the ratio's close vs. its own rolling mean/std crosses
  `PAIRS_ZSCORE_ENTRY_THRESHOLD` (default 2.0 standard deviations)
- Stop/target reuse the already-tested Wilder ATR from `features/indicators.py`,
  applied to the ratio series — no new ATR implementation needed
- Needs a second symbol's data, wired into all 4 driver scripts using the
  exact same pattern already proven for `trend_alignment_v1`'s
  higher-timeframe data (own `PAIRS_STRATEGIES` set, split at the same
  train/holdout cutoff, no lookahead across folds)
- Skips a symbol paired against itself (e.g. `BTCUSDT` vs `BTCUSDT`)

**Scope note for later**: this backtests the ratio as if it were one
tradeable instrument. Actually trading this signal live means opening TWO
offsetting real positions (long one leg, short the other) — the live
signal engine (a later step) will need to generate both legs' orders, not
just one.

**Tested before shipping**: verified the ratio construction and z-score
correctly flag a deliberately constructed relative extreme, confirmed the
strategy fires in the correct direction (LONG when this symbol has
underperformed the base leg) and profits on the reversion, and confirmed
the self-pairing guard returns no trades.

### Usage

```bash
python3 -m backtest.run_backtest --strategy pairs_ratio_v1
python3 -m backtest.report --compare
python3 -m backtest.optimize --symbol ETHUSDT --timeframe 4h --strategy pairs_ratio_v1
```

Only `ETHUSDT` and `BNBUSDT` will generate trades against the default
`BTCUSDT` base leg — `BTCUSDT` itself is skipped (can't pair against itself).

---

## Update: first genuine survivor + inverse strategies for every candidate

### `confluence_ensemble_v1` on ETH 1d: the first real survivor

Train +0.137R (38 trades) → walk-forward showing a genuinely improving
trend across folds (unlike every prior candidate's erratic pattern) →
**holdout +0.494R (9 trades, 44.4% win, PF=1.85)**. This is the first
candidate in the whole project to be positive on data it never influenced,
coming from the one walk-forward result that looked structurally different
(most recent, largest fold was also the best — the opposite of every prior
failure). Still thin (9 trades) — not treating this as fully validated —
but it's a meaningfully different, more encouraging result than anything
seen before it. BNB 1d on the same strategy was NOT pursued to holdout,
since its walk-forward showed the same "alternates with no trend" pattern
that already burned two other candidates.

### Inverse strategies: every strategy now has an opposite-direction counterpart

Several strategies showed strong, statistically dense NEGATIVE expectancy
(hundreds to thousands of trades, consistently losing, not just noise).
A strategy that's confidently, repeatedly wrong about direction can
sometimes mean the opposite side has genuine edge — a real, known
phenomenon with certain crowded/naive retail signal patterns. Rather than
assume this, every strategy module now has a `run_inverse()` alongside
`run()` — wherever the original goes LONG, the inverse goes SHORT (and
vice versa), using the exact same already-direction-aware stop/target
sizing. Registered automatically as `inverse_<name>` for all 10 strategies
(20 total now) — no new files needed beyond the one function per module,
and the existing shared `simulate()` engine handles the mechanics
identically either way.

**Important caveat, worth remembering before getting excited about any
inverse result**: inversion does NOT fix costs. Where a strategy loses
mainly because fees/slippage overwhelm a tight ATR-based stop (the
dominant, well-established problem at 15m/1h), the inverse pays the exact
same fees/slippage and will likely still lose for the same structural
reason. Inversion only reveals something real when the original had a
genuine directional bias that was backwards — not when costs are the
dominant problem. Worth checking 4h/1d inverses first, where cost drag is
less dominant, rather than 15m/1h.

**Verified correct before shipping**: on the exact same hand-crafted
scenario used to validate the original engine, the inverse correctly
flips direction (LONG → SHORT) and its stop-loss/take-profit levels
correctly mirror around the identical entry price (98/104 for the
original vs. 102/96 for the inverse, symmetric around a 100 entry) — also
had to fix a real bug along the way: `trend_alignment_v1`'s special
higher-timeframe data handling in 4 scripts checked for one exact strategy
name, which silently wouldn't have matched `inverse_trend_alignment_v1`
and left it broken without an explicit error. Fixed by checking set
membership instead of exact-name equality.

### Usage

Inverse strategies work exactly like any other — no new commands:
```bash
python3 -m backtest.run_backtest --strategy inverse_trend_ema_v1
python3 -m backtest.report --compare   # now shows inverse_* rows alongside originals
```

---

## Update: confluence ensemble strategy (10th strategy)

After 9 strategies each relying on a single trigger condition failed
holdout validation (or, for funding, went untested due to a calm holdout
window), this tries a structurally different approach: combine several
independently weak signals into one weighted score, and trade only when
enough of them agree.

### `confluence_ensemble_v1`

Six components, each contributing -1 (bearish) / 0 (neutral) / +1
(bullish), deliberately spanning different market read-outs so a high
score requires genuine cross-signal agreement rather than repetition of
one idea:

| Component | Read | Bullish condition |
|---|---|---|
| RSI | mean-reversion | oversold |
| Trend (EMA fast/slow) | trend-following | fast > slow |
| MACD histogram | momentum | positive & rising |
| Volume | confirmation | elevated + up close |
| Support/Resistance | level | price near support |
| Funding rate (if available) | positioning | extreme negative |

Each weighted (`CONFLUENCE_WEIGHT_*`, default 1.0) and summed; trades only
when the total reaches `MIN_CONFLUENCE_SCORE` (default 3.0) in either
direction. The funding component gracefully contributes 0 if funding data
isn't available for that run — same degradation pattern as
`funding_extreme_reversal_v1` — so this strategy works with or without it.

Registered in `FUNDING_STRATEGIES` alongside the funding strategy, so
`run_backtest.py`/`walk_forward.py`/`run_holdout_check.py`/`optimize.py`
all automatically fetch funding data for it — no changes needed to those
four scripts, the registry-driven design paid off here.

**Tested before shipping**: all 6 components verified individually correct
against hand-crafted rows; combined threshold logic verified (a data set
achieving score=4 fires at `MIN_CONFLUENCE_SCORE=3` but is correctly
blocked at `MIN_CONFLUENCE_SCORE=5`); graceful degradation verified
(produces identical results whether or not `funding_rate` is present).

### Run order

```bash
python3 -m backtest.run_backtest --strategy confluence_ensemble_v1
python3 -m backtest.report --compare
python3 -m backtest.optimize --symbol BTCUSDT --timeframe 4h --strategy confluence_ensemble_v1
```

### When you're done

Same discipline as every strategy before it: check train results across
symbols/timeframes, optimize if something looks promising, holdout-check
once before trusting anything. Given the pattern so far — single-trigger
strategies not holding up — this is worth watching for whether requiring
genuine multi-signal agreement changes that, or whether it turns out the
same underlying signals just don't have edge regardless of how they're
combined.

---

## Update: funding rate as a real information edge (9th strategy)

### Bug found and fixed: units mismatch produced zero trades on real data

After backfill succeeded (5,475 real funding records/symbol), the strategy
produced **zero trades across every symbol and timeframe** — not because
extreme funding is rare, but because of a real bug: Binance returns
`funding_rate` as a raw fraction (`0.00003081` means `0.003081%`), while
`FUNDING_EXTREME_POSITIVE_PCT`/`NEGATIVE_PCT` in `config.py` were written
as percentage points (`0.05` meaning `0.05%`) for readability. Comparing
them directly meant the strategy was effectively requiring **5% funding
per 8-hour settlement** — something that's essentially never happened for
BTC/ETH/BNB (even extreme historical spikes rarely exceed ~0.1-0.3%).
Fixed by converting `funding_rate` to percentage terms (×100) before
comparing. My earlier synthetic tests didn't catch this because they used
unrealistic magnitudes (e.g. `0.10`) that happened to still exceed the
broken threshold for the wrong reason — retested with realistic
Binance-scale values (`0.0001`–`0.0015` range) to confirm the fix actually
fires on genuine extremes and stays silent on normal funding.

---

After 6 of 7 holdout checks failed (including optimized parameters, which
made 2 of 3 candidates *worse* out-of-sample — a clear overfitting
signature), every strategy tried so far was a pure price-pattern strategy.
This adds a structurally different one: **funding rate**, a genuine piece
of futures-market positioning information that exists independently of
any price pattern.

### Real constraints, upfront

- **Funding rate**: full history available via REST — no limitation.
- **Open interest**: Binance only retains **~30 days** of history via the
  public API — a hard platform limitation, not something we can work
  around. Meaningful OI backtesting will only become possible as the live
  poller (running since Step 1) accumulates history over time.
- **Liquidations**: **no historical REST endpoint exists at all** — only
  the live WebSocket stream going forward. Can't backtest liquidation-based
  signals on history yet; revisit once enough live data has accumulated
  (weeks/months).

### New: connectivity check before building on it

`check_futures_access.py` (project root) tests REST access to
`fapi.binance.com` — run this first. Note this only needs REST, **not**
the futures WebSocket that was found geo-restricted earlier — funding
rate history is fetched via REST only, so it may well work even though
live futures streaming (`ENABLE_FUTURES`) doesn't.

```bash
python3 check_futures_access.py
```

### New: `funding_extreme_reversal_v1`

Fades extreme funding rate — extreme positive funding (longs paying a lot
to stay long, crowded/over-leveraged) tends to precede a downward
correction; extreme negative funding (crowded shorts) tends to precede an
upward squeeze. Runs on **spot** OHLCV with funding rate joined on as a
sentiment overlay — a real technique some spot traders use, and it means
this doesn't need futures WS access at all, only the REST-backfilled
funding history.

Joined via `pd.merge_asof(direction='backward')` on funding's own
settlement time — verified with a direct test that a candle only ever
sees funding settlements that had already occurred, never a future one.
Strategy logic itself verified: fires LONG on extreme negative funding,
SHORT on extreme positive, never fires on normal funding, and gracefully
returns no trades if funding wasn't joined onto the data at all.

### Run order

```bash
# 1. confirm REST access
python3 check_futures_access.py

# 2. backfill funding rate history (full history, all configured symbols)
python3 -m collectors.backfill_funding

# 3. (optional, limited to ~30 days) backfill what OI history exists
python3 -m collectors.backfill_oi

# 4. backtest the new strategy same as any other
python3 -m backtest.run_backtest --strategy funding_extreme_reversal_v1
python3 -m backtest.report --symbol BTCUSDT --timeframe 1d --strategy funding_extreme_reversal_v1

# 5. optimize its thresholds (train only, same discipline as before)
python3 -m backtest.optimize --symbol BTCUSDT --timeframe 1d --strategy funding_extreme_reversal_v1
```

### When you're done

Run the connectivity check first and let me know if REST access works —
if it's also blocked, we'll need to talk about a workaround (VPN, etc.)
before any of this is usable. If it works, backfill funding rate and run
the new strategy across your symbols/timeframes, then share the results —
same discipline as before: train first, optimize if something looks
promising, holdout-check once before trusting anything.

---

## Update: optimizer results + a real fix to the optimizer itself

### Bug found and fixed: redundant grid combinations

The first `optimize.py` runs showed duplicate-looking entries in the "top
10" (identical trades/expectancy, differing only in `TRAIL_DISTANCE_ATR_MULT`)
— because that parameter has zero effect whenever `USE_TRAILING_STOP=False`,
but the grid was iterating over it anyway, wastefully re-testing (and
displaying) the same combination multiple times. Fixed: combinations are
now deduplicated after normalizing away trailing sub-parameters when
trailing is off — verified with a direct test (5 combos with 3 duplicates
correctly collapse to 3 distinct results).

### Honest finding: trailing stop didn't win in any of the 3 searches

Every top-ranked result across all three optimizer runs had
`USE_TRAILING_STOP: False`. The specific hypothesis — that letting profits
run would improve these strategies — wasn't borne out on train data for
these candidates. Worth having tested; just didn't pay off here.

### But real parameter improvements were found

| Symbol/TF/Strategy | Old (defaults) | New (optimized) |
|---|---|---|
| BNB 1d `smc_liquidity_sweep_v1` | +0.388R, 41.7% win, PF=1.63 | **+0.553R, 54.2% win, PF=2.13** (RR 2.5→2) |
| BTC 4h `trend_ema_v1` | +0.063R, 31.6% win, PF=1.08 | **+0.212R, 36.0% win, PF=1.28**, 114 trades (EMA 20/50→10/100) |
| BNB 4h `supply_demand_v1` | +0.020R, 34.5% win, PF=1.03 | **+0.291R, 50.0% win, PF=1.51** (displacement 1.0→1.5, RR 2.5→2) |

All three meaningfully improved over the defaults — this is the parameter
search doing its job. These are new, distinct candidates from what was
already holdout-checked (which used only the un-optimized defaults), so
they're worth their own one-time holdout checks:

```bash
python3 -m backtest.run_holdout_check --symbol BNBUSDT --timeframe 1d --strategy smc_liquidity_sweep_v1 \
  --override SMC_WICK_MIN_ATR_MULT=0.3 --override SMC_RISK_REWARD_RATIO=2 --confirm

python3 -m backtest.run_holdout_check --symbol BTCUSDT --timeframe 4h --strategy trend_ema_v1 \
  --override TREND_EMA_FAST=10 --override TREND_EMA_SLOW=100 --override TREND_RISK_REWARD_RATIO=3 --confirm

python3 -m backtest.run_holdout_check --symbol BNBUSDT --timeframe 4h --strategy supply_demand_v1 \
  --override SD_BASE_MAX_ATR_MULT=0.2 --override SD_DISPLACEMENT_ATR_MULT=1.5 --override SD_RISK_REWARD_RATIO=2 --confirm
```

(`USE_TRAILING_STOP` omitted from these — it's off by default already.)

### Budget check

This will bring the holdout-check count to 7 across the whole project.
After these three, treat whatever holds up (or doesn't) as a fairly
decisive signal for now — repeatedly optimizing-then-checking against
holdout eventually reintroduces the exact overfitting risk the holdout
split exists to prevent, just one level removed.

---

## Update: holdout results, trailing stops, and a parameter optimizer

### Holdout check results: 3 of 4 candidates failed

The first real holdout checks came back mostly negative — a genuinely
important finding, not a setback in the process:

| Candidate | Train | Holdout | Verdict |
|---|---|---|---|
| BNB 1d `smc_liquidity_sweep_v1` | +0.388R (24 trades) | +0.081R (9 trades) | Weaker, still positive, too few holdout trades to trust either way |
| BNB 1d `trend_ema_v1` | +0.256R (24 trades) | -0.069R (4 trades) | Flipped negative |
| BTC 4h `trend_ema_v1` | +0.063R (152 trades) | **-0.346R (42 trades)** | Flipped negative, real sample size |
| BNB 4h `supply_demand_v1` | +0.020R (194 trades) | **-0.590R (52 trades)** | Flipped decisively negative, real sample size |

This is the multiple-comparisons trap the holdout split exists to catch —
testing 8 strategies × 12 combos on the same train data meant some looked
positive purely by chance. Only the SMC survivor is still directionally
positive, and even that's too thin (9 trades) to lean on.

### Trailing stop, added to the shared engine (opt-in)

A fixed 2:1/3:1 target caps upside exactly when a real move is running,
and a fixed stop can get clipped by a brief spike (plausible given the
Oct-2025-onward downtrend/volatility you mentioned) before the real move
plays out. `backtest/simulate.py` now supports an opt-in trailing stop:

- Once unrealized profit reaches `TRAIL_ACTIVATION_R` (default 1.0) times
  the trade's initial risk, the stop starts trailing behind the best price
  seen by `TRAIL_DISTANCE_ATR_MULT` (default 1.5) × ATR — only ever moving
  in the favorable direction, never loosening
  - The fixed take-profit is removed entirely once trailing is active —
    upside is uncapped
- **Default is OFF** (`USE_TRAILING_STOP=False`) — every existing
  strategy's behavior is completely unchanged unless a run explicitly
  turns it on. Verified with 3 tests: the fixed-SL/TP path is byte-for-byte
  identical to before (regression test), a trade that never reaches
  activation exits at the plain original stop (matches non-trailing
  behavior exactly), and a trade with a strong favorable run captures
  substantially more than a fixed 2R cap would have (8.57R in the test).
- New `TRAIL` exit reason added to the schema (migration:
  `db/migrate_add_trail_exit_reason.sql`)

### Parameter optimizer: `backtest/optimize.py`

Grid-searches a strategy's parameters (including trailing-stop on/off and
its settings) against **TRAIN data only** — same discipline as everything
else, holdout stays untouched during the search. Ranks by expectancy, with
a minimum trade-count filter so a lucky handful of trades can't "win" over
a larger, more meaningful sample.

```bash
python3 -m backtest.optimize --symbol BNBUSDT --timeframe 1d --strategy smc_liquidity_sweep_v1
python3 -m backtest.optimize --symbol BTCUSDT --timeframe 4h --strategy trend_ema_v1 --min-trades 20
```

`run_holdout_check.py` now accepts `--override KEY=VALUE` (repeatable) so
a promising combination found by the optimizer can actually be checked
against holdout without editing `config.py` — the optimizer prints the
exact command to run. **This is still a one-time check per combination** —
running it, not liking the result, then trying more overrides defeats the
purpose just as much as re-running it on a whole new strategy would.

### Migration needed

```bash
mysql -u your_user -p < db/migrate_add_trail_exit_reason.sql
```

### When you're done

Run the optimizer on the SMC survivor and the two that failed (to see if
better parameters — including trailing stops — change anything), pick
at most one or two promising combinations, holdout-check each ONCE with
`--override`, and share the results. Given the process discipline we've
built, I'd treat "nothing survives holdout even after this" as a real,
important, and answerable finding in itself — not a reason to keep
searching indefinitely.

---

## Update: PnL simulation + double top/bottom strategy (8th strategy)

### $ PnL / equity curve simulation

Backtest results only ever showed R-multiples (risk-adjusted, account-size
independent) — useful for comparing strategies fairly, but not "what would
this actually have meant in dollars." Added:

- **`backtest/equity.py`**: converts any trade list into a dollar equity
  curve using fixed-fractional position sizing — every trade risks
  `RISK_PER_TRADE_PCT` (default 1%) of *current* equity (compounding),
  starting from `INITIAL_CAPITAL_USD` (default $5,000). This is
  mathematically consistent with the R-multiples already computed — no
  separate per-unit position-sizing math needed. Verified against a
  hand-calculated 2-trade sequence before shipping (exact match).
- **`backtest/pnl_report.py`**: detailed single-strategy $ report —
  `python3 -m backtest.pnl_report --symbol BNBUSDT --timeframe 1d --strategy trend_ema_v1`
  (override `--capital` / `--risk-pct` / `--segment` as needed)
- **`backtest/report.py --compare`** now includes `finalEquity` and
  `return%` columns by default (use `--no-pnl` to skip them and speed
  things up) — so you get $ results for every strategy in the same table
  you already use, not just as a separate command.

### 8th strategy: `double_pattern_v1` (double top / double bottom)

Classic W/M reversal pattern: two confirmed swing lows (or highs) within
`DOUBLE_PATTERN_TOLERANCE_PCT` of each other, formed within
`DOUBLE_PATTERN_MAX_BARS_APART` bars, with entry on a decisive close
**through the neckline** (the peak/trough between the two touches) — the
standard textbook confirmation, not just the second touch alone.

Pivot confirmation has a genuine, inherent lag (you can't know a low was a
local minimum until enough bars afterward confirm it) — handled correctly
by only treating a pivot as "known" starting `PIVOT_LOOKBACK_BARS` bars
after it occurred, verified with a direct lookahead test (mutating
far-future data left the earlier trade completely unchanged).

Also confirmed against a hand-crafted clean double-bottom shape (fires
correctly on the neckline break) and a smooth one-directional decline with
no second touch (correctly generates zero trades).

### Note on liquidity sweep

`smc_liquidity_sweep_v1` (added earlier) already covers "open a position
after the wick" — it waits for a wick beyond support/resistance to reject
back inside, then enters at the next candle's open. No separate strategy
needed for this; flag if something more specific was meant.

### Methodology reminder

We're now at 8 strategies × 12 symbol/timeframe combos, all still only
checked against train data. `BNBUSDT`/`1d` has now shown positive results
in two independent strategies (`trend_ema_v1` +0.256R, `smc_liquidity_sweep_v1`
+0.388R) — worth a holdout check on both before the strategy count grows
much further (see Step 4's `run_holdout_check.py`).

---

## Update: trend_alignment result + 3 new strategies + params refactor

### trend_alignment_v1 didn't solve the problem

Full comparison across BTC/ETH/BNB showed `trend_alignment_v1` performing
the same or *worse* than plain `trend_ema_v1` in most cells (e.g. ETH 4h:
+0.013R → -0.142R). The multi-timeframe confirmation filter reduced trade
counts without proportionally improving quality. Useful to know — that
hypothesis is now closed, not worth pursuing further. The only candidate
that has stayed consistently positive across everything tested so far
remains **`BNBUSDT` + `trend_ema_v1` + `1d`** (+0.256R, PF=1.36).

### Known gap: only 3 of 10 configured symbols have data

`SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, TONUSDT` currently
show `0 closed candles` — backfill has only ever been run for BTC/ETH/BNB.
Run `python3 -m collectors.backfill_klines` again when ready to test the
rest (this doesn't affect BTC/ETH/BNB — the backfill script upserts, it's
always safe to re-run).

### 3 new strategies added

| Strategy | Philosophy | Entry trigger |
|---|---|---|
| `fibonacci_retracement_v1` | Buy the pullback, in trend direction | EMA trend bias + price pulls back into the 61.8%-78.6% retracement zone (reuses the shared `features.fib_618`/`fib_786` columns) + reaction candle |
| `smc_liquidity_sweep_v1` | Fade a failed break (stop-hunt reversal) | Wick beyond support/resistance by a minimum ATR multiple, but closes back inside (rejection) — Smart Money Concepts style liquidity grab |
| `supply_demand_v1` | Trade the return to an origin zone | A small-bodied "base" candle immediately followed by a strong "displacement" candle marks a zone; price returning to retest it (without breaking through) triggers entry |

`supply_demand_v1` needed a one-time bug fix during testing: the zone was
initially becoming tradable on the *same* bar it formed (a displacement
candle's own low/high often sits right at the zone it just departed from),
causing phantom same-bar entries instead of only genuine later retests.
Fixed by reordering the precompute pass so a zone only becomes visible
starting the bar *after* it forms — verified with a synthetic test that
confirms zero phantom entries and a correct fire on a genuine later retest.

All three were tested against hand-crafted synthetic data before shipping:
Fibonacci's trend filter genuinely blocks longs in a clean downtrend, SMC
fires on a genuine sweep-and-reject but not on a real breakdown, and
supply/demand correctly waits for a real retest.

### Params refactor: single source of truth

`run_backtest.py`, `walk_forward.py`, and `run_holdout_check.py` previously
each defined their own copy of `BASE_PARAMS` — a growing risk of the three
drifting out of sync as more strategies/params were added. All three now
import `BASE_PARAMS` from **`backtest/params.py`**, assembled from
`config.py`. This is also the natural place a future UI's "adjust
parameters or use defaults" screen would read defaults from — one flat
dict covering every adjustable knob across every strategy.

### Noted for later: web UI

Flagged (not built yet, see top of this file) — select symbol(s), select
one or more strategies, adjust parameters or use defaults, run backtest,
get a comparison across everything selected. The registry pattern in
`backtest/strategies/__init__.py` and the centralized `backtest/params.py`
exist specifically so that integration doesn't require restructuring how
strategies or parameters work — a UI layer would call into these same
functions rather than needing new backend logic.

---

## Step 4: Walk-Forward Validation + Train/Holdout Split

### Where this stands after testing 4 strategies

`confluence_reversal_v1`, `breakout_continuation_v1`, and `trend_ema_v1`
were all walk-forward tested across 15m/1h/4h/1d and **none showed a
robust, stable edge** — either flat/negative in aggregate, or "profitable"
numbers that turned out to be driven by one lucky fold rather than
consistent performance. That's a real, useful finding, not a dead end —
proper backtesting is supposed to reject strategies that don't hold up.

That led to two changes:

**1. A 4th strategy: `trend_alignment_v1` (multi-timeframe trend
confirmation).** Hypothesis: `trend_ema_v1` whipsaws because it fires on a
crossover regardless of the bigger picture. This strategy only takes a
working-timeframe (e.g. 4h) EMA crossover entry when the daily trend
**also** agrees — a standard institutional-style filter, and a genuinely
different structural idea, not just a re-tuned parameter set. It computes
its own daily EMA trend and merges it onto the working timeframe with
`pd.merge_asof`, using an availability timestamp (daily candle open_time +
1 day) so a working-timeframe bar can never see a daily candle that hadn't
actually finished forming yet. Verified against synthetic data: fires when
daily and working-timeframe trends agree, is correctly **blocked** when
they disagree (the core hypothesis), and past trades are unaffected by
daily candles that hadn't happened yet.

Only meaningful below `1d` (there's no timeframe above daily currently
collected to confirm against) — running it on `1d` itself returns no trades.

**2. A real train/holdout split (`backtest/holdout.py`).** Every backtest
so far looked at the same full dataset repeatedly across many strategy
variations — the more combinations tested against the same data, the more
likely one looks good purely by chance (the multiple-comparisons trap).
Now:

- `run_backtest.py` and `walk_forward.py` operate **only on the TRAIN
  portion** (earliest `1 - HOLDOUT_FRACTION` = 80% of history) by default
- The most recent `HOLDOUT_FRACTION` (20%) is reserved and **never touched**
  during strategy exploration
- `backtest/run_holdout_check.py` is the **only** place holdout data is
  evaluated — requires `--confirm`, meant to run **once**, after a strategy
  is fully decided on, not as another exploratory tool
- The split point is an absolute timestamp, applied consistently across
  both a working timeframe and any higher-timeframe series it uses (so
  `trend_alignment_v1`'s daily confirmation data can never leak from the
  holdout calendar period into a training-period decision)
- `backtest_runs.data_segment` records which segment (`train`/`holdout`)
  every stored result came from, so this is never ambiguous later

### Usage

```bash
# add data_segment to backtest_runs if you already have that table
mysql -u your_user -p < db/migrate_add_data_segment.sql

# re-run backtests (now train-only, includes the new 4th strategy)
python3 -m backtest.run_backtest
python3 -m backtest.report --compare

# walk-forward validate (also train-only)
python3 -m backtest.walk_forward --symbol BNBUSDT --timeframe 4h --strategy trend_alignment_v1 --folds 4

# ONLY once you've fully committed to a strategy:
python3 -m backtest.run_holdout_check --symbol BNBUSDT --timeframe 4h --strategy trend_alignment_v1 --confirm
```

### How to read walk-forward output

- Each fold prints its own trade count, win rate, expectancy, profit factor, max drawdown
- **Stability summary** at the bottom tells you, in plain terms, whether the
  edge held up in every fold, none, or a mix
- Watch not just *whether* a fold is profitable but the **trend across
  folds** — e.g. escalating drawdown or declining returns in more recent
  folds is a warning sign even if the aggregate still looks positive
- Small per-fold trade counts limit how much confidence any single
  walk-forward run can give — more history or more symbols narrows this

### When you're done

Run the new comparison (now 4 strategies), share it, and we'll figure out
if `trend_alignment_v1` looks more stable than what came before — and only
run the holdout check once we're confident enough to treat it as close to
final.
