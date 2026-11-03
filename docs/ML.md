# Machine-Learning Signal Filters

ML is an extra research layer on top of the built-in strategies. It does **not** invent trades: an existing strategy produces a signal, and a model decides whether to accept or reject it.

```
market data ─▶ shared features ─▶ base strategy signal ─▶ ML filter ─▶ risk/execution rules ─▶ backtest / paper
```

The model never sets prices, stops or sizes. Those still come from the base strategy, `simulate()` and `backtest/accounting.py`.

## 1. Rules that keep results honest

- **Point-in-time features only.** `ml/dataset.py` builds one row per base-strategy signal using data available at that candle's close. Features are `SHARED_FEATURE_COLUMNS`; there is no second feature pipeline.
- **Purged chronological split, no shuffling:** train / validation / test (default 60% / 20% / 20%, `ml/split.py`). Every supervised trade row carries its `exit_time`; train labels whose trades reach validation are purged, and validation labels whose trades reach test are purged. Absolute-timestamp splits and walk-forward folds apply the same rule. Tournament and walk-forward entry points reject datasets without exit timestamps.
- **Selection uses the real simulator.** Model, hyperparameters and probability threshold are chosen on train and validation by running the filtered signals through the actual backtest, so fees, slippage and stop/target behaviour count. Classification metrics (`ml/metrics.py`) are diagnostics only; validation probability diagnostics include ROC-AUC and Brier score.
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

`--model all` runs the whole tournament grid instead of a single model. It writes a JSON result to `ml_results/` and a model artifact (`<id>.json` manifest plus `<id>.joblib`) to `ml_models/`; both directories are gitignored. Result IDs are unique per run and validated before they are used as paths. The JSON exposes both canonical nested metrics and dashboard-compatible aliases, and the API normalizes older result files when reading them. The run does not insert backtest or paper records.

What to read in the result: validation and test trade counts, total R or net PnL, profit factor, max drawdown, the locked threshold, and the validation-to-test degradation. A small test sample is weak evidence even when profitable.

If a run fails, the usual causes are: no database connection, the base strategy yields no completed trades, incomplete feature rows, fewer than `--min-validation-trades` validation trades, or missing dependencies. Do not loosen the leakage rules to make a run pass.

## 4. Promotion to paper trading

The gate (`ml/artifacts.py: paper_eligibility_report`) requires, on the untouched test segment: at least 30 integer-count trades, profit factor at least 1.0, max drawdown between 0% and 25%, and net PnL at least 0. Missing, malformed, fractional-count, NaN, and infinite metrics fail closed. The model list combines the metric report with the manifest's eligibility flag and a regular `.joblib` file; paper manifests must include a SHA-256 digest of the serialized weights. The paper runtime opens the resolved manifest with `O_NOFOLLOW`, verifies the descriptor is a regular file, and validates eligibility from that one manifest snapshot. It then hashes and deserializes weights through the same opened file descriptor. The model store, online retrainer, ML listing/deployment routes, and read-only artifact audit use the same no-follow manifest reader. The runtime builds the inference filter from the verified artifact/model pair; missing files, digest mismatches, unsupported feature schemas, or prediction failures fail closed. This detects weight-file changes when the manifest remains intact, but a hash stored beside the weights is not a signature: an attacker able to replace both files can replace the digest too. Use trusted signing or an independently protected provenance store for that threat model. PR #263 additionally requires model manifests and weights to resolve inside `ml_models/`, rejecting external symlinks. Before activating a paper config, deployment validates the threshold as finite and strictly between 0 and 1, requires `strategy_params` to be an object, and validates symbol, market, timeframe, and registered base strategy. `ml/registry.py` independently rechecks metrics rather than trusting only `eligible_for_paper`.

**Existing artifact compatibility:** manifests created before weight-digest enforcement do not contain `weights_sha256` and are now rejected by the paper eligibility loader.

**Evaluation protocol compatibility:** paper-eligible manifests must also declare `evaluation_protocol_version: "purged_exit_time_v1"`. This marker is written by the current experiment runner only after the dataset builder has required trade exit timestamps; chronological train/validation boundaries purge trades whose realized outcomes reach the next partition. Older artifacts without this marker are not eligible for paper deployment, even if their metrics and weight digest otherwise pass. Re-run the experiment with the current code from trusted source data and review the new untouched-test metrics. Do not add or change the marker manually on an old manifest; that would misrepresent how the model was evaluated. Do not simply calculate a digest for an unverified legacy file and append it to the manifest: that would bind the manifest to whatever bytes happen to be present, not prove their origin. Re-run the experiment from a trusted source and save it through `ModelStore` to produce a digest, then re-check the resulting test metrics and eligibility before promoting it. Treat existing eligible flags as insufficient until this migration is complete.

Deploy from the dashboard (`POST /api/ml/deploy-to-paper`) with an allocated capital and risk per trade. The paper config stores `ml_model_id`; the paper engine then loads the `.joblib` model and applies `MLSignalFilter` with the manifest's threshold, recording `ml_model_id` and `ml_probability` on each position and trade.

How strictly the eligibility gate is enforced on this path is listed in [STATUS.md](STATUS.md).

### Read-only artifact compatibility audit

Before promoting existing artifacts after digest enforcement, inventory the configured artifact directory without deserializing any `.joblib` file:

```bash
python -m ml.audit_artifacts --directory ml_models
python -m ml.audit_artifacts --directory ml_models --json
```

The command only reads root-level JSON manifests and weight bytes. It reports missing/invalid digests, digest mismatches, missing or orphan files, malformed manifests, ID mismatches, and symlinks. It does not edit or repair artifacts, and a `digest_matches` result proves byte consistency with the adjacent manifest only—not trusted provenance. Run it against the actual artifact directory on the owning machine; model artifacts are gitignored and are not expected to exist in a repository checkout. Regenerate untrusted legacy artifacts from a trusted experiment instead of adding a digest to an unverified file.

For the proposed signature-based authenticity boundary, key-management options, threat model, and rollout gates, see [ML artifact provenance design](ML_ARTIFACT_PROVENANCE_DESIGN.md). This is a design proposal only; signing and signature enforcement are not implemented.

## 5. Online retraining

Online retraining consumes the same experiment-result contract as the dashboard. A cycle is checkpointed only after the result identifies its experiment and the matching manifest/weight pair exists inside `ml_models/` with a matching SHA-256 digest. Missing or inconsistent artifacts leave the cycle failed and do not advance the candle checkpoint. The verifier reads the manifest and hashes the weights through no-follow file handles.

The `auto_promote` setting is currently an audit request only: `auto_promote_applied` remains false and the promotion status is `not_implemented`. Online retraining does not activate paper configurations or Telegram alerts. Paper deployment remains a separate explicit operation after the untouched-test eligibility gate passes.

## 6. Package map

| Module | Role |
|---|---|
| `dataset.py`, `split.py` | Point-in-time dataset with trade exit timestamps, chronological purged splits |
| `models.py`, `trainer.py` | Model factory, leakage-safe training and validation selection |
| `strategy.py`, `strategy_config.py`, `strategy_registry.py` | ML filter adapter, verified-artifact inference factory, configuration contract, bridge to the strategy menu |
| `evaluation.py` | Filtered-vs-original comparison through the simulator |
| `tournament.py`, `tournament_runner.py`, `execution_tournament.py` | Candidate grid and execution-aware selection |
| `experiment.py`, `experiment_runner.py`, `result.py`, `runner.py` | Immutable experiment contract, orchestration, serializable result |
| `artifacts.py`, `model_store.py`, `registry.py` | Versioned artifacts, validated storage, promotion registry |
| `run_real_experiment.py` | DB-backed command-line entry point |


## 7. Independent ML signal generator

The generator is a separate ML task, not a replacement for this filter pipeline:

```
closed candles -> point-in-time shared features -> LONG / HOLD / SHORT
              -> confidence + directional-margin policy
              -> standalone strategy -> canonical simulator / paper engine
```

- `ml/generator_dataset.py` creates one opportunity per eligible closed candle; it does not require a technical strategy trade. Labels use next-open entry, ATR barriers, an explicit forward horizon, cost hurdle, and label-resolution timestamps.
- `ml/generator_trainer.py` fits the multiclass estimator and train-only preprocessing. The end-to-end runner selects confidence and directional-margin thresholds through actual validation execution using `backtest.simulate`; overlapping candle opportunities are not counted as independent executed trades.
- `ml/generator_experiment.py` evaluates the locked policy once on the untouched test partition through the same simulator, verifies non-overlapping execution, and saves a typed `signal_generator_v1` artifact with the hardened model store.
- The dedicated research endpoint is `POST /api/ml/generator/run-experiment`. It fetches closed candles and shared features, trains a new generator, and returns its model ID, policy, validation/test metrics, and paper eligibility. This endpoint does not activate paper trading or send Telegram.
- Generator artifacts are listed separately at `GET /api/ml/generator-models`. A generator artifact cannot be used as a legacy filter artifact, and a filter artifact cannot be used as a generator.
- The selectable paper strategy is `ml_signal_generator_v1`; its paper config uses `ml_generator_model_id`, not the legacy `ml_model_id`. Multiple generator config instances are isolated for order identity, open positions, and performance attribution.
- Strategy activation and Telegram alerts are independent per paper config. Telegram is off unless explicitly enabled. Disabling a strategy prevents new entries while normal exit management continues for existing positions.

**Research vs paper eligibility:** label-level trainer diagnostics alone are not paper evidence. The generator paper gate requires untouched canonical simulator metrics identifying `evaluation_mode=canonical_simulator_oos`, `execution_engine=backtest.simulate`, and non-overlapping executed trades, in addition to the generator-specific trade-count, profit-factor, drawdown, and net-return requirements. Failed models remain research-only and can be inspected, but cannot be activated for paper trading. No automatic paper promotion or Telegram opt-in is performed.


The default dashboard's ML Forecast Studio exposes this workflow through the **Train Independent ML Signal Generator** form. It displays the saved model ID, locked policy, partition sizes, and untouched-test diagnostics. After a model is saved, the Paper Trading configuration form refreshes its generator model selector. Research-only models can be saved with paper execution disabled; only eligible artifacts may have paper activation enabled. The Telegram checkbox remains an independent per-config setting.


Generator execution assumptions are pinned to the server's configured `BACKTEST_FEE_PCT` and `BACKTEST_SLIPPAGE_PCT`; the UI does not permit overriding those values for an experiment. Paper inference fails closed if the server cost settings later differ from the artifact's stored assumptions. The artifact's stop distance, reward multiple, and maximum holding bars are carried into the generator strategy and persisted on paper positions so paper exits use the evaluated limits.
