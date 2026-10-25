# UI-Managed Strategy Lifecycle and Telegram Promotion

**Latest lifecycle UI update:** PR #326 adds one-click Stop & Hide for executable strategy/mode rows. It disables evaluation, new paper entries, Telegram, and report visibility through the existing authenticated API; historical ledger rows remain, and existing open positions are not force-closed. The change passed CI and was merged as [`0d207ff`](https://github.com/kurnFR/signal-bot/commit/0d207ff2a1f68ed25da5319c120330eaf4ab7072). Actual owner-environment DB/worker restart behavior remains unverified.

Status: PRs #302–#307 implement composite attribution correction, UI-managed RetailBot2 lifecycle controls, configurable Battle Royale backtest-winner promotion, report visibility, and evaluation error logging. PR #309 adds independent holdout validation for auto-promotion. Runtime migration/application and live behavior still require owner-environment verification.
Owner-facing behavior: all lifecycle controls are persisted in the application database and editable in the authenticated dashboard. Strategy names and promotion decisions must not be embedded as backend allowlists.

## Goals

1. Let a user independently control whether each strategy is evaluated, paper-traded, eligible to send Telegram entry/exit alerts, and included in performance reports.
2. Allow a completed, eligible backtest to auto-promote a strategy according to user-configured criteria. Auto-promotion is opt-in and must never bypass validation or profitability gates.
3. Keep non-promoted strategies running in research/paper mode when the user wants performance evidence, without sending their trade alerts.
4. Let users remove a strategy from future evaluation and reporting without deleting historical trades or mutating accounting history.
5. Preserve unrelated notification streams, including Smart Money Screener and wallet tracking.

## Current implementation findings

- `paper/retailbot2.py` runs seven strategies through `RetailStrategyEngine.run_all()`, then combines same-direction signals in `build_composite()`. The first implementation step now labels those combined trades `COMPOSITE` and retains `strategies_triggered` only as contributor metadata. This prevents future composite P&L from being misrepresented as independent P&L for the first contributor. Existing historical rows remain unchanged; independent per-strategy trading is a later phase.
- PR #303 adds persisted `rdt_strategy_lifecycle` controls for each executable strategy/mode, a dashboard editor, API validation, strategy-scoped paper slots/cooldowns, Telegram entry/exit gating, and report visibility filtering. New strategy alerts default off; paper tracking and report visibility default on. The exit alert decision is stored on each trade so a later toggle does not change that position's notification lifecycle.
- RetailBot2 entry alerts still come from `_open_trade()`, exit alerts from `_close_trade()`, and the periodic performance report from `send_stats()`. The lifecycle gate applies only to strategy entry/exit alerts; the periodic report and unrelated screener/wallet notification paths remain separate.
- RetailBot2 already has a database-backed `bot_control` row polled by the running process. This is useful for process-level controls, but promotion/lifecycle configuration needs a distinct validated schema and API contract, not arbitrary tuning overrides.
- The Strategy Library and main paper engine are separate from RetailBot2. A strategy in the library does not automatically become a RetailBot2 strategy or an active `paper_configs` row. Backtest winner promotion must explicitly map a backtest strategy identity to an executable strategy and reject unmapped strategies.
- Historical reports alone cannot prove that every library strategy is generating signals or has an active paper configuration. Runtime DB state and worker logs are required for live verification.

## User-facing lifecycle model

Each registered strategy has persisted controls:

- **Evaluation**: run the strategy against incoming market data.
- **Paper tracking**: open and manage independent shadow/inverse research trades for this strategy.
- **Telegram alerts**: send entry alerts only when enabled for this strategy; exit alerts inherit the same decision captured at entry so changing a toggle does not orphan an existing position's notification lifecycle.
- **Performance report**: include/exclude this strategy from future report output without deleting trades or changing equity/accounting history.
- **Auto-promote**: whether a qualifying Battle Royale backtest winner can automatically create/update a main paper-trading configuration under the saved policy. This is distinct from the RetailBot2 lifecycle panel.
- **Promotion status/source**: research, manually promoted, auto-promoted, or paused, with timestamps and the backtest/run ID that caused a promotion.

Controls must be editable in the UI and saved in the database. Missing configuration must fail safe: evaluation may continue, but Telegram promotion is off by default for strategies without an explicit promotion decision. Existing Smart Money Screener and wallet notification paths are not governed by this strategy allowlist.

## Auto-promotion contract

Implemented in PR #304 for Battle Royale backtest winners. The policy is opt-in, saved in the database, editable in Backtest Studio, and recent decisions are auditable there. It is not a universal auto-promoter for every strategy family or for RetailBot2's seven built-in functions; strategy identity must map to a supported paper configuration.

Auto-promotion is not equivalent to “highest score wins”. The UI must expose and persist the criteria used to evaluate a completed backtest, including at least:

- Minimum closed test trades.
- Minimum profit factor.
- Minimum net return / expectancy.
- Maximum drawdown.
- Required out-of-sample / untouched-test evidence.
- Optional walk-forward consistency requirement.
- Strategy mapping and execution-cost/accounting compatibility.
- Whether auto-promotion is enabled at all.

Defaults must be conservative and fail closed. A strategy cannot auto-promote if test metrics are missing, non-finite, malformed, use an already-consumed holdout without policy approval, or fail any configured threshold. A backtest winner that fails criteria stays in research. Promotion should record an audit event with the metrics and criteria snapshot. Changing criteria later must not silently retroactively promote old runs; a user must re-evaluate them explicitly.

The exact numerical criteria are product defaults and must be editable in the UI; they are not per-strategy hardcoded rules. UI values are validated server-side.

**Selection and validation flow (PR #309):** choose `Train` in the Backtest Studio data-segment selector to rank candidates on the oldest 80% of candles. The single top-ranked candidate is then evaluated separately on the recent holdout segment, and the configured trade-count, profit-factor, expectancy, and drawdown gates use those holdout metrics. Selecting `Full` or `Holdout` is analysis-only for auto-promotion; candidates selected on those segments fail closed. If holdout evaluation errors or is missing, promotion is rejected. The holdout remains a limited historical test, not a guarantee of future performance.

## Independent performance attribution

- New RetailBot2 paper entries must be opened per strategy signal, not by assigning an aggregate composite result to the first contributor.
- Strategy and mode (shadow/inverse) must be stored on each trade; reports aggregate realized closed trades by that identity.
- If a confluence/composite strategy is retained as a separate experiment, it must be named and reported as its own strategy, never counted as independent P&L for every contributor.
- Strategy/mode limits and cooldowns must not unintentionally block other enabled strategies on the same symbol.
- Historical composite records remain immutable and should be labeled as legacy/composite data where possible; do not rewrite old trade P&L to guess attribution.

## Remove / pause semantics

- **Pause evaluation**: stop creating new signals for this strategy; existing open paper positions continue exit management.
- **Disable paper tracking**: stop opening new research trades for the strategy; existing positions continue normal exit management until closed unless the user explicitly uses a separate close-all action.
- **Disable Telegram**: suppress future entry alerts; already-open trades follow the persisted alert decision captured at entry for their exit notification.
- **Hide from report**: exclude the strategy from future report output, without deleting ledger rows.
- **Remove strategy**: deactivate future evaluation/trading and hide it from reports. Historical ledger rows remain auditable and can be shown again if the user re-enables report visibility.
- These controls must not stop wallet tracking or the Smart Money Screener.

## API and security requirements

- Read current lifecycle settings and registry from authenticated endpoints.
- Save settings with strict schema validation, known strategy IDs, explicit booleans, bounded criteria, and atomic persistence.
- UI mutation endpoints require trader/admin permissions; report/settings reads use existing authenticated role rules.
- The backend registry describes executable capabilities only; it must not contain a hardcoded promotion allowlist. Promotion state and criteria live in persistent settings.
- If a database write fails, do not report success and do not mutate in-memory state.
- Runtime workers reload persisted settings periodically and log a version/timestamp when applied.
- All alert sends must pass through one strategy-aware gate; non-strategy alert sources remain independent.
- Tests must cover default fail-closed behavior, allow/deny alert cases, exit-alert consistency, config validation, DB failure, worker reload, independent trade attribution, report hiding, auto-promotion pass/fail, and unaffected screener/wallet alerts.

## Rollout phases

1. **Correct attribution and contract**: merged in PR #302. Composite history remains unchanged and new composite P&L is no longer attributed to the first contributor.
2. **Persistent lifecycle API and UI**: implemented in PR #303. Settings are stored in the database and can be changed per strategy/mode without code edits.
3. **Independent paper accounting**: implemented in PR #303 for new signals, with per-strategy/mode entries, capacity checks, cooldowns, and restart recovery. Composite is no longer booked as if it were an independent contributor.
4. **Backtest-driven auto-promotion**: implemented for Battle Royale winners in PRs #304 and #309. Policy is opt-in, configurable in Backtest Studio, selects on Train and evaluates the fixed winner on a separate Holdout by default, and records deduplicated audit decisions. Verify migration 018 in the owner environment before enabling it. Broader automatic promotion for strategy families not mapped to supported paper configurations remains out of scope.
5. **Operator stop/hide controls**: PR #326 adds a confirmed one-click action that disables evaluation, paper entries, Telegram, and report visibility while retaining historical trades. Existing open positions are not force-closed; runtime/DB restart behavior still needs owner-environment verification.
6. **Verification**: automated tests and CI are required before merge. Then do read-only runtime inventory and user-authorized runtime validation. Do not deploy or mutate production runtime settings automatically.

## Acceptance criteria

- A user can promote/demote supported RetailBot2 strategy/mode identities in the dashboard without code edits or process restart.
- A user can configure the supported Battle Royale winner auto-promotion policy in Backtest Studio; policy is disabled by default and audit outcomes are visible.
- Only explicitly promoted strategies send entry alerts; exit behavior is consistent with the persisted entry decision.
- Every enabled strategy can continue collecting independent performance evidence without being promoted.
- A user can hide a strategy from reports and stop its future research trades without deleting historical data.
- Auto-promotion uses editable persisted criteria, is opt-in, fail-closed, auditable, and cannot promote an unmapped or unprofitable candidate.
- Screener and wallet alerts remain unchanged.
- CI verifies the settings, accounting, alert, report, and promotion behaviors.


## Backtest Studio vs RetailBot2 identity boundary (reviewed 2026-10-25)

Backtest Studio's existing Battle Royale auto-promotion path targets the main paper engine (`paper.engine` / `paper_configs`) and validates against the Backtest Studio `STRATEGIES` registry. RetailBot2 has a separate `StrategyID` registry, per-strategy/mode lifecycle settings, worker, and report/Telegram gates. Passing the main paper engine's promotion gates does **not** activate a RetailBot2 strategy and must not be treated as equivalent.

PR #341 hardens the test-only mapping resolver to accept only a separate server-owned mapping registry keyed by exact source strategy identity. Candidate payloads cannot supply or override trusted mapping records. The resolver is not connected to activation and no production mappings are defined. Do not add guessed mappings from similar names: compatibility must be reviewed against signal semantics, parameters, market/timeframe assumptions, risk/exit behavior, and accounting. Keep generalized RetailBot2 auto-promotion rejected until this source is established and the entire flow has explicit opt-in, lifecycle persistence, audit/recovery, paper-trading activation, separate Telegram controls, and stop/hide coverage.


### RetailBot2 strategy compatibility audit — reviewed 2026-10-25

- PR #342 merged as [`e17713a`](https://github.com/kurnFR/signal-bot/commit/e17713ac6c25c3e43d3b1f70d7479128170466d7) after all five required CI checks passed on head `66b15635daca059cb0a1f9d1b2b6295f17e22aa0`. It documents the trust boundary: Backtest Studio auto-promotion targets the main `paper_configs` engine, not RetailBot2 lifecycle state.
- Reviewed source implementations: RetailBot2 `strat_bband_revert` enters near/crossing Bollinger bands; Backtest Studio `confluence_reversal_v1` combines RSI oversold, rolling support, elevated volume, volatility and MACD-turn conditions. These are not equivalent strategies.
- RetailBot2 `strat_double_pattern` uses recent extrema and can enter after price moves away from an extreme; Backtest Studio `double_pattern_v1` waits for confirmed pivots and neckline-break confirmation. Entry semantics differ, so the names alone do not justify mapping them.
- **No valid source-to-RetailBot2 mappings are approved yet.** Do not map `BB_REV_INV` or `DBL_PAT_INV` to superficially similar Backtest Studio strategies. Review signal definitions, parameters, market/timeframe assumptions, risk/exit behavior and accounting before approving any mapping.
- The server-owned mapping resolver remains a fail-closed helper, disconnected from activation. Generalized RetailBot2 auto-promotion must remain rejected until a reviewed registry and end-to-end lifecycle integration exist, including opt-in policy, persistent lifecycle settings, idempotent audit/recovery, independent paper accounting, Telegram separation, and stop/hide/report behavior. CI does not verify owner-environment worker or DB state. No deployment or production DB changes.



### Seven-strategy compatibility crosswalk — reviewed 2026-10-25

- [PR #344](https://github.com/kurnFR/signal-bot/pull/344) merged this compatibility audit after all five required pre-merge checks passed; observed post-merge pytest, unit, Wallet Tracker MariaDB integration, and paper-accounting checks also passed. This is source-review and regression evidence only, not proof of deployment or profitability.


This is a source-code compatibility review, not a performance ranking. “Nearest candidate” means only a strategy worth inspecting in a future comparative study; it is **not** an approved activation mapping. RetailBot2 signals are emitted from its own feature calculations and configured 15-minute primary timeframe; Backtest Studio strategies use their own parameter schema, feature preparation, stop/target sizing, and backtest execution path. Similar labels or opposite-direction variants do not make the systems equivalent.

| RetailBot2 identity | RetailBot2 entry concept | Nearest Backtest Studio candidate(s) to investigate | Current mapping decision / reason |
|---|---|---|---|
| RSI_CTR | RSI exits an oversold/overbought zone and starts recovering/rejecting | confluence_reversal_v1 | **Not approved.** The Backtest Studio strategy also requires rolling-support context, elevated volume, volatility and MACD-turn conditions; it is not a plain RSI counter-trend equivalent. |
| MA_WHIP | Fresh EMA 9/21 crossover with a minimum ATR-based separation; EMA 200 supplies context only in other logic | trend_ema_v1, trend_alignment_v1 | **Not approved.** The RetailBot2 trigger is a crossover event. Trend/pullback and higher-timeframe alignment strategies have different entry conditions and data requirements. |
| SR_TRAP | Price near repeated pivot-based support/resistance; direction is LONG at support and SHORT at resistance, even when trend context is adverse | supply_demand_v1, smc_liquidity_sweep_v1 | **Not approved.** Zone formation/retest and liquidity-sweep confirmation differ from proximity to repeated S/R levels. |
| MACD_DIV | Price forms a lower low/higher low in MACD histogram or higher high/lower high in histogram using detected extrema | confluence_reversal_v1 | **Not approved.** Shared reversal intent is insufficient; the Backtest Studio strategy does not implement the same explicit price/MACD-divergence trigger. |
| BB_REV | Price is within configured proximity of Bollinger band or crosses back inside after exceeding it | No direct Bollinger-band strategy in the current registry; confluence_reversal_v1 is not equivalent | **No mapping.** Its RSI/support/volume/volatility/MACD conjunction is materially different. |
| DBL_PAT | Two similar extrema separated by configured bars; enters when price has moved away from the extrema, before a required neckline-break confirmation | double_pattern_v1 | **Not approved.** Backtest Studio confirms pivots and waits for neckline-break confirmation; timing and trade population differ. |
| SPIKE_FADE | Large ATR-relative candle followed by an opposite-colour confirmation candle; trades against the spike | volatility_breakout_v1, breakout_continuation_v1 | **No mapping.** These are breakout/continuation concepts, not a confirmed counter-spike fade. Reversing their direction does not reproduce the fade trigger. |

#### Crosswalk conclusion and gate

- **Approved mapping count: zero of seven.** This is deliberate; no current Backtest Studio strategy has been demonstrated source-equivalent to a RetailBot2 strategy from this inspection alone.
- The current server-owned mapping resolver validates a supplied registry but is not connected to activation. Do not add registry entries merely to make a winner promotable.
- Before any mapping is approved, compare entry/exit conditions, indicator calculation and warm-up, closed-candle/lookahead rules, symbol/market/timeframe support, parameter meaning, stop/target/risk behavior, fees/slippage, trade accounting and out-of-sample outcomes. Add deterministic fixture-based parity tests where equivalent behavior is claimed.
- A mapping approval must identify the exact source strategy ID, target RetailBot2 strategy ID and mode, mapping version, reviewer rationale, compatible parameter translation, and tests. Unmapped or ambiguous winners must be rejected with a visible reason.
- No activation, Telegram, database, runtime, or deployment changes are part of this audit. Keep RetailBot2 promotion API fail-closed until the mapping and full lifecycle workflow are implemented and tested end to end.


### Empty reviewed-registry regression guard — PR #346 (2026-10-25)

- [PR #346](https://github.com/kurnFR/signal-bot/pull/346) merged as [`2f7ead7`](https://github.com/kurnFR/signal-bot/commit/2f7ead76e170b8a1a6cb9e32e1b7fb6896bbc9d8), after all five required PR-head checks passed.
- A parameterized test now checks every registered Backtest Studio strategy against an empty server-owned mapping registry. Every candidate must be rejected; strategy-name similarity must never infer compatibility.
- The approved mapping count remains zero. No activation or Telegram behavior changed, and the resolver remains disconnected from activation. Post-merge CI subsequently completed successfully: PR #346 passed both pytest runs, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression; PR #347 passed its post-merge pytest, unit tests, Wallet Tracker MariaDB integration, and paper-accounting regression. No deployment or production DB changes were made.


### Report visibility must remain mode-scoped — PR #349 (2026-10-25)

- [PR #349](https://github.com/kurnFR/signal-bot/pull/349) added regression assertions for both RetailBot2 performance-report query paths, requiring `l.strategy_id = t.strategy AND l.mode = t.mode`.
- This protects independent shadow/inverse report visibility. All five pre-merge checks and all four post-merge checks passed. No production code, mappings, activation, DB state, or deployment changed.


### Strict lifecycle boolean contract — PR #351 (2026-10-25)

- [PR #351](https://github.com/kurnFR/signal-bot/pull/351) makes `evaluation_enabled`, `paper_enabled`, `telegram_enabled`, `report_enabled`, and `auto_promote_enabled` strict booleans at the API boundary. String/numeric coercion attempts return HTTP 422, and tests verify the persistence helper is not called.
- All five pre-merge and four post-merge CI checks passed. This does not enable automatic promotion: its API rejection remains in place. No DB/runtime/deployment changes.


### Lifecycle readback must fail closed — PR #353 (2026-10-25)

- [PR #353](https://github.com/kurnFR/signal-bot/pull/353) merged as [`4424db9`](https://github.com/kurnFR/signal-bot/commit/4424db91def562d7156a5c2ca852f9b89167fb43). All five required PR-head checks passed.
- Persisted lifecycle flags are explicitly parsed rather than relying on Python truthiness, and auto-promotion is always returned as disabled until its reviewed workflow is implemented. Regression coverage includes textual false values and legacy auto-promotion true.
- Post-merge CI was pending when recorded. No live DB, runtime, or deployment changes.


### Worker execution boundary lifecycle guard — PR #355 (2026-10-25)

- [PR #355](https://github.com/kurnFR/signal-bot/pull/355) merged as [`80b1c12`](https://github.com/kurnFR/signal-bot/commit/80b1c125c0ee80c4fcb9e58f15b11e404669a43b) after all five required pre-merge checks passed.
- Worker-side lifecycle refresh now parses boolean representations explicitly and always forces `auto_promote_enabled` false until the reviewed workflow is implemented. Regression coverage tests malformed legacy string values.
- PR #353 and #354 post-merge checks passed; PR #355 post-merge checks were pending when recorded. No live runtime or deployment changes.
