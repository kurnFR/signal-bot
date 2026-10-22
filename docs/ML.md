# Machine-Learning Signal Filters

ML is an extra research layer on top of the built-in strategies. It does **not** invent trades: an existing strategy produces a signal, and a model decides whether to accept or reject it.

```
market data ─▶ shared features ─▶ base strategy signal ─▶ ML filter ─▶ risk/execution rules ─▶ backtest / paper
```

The model never sets prices, stops or sizes. Those still come from the base strategy, `simulate()` and `backtest/accounting.py`.

## 1. Rules that keep results honest

- **Point-in-time features only.** `ml/dataset.py` builds one row per base-strategy signal using data available at that candle's close. Features are `SHARED_FEATURE_COLUMNS`; there is no second feature pipeline.
- **Chronological split, no shuffling:** train / validation / test (default 60% / 20% / 20%, `ml/split.py`).
- **Selection uses the real simulator.** Model, hyperparameters and probability threshold are chosen on train and validation by running the filtered signals through the actual backtest, so fees, slippage and stop/target behaviour count. Classification metrics (`ml/metrics.py`) are diagnostics only.
- **Test is evaluated once**, after the choice is locked. Changing the model, features, threshold or strategy parameters after seeing the test result makes that test result no longer out-of-sample.
- **ML never mutates `BASE_PARAMS`.** Strategy parameters stay the single source of truth.

## 2. Targets, models and tournament

| Item | Options |
|---|---|
| Targets (`ml/dataset.py`) | `binary_positive_r`, `binary_r_above_half`, `binary_r_above_one` |
| Models (`ml/models.py`) | `logistic_regression`, `random_forest`, `hist_gradient_boosting` (CPU-friendly, tabular) |
| Threshold search | probability thresholds 0.20 to 0.50 in steps of 0.05 (`DEFAULT_THRESHOLDS` in `ml/run_real_experiment.py`) |
| Tournament | `ml/tournament.py` runs a fixed grid of candidates (logistic C 0.1/1/10, two random forests, two boosted trees); `ml/execution_tournament.py` picks the winner on validation trading results |

## 3. Running an experiment

From the dashboard: **ML Forecast Studio** → *Launch ML Tournament* (`POST /api/ml/run-experiment`), then review *Completed Experiments*.

From the command line (needs the database to hold closed OHLCV and features):

```bash
python3 -m ml.run_real_experiment \
  --symbol BTCUSDT --market spot --timeframe 1h \
  --strategy trend_ema_v1 --model logistic_regression \
  --target binary_positive_r --min-validation-trades 20 --seed 42 \
  --output-dir ml_results
```

`--model all` runs the whole tournament grid instead of a single model. It writes a JSON result to `ml_results/` and a model artifact (`<id>.json` manifest plus `<id>.joblib`) to `ml_models/`; both directories are gitignored. The run does not insert backtest or paper records.

What to read in the result: validation and test trade counts, total R or net PnL, profit factor, max drawdown, the locked threshold, and the validation-to-test degradation. A small test sample is weak evidence even when profitable.

If a run fails, the usual causes are: no database connection, the base strategy yields no completed trades, incomplete feature rows, fewer than `--min-validation-trades` validation trades, or missing dependencies. Do not loosen the leakage rules to make a run pass.

## 4. Promotion to paper trading

The gate (`ml/artifacts.py: paper_eligibility_report`) requires, on the untouched test segment: at least 30 integer-count trades, profit factor at least 1.0, max drawdown between 0% and 25%, and net PnL at least 0. Missing, malformed, fractional-count, NaN, and infinite metrics fail closed. The model list combines the metric report with the manifest's eligibility flag and a regular `.joblib` file; deployment also rechecks the manifest ID, metrics, and weight file. `ml/registry.py` independently rechecks metrics rather than trusting only `eligible_for_paper`.

Deploy from the dashboard (`POST /api/ml/deploy-to-paper`) with an allocated capital and risk per trade. The paper config stores `ml_model_id`; the paper engine then loads the `.joblib` model and applies `MLSignalFilter` with the manifest's threshold, recording `ml_model_id` and `ml_probability` on each position and trade.

How strictly the eligibility gate is enforced on this path is listed in [STATUS.md](STATUS.md).

## 5. Package map

| Module | Role |
|---|---|
| `dataset.py`, `split.py` | Point-in-time dataset, chronological split |
| `models.py`, `trainer.py` | Model factory, leakage-safe training and validation selection |
| `strategy.py`, `strategy_config.py`, `strategy_registry.py` | ML filter adapter, configuration contract, bridge to the strategy menu |
| `evaluation.py` | Filtered-vs-original comparison through the simulator |
| `tournament.py`, `tournament_runner.py`, `execution_tournament.py` | Candidate grid and execution-aware selection |
| `experiment.py`, `experiment_runner.py`, `result.py`, `runner.py` | Immutable experiment contract, orchestration, serializable result |
| `artifacts.py`, `model_store.py`, `registry.py` | Versioned artifacts, validated storage, promotion registry |
| `run_real_experiment.py` | DB-backed command-line entry point |
