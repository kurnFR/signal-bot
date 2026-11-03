# ML Signal Generator — Phase 0 Integration Audit

**Audit baseline:** master after PR #671  
**Purpose:** Map existing integration points and identify required compatibility changes before implementing the independent ML signal generator.  
**Result:** Existing infrastructure is reusable, but the current paper configuration identity and runtime dispatch are not sufficient for multiple model instances of the same strategy.

## 1. Existing architecture verified

### ML research and artifact pipeline
- `ml/dataset.py` builds supervised examples from completed trades emitted by a supplied strategy. Its input rows are therefore strategy-conditioned and cannot serve as the new generator dataset.
- `ml/experiment_runner.py` trains a classifier and evaluates it by wrapping an existing `STRATEGIES` function with `MLSignalFilter`. This is explicitly a filter workflow.
- `ml/models.py` currently exposes CPU-friendly logistic regression, random forest, and histogram gradient boosting classifiers. The first generator release can reuse these estimator families but needs a multiclass training/evaluation contract.
- `ml/artifacts.py` and `ml/model_store.py` contain the hardened manifest and weight-loading primitives. The generator must add a task/type discriminator and reject cross-loading filter artifacts.
- `ml/registry.py` currently has research, paper, and retired statuses, but its eligibility contract is tied to the current filter artifact schema. Do not reuse it blindly without typed eligibility validation.
- `web/routes/ml_routes.py` exposes filter-oriented experiment requests and a `/deploy-to-paper` endpoint. That endpoint requires `base_strategy`, a binary probability threshold, activates the config, and can send a Telegram deployment message. It is not suitable for generator deployment without a separate typed endpoint.

### Strategy and paper runtime
- `backtest/strategies/__init__.py` registers executable strategy functions in `STRATEGIES`; functions consume a dataframe and params and return trade dictionaries. This is a suitable adapter boundary for a generator once it has a standalone inference implementation.
- `paper/engine.py::sync_and_evaluate_paper_trading` discovers new entries by invoking `STRATEGIES[strategy_name](signal_df, run_params)`. It currently has a single generic path and optionally applies `MLSignalFilter` after a base strategy emits a trade.
- The paper engine already uses the latest closed candle, the next open candle, pending-order persistence, circuit breakers, and shared accounting. The generator should integrate at the strategy adapter boundary so it inherits those protections.
- Exit management is keyed by symbol, market, timeframe, and strategy name. Existing positions continue through the normal SL/TP/trailing/time-exit path.
- `paper/engine.py` snapshots `telegram_enabled` into a pending order and checks it both on immediate fill and restart recovery. This is a good foundation for per-config Telegram behavior.
- `web/routes/paper_routes.py` accepts `is_active`, `ml_model_id`, `telegram_enabled`, and `report_enabled`. The current model field is explicitly the existing filter model association, not a typed generator model reference.
- `web/static/js/app.js` builds strategy choices from `/api/backtest/strategies` and creates paper configs with activation and Telegram controls. It currently has no generator model selector or separate generator artifact type.

## 2. Critical schema limitation: configuration identity

The current `paper_configs` unique key is:

```sql
UNIQUE KEY uq_paper_cfg (symbol, market, timeframe, strategy_name)
```

The paper order idempotency key is `(config_id, signal_open_time)`. The open-position uniqueness key is generated from:

```
symbol | market | timeframe | strategy_name
```

Consequences:
1. A user cannot add two independent model instances for the same symbol/market/timeframe/strategy; the config upsert overwrites the existing row.
2. A model ID cannot currently distinguish independent generator configs.
3. The open-position constraint would prevent two instances of the same strategy from maintaining independent positions.
4. `_signal_already_traded` checks symbol/market/timeframe/strategy/entry candle, not model/config identity.
5. Historical trade reporting groups by strategy name, which can combine distinct model runs.

**Required design change:** introduce a stable strategy-instance identity in paper configuration and propagate it through pending orders, positions, trades, idempotency, and reporting. A typed `model_id` (separate from legacy filter `ml_model_id`) or a stable `config_id` must distinguish generator instances. The migration must preserve existing rows and legacy uniqueness behavior for existing configurations. Do not simply drop uniqueness constraints: preserve duplicate-signal and one-open-position guarantees per configured instance.

Preferred approach to validate during implementation:
- Add nullable `ml_generator_model_id` (or typed `model_ref`) to `paper_configs`.
- Add a stable `strategy_instance_key` / config identity and migrate existing rows deterministically.
- Rework unique config and open-position constraints to include the instance discriminator while retaining one open position per instance.
- Add the config/instance identifier to position/trade attribution and reporting.
- Ensure pending orders remain unique per config instance and candle.
- Keep legacy `ml_model_id` filter semantics unchanged.

The exact migration must be designed against the live schema's actual indexes and column state; do not assume a fresh database.

## 3. Telegram routing findings

- Paper entry alerts are sent by `_send_paper_entry_alert` after a pending order fills. Both immediate fills and restart recovery consult the order's persisted `telegram_enabled` value.
- This provides a reliable place to carry the selected generator model ID and signal confidence into the alert.
- The generic ML `/deploy-to-paper` endpoint currently defaults `send_telegram=True`, activates the paper config, and sends a deployment announcement. Generator onboarding must not reuse this endpoint or inherit this default.
- The new generator should use an explicit activation request with safe defaults: disabled paper execution and disabled Telegram unless the user opts in. A Telegram-off config must still evaluate/backtest/report according to its other settings, but must never dispatch entry alerts.
- Audit all other signal send paths during implementation; this audit covered the primary paper fill and ML deployment paths, not every notifier call site in the repository.

## 4. Proposed generator-specific integration contract

Keep the old filter path unchanged:
- `paper_configs.ml_model_id` continues to mean a binary filter model attached to a registered base strategy.
- Existing filter artifacts and `/api/ml/deploy-to-paper` retain their current typed semantics and compatibility behavior.

Add a separate generator path:
- `strategy_name = ml_signal_generator_v1` identifies the strategy adapter.
- `ml_generator_model_id` identifies a typed `signal_generator_v1` artifact.
- The adapter consumes the latest closed-candle feature row and produces zero or one candidate trade with LONG/SHORT direction, risk levels, and model confidence.
- The adapter does not call another strategy to create a candidate signal.
- Paper runtime validates artifact type and eligibility before any new entry. Any missing/invalid model or inference error fails closed (no new position).
- Generator configs are research-only/inactive by default. Explicit paper activation and Telegram opt-in are separate user actions.
- Disabling new entries does not silently close existing positions; normal exit management remains active.

## 5. Implementation order and gates

1. **Dataset contract:** independent candle rows, directional barrier labels, explicit label-resolution timestamps, costs, and purge/embargo tests.
2. **Multiclass training:** fit only on training rows; choose decision thresholds on validation; lock before untouched test.
3. **Typed artifact:** explicit task, feature schema, class map, label protocol, data boundaries, and metrics; reuse secure storage primitives.
4. **Standalone adapter:** inference-only LONG/SHORT/HOLD strategy with shared risk contract and no base-strategy dependency.
5. **Paper schema/runtime:** instance identity migration, model-type validation, independent config controls, idempotent order and position uniqueness.
6. **UI and Telegram:** model selection, per-instance activation, independent Telegram opt-in, no dispatch for disabled/unselected configs.
7. **Evaluation:** canonical simulator, net-of-cost OOS metrics, and explicit eligibility gate. No automatic promotion.

## 6. Safety and compatibility constraints

- No changes to live runtime, production database, paper activation, model training, or Telegram dispatch during this audit.
- No existing ML filter artifact is converted or removed.
- New generator code must not accept a filter artifact or use `base_strategy` as a signal source.
- New configurations remain disabled until explicitly activated.
- Existing open positions continue to receive exit management after a strategy is disabled.
- All schema changes must be additive/backward-compatible and covered by migration tests and MariaDB integration tests.
