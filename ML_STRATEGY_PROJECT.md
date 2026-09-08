# Signal Bot — Machine Learning Strategy Project

**Created:** 2026-09-08  
**Status:** DESIGN LOCKED — implementation follows the P0 execution/accounting gates  
**Priority:** P1.8  
**Scope:** ML-assisted strategy discovery, parameter optimization, validation, and paper-trading integration

## 1. Objective

Add machine learning as a new strategy family that learns from the repository's existing market data, engineered features, strategy signals, and configurable parameters.

The goal is **not** to maximize backtest profit. The goal is to discover models and parameter combinations that produce a repeatable, cost-aware trading edge that survives chronological validation and untouched out-of-sample testing.

ML must be an additional research and strategy layer. It must not replace the existing built-in strategies or bypass their risk/accounting controls.

## 2. Initial strategy design: ML signal filter

Version 1 will use ML primarily as a **signal filter**, rather than allowing a model to invent unrestricted BUY/SELL decisions.

Pipeline:

`market data -> shared features -> existing strategy signal -> ML filter -> risk/execution rules -> backtest/paper`

The first ML strategy should answer:

> Given an existing strategy signal and the information available at that candle close, is this signal sufficiently likely to produce a favorable net trade outcome?

The model may output a probability/confidence score and an accept/reject decision. It must never use future candles or future-derived statistics as input.

A later version may evaluate direct signal generation, but only after the filter architecture demonstrates reliable OOS performance.

## 3. Reuse existing infrastructure

The implementation must reuse existing components wherever possible:

- existing market/OHLCV data collectors;
- existing feature engine;
- existing 24 built-in strategy registry;
- `backtest/params.py` as the canonical parameter source;
- existing backtest simulator and canonical accounting;
- existing holdout evaluation;
- existing temporal stability testing;
- existing Battle Royale ranking/evidence infrastructure;
- existing paper-trading engine after P0 execution parity is complete.

Do not create a second independent feature calculation system or a second accounting implementation.

## 4. Parameter architecture

`BASE_PARAMS` remains the canonical source for strategy parameters. The ML system must never mutate the global `BASE_PARAMS` object.

Every parameter must have an explicit role:

| Role | Meaning |
|---|---|
| FIXED | Locked for an experiment; not searched by ML/optimizer |
| OPTIMIZABLE | Existing strategy parameter whose candidate values may be searched |
| ML_FEATURE | Parameter value is supplied as a model feature/context value |
| MODEL_HYPERPARAMETER | Controls the ML model itself, not the trading strategy |

The dashboard/settings layer should expose these categories clearly rather than presenting one uncontrolled list of numbers.

Candidate strategy parameters must be copied into an experiment-specific configuration and validated before a backtest runs.

Example flow:

`BASE_PARAMS -> validated candidate config -> backtest/ML experiment -> validation -> locked candidate -> OOS test`

## 5. Dataset contract

The ML dataset builder must create point-in-time training examples.

Each row represents information that was actually available at the decision timestamp. Features may include, where they exist in the shared feature engine:

- OHLCV-derived indicators;
- volatility and range information;
- volume information;
- trend/momentum information;
- support/resistance context;
- funding information for futures;
- existing strategy signal/direction;
- the strategy parameter values used for that candidate;
- regime/context features already available from the repository.

Do **not** assume feature names merely because another component references them. The implementation must first inspect and reuse the actual feature columns produced by the repository.

### Leakage rules

Never include:

- future OHLCV values;
- future returns;
- future indicator values;
- labels or label-derived aggregates;
- future funding events that were not known at decision time;
- statistics calculated using the validation/test period;
- information from a trade outcome that has not yet occurred.

Feature calculation must preserve indicator warmup/context without allowing future rows to influence earlier rows.

## 6. Label design

The first label should represent the **future net trading outcome of the existing signal under the canonical accounting/execution assumptions**.

The label must be generated only after the feature timestamp and must include the configured execution/cost assumptions used by the backtest.

Possible initial classification target:

- `1` = signal reaches the configured favorable outcome before an adverse outcome after costs;
- `0` = otherwise.

The exact label implementation must be aligned with the backtest's entry timing, stop/target policy, fees, slippage, and funding treatment. A model must not be trained on a cheaper or easier label than the strategy will actually trade.

## 7. Chronological train / validation / test

Random train/test splitting is prohibited for market time series.

Minimum protocol:

`TRAIN -> VALIDATION -> LOCK MODEL -> UNTOUCHED TEST`

Recommended initial split is chronological, for example:

- 60% train;
- 20% validation;
- 20% untouched test.

Exact dates/ratios must be configurable and recorded in the experiment metadata.

The test period must not be used to choose:

- features;
- strategy parameters;
- model type;
- hyperparameters;
- probability thresholds;
- trading rules.

After a model is selected, the untouched test is run once as the primary OOS evidence for that experiment. Repeated test/holdout tuning must be explicitly marked as contaminated/secondary evidence.

A later phase should add true rolling walk-forward optimization, distinct from the repository's current temporal stability testing.

## 8. Candidate models

Start with robust tabular models rather than deep learning:

1. Logistic Regression — transparent baseline.
2. Random Forest — nonlinear interactions and feature importance.
3. HistGradientBoosting — strong tabular baseline without requiring heavyweight dependencies.

XGBoost/LightGBM or other libraries may be evaluated later if dependency and deployment constraints justify them.

Do not start with LSTM/Transformer models. Complexity must be earned by evidence.

## 9. ML optimization objective

Model accuracy alone is not the optimization target.

Every candidate must ultimately be evaluated through the real trading simulator using canonical accounting.

Primary evaluation should include:

- net P&L;
- expectancy in R;
- profit factor;
- maximum drawdown;
- win rate;
- trade count;
- fees;
- slippage;
- funding for futures;
- return/drawdown relationship;
- stability across chronological segments.

The optimization objective must include minimum-trade and risk constraints so that a model cannot win simply by taking a handful of lucky trades.

A high cumulative profit with unacceptable drawdown, insufficient trades, or unstable OOS behavior is not an acceptable winner.

## 10. Experiment registry

Every ML experiment must be reproducible and versioned.

Record at minimum:

- experiment ID;
- dataset/version identifier;
- symbol/timeframe;
- date ranges;
- feature list and feature version;
- base strategy;
- strategy parameter candidate;
- ML model type/version;
- model hyperparameters;
- signal threshold;
- training/validation/test boundaries;
- accounting assumptions;
- random seed where applicable;
- validation metrics;
- untouched OOS metrics;
- eligibility/gate results;
- artifact path/version;
- creation timestamp.

Model artifacts must be immutable once promoted to an OOS-tested version.

## 11. Paper eligibility gates

An ML model must **not** become paper-eligible merely because it has the highest backtest profit.

Minimum gates should include:

1. sufficient trade count;
2. positive cost-aware expectancy;
3. acceptable maximum drawdown;
4. positive untouched OOS performance;
5. stable performance across chronological segments;
6. no material train/validation/test degradation suggesting overfitting;
7. accounting parity with the backtest/paper engine;
8. no feature leakage findings;
9. valid model artifact/version metadata;
10. portfolio/risk limits pass.

These gates should produce explicit PASS/FAIL evidence visible in the dashboard.

## 12. Dashboard / strategy menu

ML should appear as a strategy family alongside the existing built-in strategies.

The strategy/settings UI should allow the user to configure, without editing source code:

- enable/disable ML strategy;
- base strategy to filter;
- symbol/timeframe scope;
- feature set/version;
- train/validation/test dates;
- model type;
- model hyperparameters;
- signal probability threshold;
- strategy parameters that are OPTIMIZABLE;
- optimization ranges/steps;
- minimum trade count;
- drawdown limit;
- minimum OOS expectancy/profitability thresholds;
- paper-eligibility gates.

The UI must distinguish **training settings** from **live/paper trading settings** so that changing a training range does not silently alter an already locked paper model.

## 13. ML package boundary

Planned package:

```text
ml/
  __init__.py
  dataset.py          # point-in-time dataset construction
  split.py            # chronological train/validation/test splitting
  models.py           # model factory and supported model definitions
  metrics.py          # classification + trading-aware evaluation helpers
  optimize.py         # parameter/hyperparameter search orchestration
  experiment.py       # experiment metadata/results
  registry.py         # immutable model/version registry
  strategy.py         # ML signal-filter strategy adapter
```

The package should depend on existing feature, strategy, backtest, and accounting boundaries rather than duplicate them.

## 14. Implementation phases

### Phase A — Audit and dataset contract

- Inspect actual feature-engine output and available columns.
- Inspect strategy registry and signal representation.
- Inspect backtest entry/exit semantics.
- Define point-in-time feature schema.
- Define label generation against canonical accounting.
- Add leakage tests.

### Phase B — Baseline ML engine

- Add chronological splitter.
- Add Logistic Regression, Random Forest, and HistGradientBoosting adapters.
- Add deterministic experiment configuration.
- Add model metrics and artifact metadata.
- Add training/validation evaluation.

### Phase C — Trading-aware optimization

- Connect candidates to the existing backtest simulator.
- Search selected strategy parameters and ML hyperparameters.
- Optimize against cost-aware trading metrics and constraints.
- Record all candidates and winning configuration.

### Phase D — OOS and stability

- Lock selected model/configuration.
- Run untouched OOS test.
- Run temporal stability checks.
- Produce explicit eligibility evidence.
- Compare ML-filtered strategy against the unfiltered base strategy and existing Battle Royale candidates.

### Phase E — Dashboard and paper

- Add ML strategy to the strategy menu.
- Add model/experiment selection.
- Display validation/OOS evidence and gate status.
- Integrate only the locked, eligible model into paper trading.
- Preserve P0 execution/accounting parity.

## 15. What ML must never do

- mutate global `BASE_PARAMS`;
- train on the test set;
- randomly shuffle time-series observations for evaluation;
- use future candles in features;
- optimize directly on test results;
- claim profitability from classification accuracy alone;
- bypass canonical accounting;
- bypass portfolio risk controls;
- automatically promote itself to paper/live trading;
- enable live exchange execution.

## 16. Success criterion

The ML project is successful only if it demonstrates a **repeatable, cost-aware, OOS-supported improvement** over the relevant existing strategy baseline, with acceptable drawdown and stability, and can be reproduced from its recorded experiment configuration.

The system should prefer **no ML trade** over a low-confidence or unsupported trade. A model that cannot demonstrate an edge remains a research artifact and is not promoted.

## 17. Immediate next coding step

Before writing model-training code, audit these existing repository boundaries:

1. actual feature columns produced by the feature engine;
2. strategy registry and signal schema;
3. `backtest/params.py` parameter definitions and types;
4. optimizer search mechanics;
5. holdout/temporal-stability evaluation inputs and outputs;
6. dependency constraints in `requirements.txt`.

Only after that audit should the first `ml/` implementation be added.
