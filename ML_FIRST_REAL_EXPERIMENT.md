# First Real ML Experiment

**Status:** research-only execution

The repository now has a local DB-backed runner for the first real historical ML experiment. It is intentionally separate from paper/live deployment.

## Locked first experiment

- Symbol: `BTCUSDT`
- Market: `spot`
- Timeframe: `1h`
- Base strategy: `trend_ema_v1`
- Model: `logistic_regression`
- Features: current `SHARED_FEATURE_COLUMNS`
- Split: chronological `60% train / 20% validation / 20% test`
- Threshold search: `0.50` through `0.90`
- Candidate count: `1`
- Selection: execution-aware validation through the real simulator
- Test: untouched until the model and threshold are locked, then evaluated once
- Status: `research`

## Run locally

From the repository root, using the same Python environment that can access the configured MySQL/MariaDB database:

```bash
python3 -m ml.run_real_experiment
```

Optional overrides:

```bash
python3 -m ml.run_real_experiment \
  --symbol BTCUSDT \
  --market spot \
  --timeframe 1h \
  --strategy trend_ema_v1 \
  --model logistic_regression \
  --output-dir ml_results
```

The runner reads closed OHLCV/features through the existing DB helper and does not insert backtest, paper, or registry records. The JSON result is written below `ml_results/` by default.

## What the result means

The validation winner is selected using actual simulated trades, so execution timing, fees, slippage, stop/target behavior and other simulator semantics participate in model/threshold selection.

The test result is **OOS evidence only**. Do not tune the model, feature set, threshold, strategy parameters, or trading rules after seeing the test result and then call that same test result untouched.

A profitable OOS result is not automatically paper-eligible. Paper promotion still requires the project's eligibility gates, including sufficient trades, cost-aware profitability, drawdown limits, stability, accounting parity and artifact/version evidence.

## If the run fails

Common causes are:

1. Database connection/configuration is unavailable.
2. The selected strategy produces no completed trades.
3. Historical feature rows are incomplete or do not match the point-in-time dataset contract.
4. Validation produces fewer than the configured minimum trades.
5. Required Python dependencies are missing.

Do not weaken the leakage or OOS rules to make an experiment run.

## Next evidence to capture

Paste the runner output and the saved JSON summary back into the development workflow. The next decision should be based on:

- validation trade count, total R/net P&L, PF and max drawdown;
- locked threshold;
- untouched test trade count, total R/net P&L, PF and max drawdown;
- validation-to-test degradation;
- whether the OOS sample is large enough to be meaningful.

Only after reviewing that evidence should we expand the candidate grid to additional models or optimize strategy parameters.
