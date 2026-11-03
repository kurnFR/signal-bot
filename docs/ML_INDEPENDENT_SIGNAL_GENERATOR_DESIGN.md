# Independent ML Signal-Generating Strategy — Design

**Status:** Proposed architecture; implementation has not started  
**Scope:** Add a second ML mode that independently generates LONG, SHORT, or HOLD decisions. Preserve the existing ML signal-filter pipeline unchanged.

## 1. Product intent

The current ML pipeline is a signal filter: a registered technical strategy emits a candidate trade and an ML classifier accepts or rejects it. This proposal adds a distinct signal-generating model. It evaluates every eligible closed candle and generates its own directional decision without requiring a technical strategy signal.

The new model is an additional strategy in the strategy library. Users must be able to add/select it, enable or disable its paper execution, and independently enable or disable Telegram alerts for each configured strategy/model instance. Existing strategies and existing ML filter artifacts remain available and unchanged.

## 2. Two separate ML modes

| Capability | Existing ML filter | New ML signal generator |
|---|---|---|
| Input trigger | Signal from a registered strategy | Every eligible closed candle |
| Output | Accept/reject candidate signal | LONG / SHORT / HOLD |
| Depends on base strategy | Yes | No |
| Artifact contract | Existing filter artifact | New, explicitly typed generator artifact |
| Runtime identity | Base strategy + optional filter model | Dedicated strategy ID + generator model ID |
| User controls | Existing paper config | Independent strategy activation and Telegram preference |

Do not reinterpret or migrate existing filter artifacts as generator models. Do not change the semantics of existing ML filter configurations.

## 3. Proposed architecture

    closed OHLCV + point-in-time shared features
                      |
                      v
           generator feature contract
                      |
                      v
           LONG / HOLD / SHORT model
                      |
              confidence + policy
                      |
                      v
         independent ML strategy adapter
                      |
            shared risk/SL/TP contract
                      |
                      v
       canonical backtest simulator/accounting
                      |
                      v
           paper engine strategy registry
                      |
             +--------+---------+
             |                  |
       paper enabled?     Telegram enabled?
             |                  |
          positions         signal alerts

The model chooses direction only. It must not bypass existing position sizing, circuit breakers, duplicate protection, fees, slippage, stop/target handling, or the shared accounting engine. The strategy adapter will produce the same trade/signal contract as other registered strategies so historical backtest and paper trading use the same implementation.

## 4. Training target and leakage controls

The generator dataset must not be built from existing strategy trades. It must contain eligible candle decisions, including candles on which existing strategies emit no signal.

Initial target proposal: a three-class, direction-aware, ATR-scaled barrier outcome:
- LONG wins if the upper barrier is reached before the lower barrier within the configured horizon.
- SHORT wins if the lower barrier is reached before the upper barrier within the configured horizon.
- HOLD covers no barrier reached before expiry and ambiguous/invalid observations according to a documented conservative rule.

The barrier multipliers, maximum holding horizon, minimum net edge after fees/slippage, and same-candle stop/target ambiguity policy must be explicit experiment configuration and serialized into the experiment manifest. Label generation must use future bars only to create the target; none of those future values may enter the feature matrix.

Each labeled row must carry its label-resolution/exit timestamp. Use chronological train/validation/test partitions and purge rows whose label horizon crosses the next partition boundary. Add embargo where overlapping forward horizons require it. Never shuffle. Fit preprocessing on training data only.

Point-in-time features must be drawn from the existing shared feature pipeline and values available at the signal candle close. Decisions are made only on closed candles; simulated entries occur at the next bar open. Add future-bar mutation tests to prove earlier signals do not change.

## 5. Model and decision policy

Start with CPU-friendly tabular classifiers already supported by the repository (logistic regression, random forest, and histogram gradient boosting), adapted for three-class probabilities. Do not add deep learning or reinforcement learning in the first version.

The inference policy must:
- validate the artifact's task type, feature schema, model class, and supported output classes;
- require finite probabilities that sum to approximately one;
- select LONG or SHORT only when its validation-selected confidence and margin over the opposing class meet locked thresholds;
- otherwise emit HOLD;
- reject missing features, non-finite inputs, incompatible artifacts, or prediction errors (fail closed to HOLD / no new position, with an auditable reason).

Thresholds and all decision parameters are selected using validation only, then locked before one untouched test evaluation. Do not tune against test results.

## 6. Execution and evaluation

The generator must be evaluated as a standalone strategy through the canonical simulator and accounting path, not by scoring precomputed labels alone. Include fees, slippage, funding where applicable, stop/target behavior, and the same next-open execution convention used by the rest of the system.

Compare against a no-trade/hold baseline and a simple non-ML benchmark (for example, an existing trend strategy) as context only. Report trade count, net PnL, profit factor, expectancy in R, max drawdown, exposure/time-in-market, long/short breakdown, turnover, and validation-to-test degradation. Promotion must require adequate sample size and robust net-of-cost results; define exact eligibility gates before paper activation. Test is single-use.

## 7. Artifact and registry isolation

Introduce a distinct artifact task/type such as signal_generator_v1 and a versioned generator feature schema. Existing filter artifacts remain signal_filter_v1 (or retain their current legacy contract with an explicit compatibility adapter).

The loader must reject filter artifacts supplied to the generator runtime, generator artifacts supplied to the filter runtime, unsupported feature schema or target protocol versions, missing/invalid evaluation metadata, path escapes, symlinks, digest mismatch, and invalid model payloads.

Reuse the hardened no-follow manifest reader and same-file-descriptor weight verification/deserialization. Store the experiment's label protocol, horizon, barriers, feature list/version, class mapping, thresholds, train/validation/test boundaries, and metrics in the immutable manifest.

The generator has its own registry status lifecycle (research, paper-enabled, retired) or a typed extension of the existing registry. Never overwrite existing artifacts. Model IDs remain unique per run.

## 8. Strategy library, paper controls, and Telegram

Expose the generator as a selectable strategy, for example ml_signal_generator_v1. A paper configuration must identify the generator model explicitly and validate that the model is of generator type and passes its own paper eligibility gate.

Per configured symbol/market/timeframe/model:
- Strategy enabled controls whether the paper engine evaluates and opens new positions from this generator.
- Telegram enabled controls only whether alerts from this configuration are sent.
- Reporting enabled remains independently configurable.
- Disabling a strategy prevents new entries but does not silently discard or forcibly close existing open positions; normal exit management continues.
- Disabling Telegram must not disable model inference, backtesting, paper positions, or reporting.
- Telegram messages identify the strategy and generator model ID, plus direction/confidence and risk levels.
- Notifications are emitted only for the selected active configuration; no global broadcast from unselected research models.

Use the existing per-config telegram_enabled preference where its semantics fit. Verify all notification call sites honor it, and add regression tests proving disabled alerts never dispatch. Do not send a Telegram test or production notification during development.

The UI should support adding a model/configuration, selecting among available eligible generator models, enabling/disabling each strategy instance, and independently toggling Telegram. Research-only/ineligible models may be visible for inspection but must not be activatable for paper trading.

## 9. Implementation phases

### Phase 0 — Contract and architecture
- Approve this design through a documentation PR.
- Audit paper strategy dispatch, strategy picker, ML routes, paper config schema, and every Telegram signal call site.
- Produce a dependency map and migration plan before runtime changes.

### Phase 1 — Dataset and labels
- Add a candle-level generator dataset builder independent of base strategy trades.
- Add triple-barrier/directional label generation with explicit horizon and costs.
- Add label exit timestamps, purged chronological splits, and synthetic leakage tests.

### Phase 2 — Training and evaluation
- Add multiclass model training, validation-only threshold/margin selection, experiment runner, and immutable typed artifact.
- Run deterministic synthetic tests and database-free tests first.
- Add a DB-backed experiment entry point only after unit contracts are green.

### Phase 3 — Standalone backtest strategy
- Add the generator strategy adapter and inference-only loader.
- Integrate with canonical backtest simulator/accounting.
- Verify no-lookahead, next-open execution, risk behavior, and parity between backtest and paper signal generation.

### Phase 4 — Paper registry and controls
- Add typed model selection and validation to paper configs/API/UI.
- Keep disabled by default; require explicit user activation after paper eligibility.
- Preserve existing strategy and filter behavior.

### Phase 5 — Telegram controls
- Audit every signal notification path.
- Enforce per-config strategy/model selection and Telegram toggle.
- Add tests for enabled, disabled, unselected, and duplicate signal cases.

### Phase 6 — Validation and promotion
- Run walk-forward stability checks and one untouched final holdout.
- Require predeclared minimum sample and net-of-cost eligibility thresholds.
- Start paper trading only after explicit user approval; no live deployment or live trading is part of this project phase.

## 10. Acceptance criteria

- Existing ML filter training, artifacts, runtime, and configs continue to work unchanged.
- Generator creates LONG/SHORT/HOLD decisions on closed candles without calling any base strategy.
- Dataset includes candle opportunities independent of existing strategy signals.
- Future-data mutation, label-boundary purge, next-open execution, and same-candle barrier ambiguity tests pass.
- Backtest and paper use the same generator inference and signal adapter.
- Incompatible or unverified artifacts fail closed.
- Users can add/select multiple generator model configurations and enable/disable each independently.
- Telegram is independently configurable per selected strategy/model; disabled or unselected configurations produce no Telegram dispatch.
- All existing required CI checks and new tests pass before merge.
- Default state is research-only / disabled; no automatic paper activation, Telegram activation, or live execution.

## 11. Explicit non-goals for the first release

- Replacing or deleting the existing ML signal filter.
- Automatically promoting a model to paper or enabling Telegram.
- Live exchange execution.
- Deep learning, reinforcement learning, online self-modification, or autonomous strategy mutation.
- Changing global risk/accounting rules to improve ML metrics.
