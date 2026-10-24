# UI-Managed Strategy Lifecycle and Telegram Promotion

Status: PRs #302–#304 implement composite attribution correction, UI-managed RetailBot2 lifecycle controls, and configurable Battle Royale backtest-winner promotion. Runtime migration/application and live behavior still require owner-environment verification.
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

**Important safety limitation:** the current Battle Royale request ranks candidates on the selected data segment. It must not promote a candidate ranked on the same `holdout` segment used as its promotion gate, because this reuses the holdout for both selection and confirmation. A follow-up safeguard rejects that path. With the default `require_holdout=true`, automatic promotion remains blocked until candidate selection on a separate train/validation segment and a distinct holdout evaluation are implemented. Disabling the holdout requirement is an explicit opt-out and permits only a `train`-segment run; it does not make training performance equivalent to out-of-sample evidence.

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
4. **Backtest-driven auto-promotion**: implemented for Battle Royale winners in PR #304. Policy is opt-in, configurable in Backtest Studio, requires the configured gates (including holdout evidence by default), and records deduplicated audit decisions. Verify migration 018 in the owner environment before enabling it. Broader automatic promotion for strategy families not mapped to supported paper configurations remains out of scope.
5. **Verification**: automated tests and CI are required before merge. Then do read-only runtime inventory and user-authorized runtime validation. Do not deploy or mutate production runtime settings automatically.

## Acceptance criteria

- A user can promote/demote supported RetailBot2 strategy/mode identities in the dashboard without code edits or process restart.
- A user can configure the supported Battle Royale winner auto-promotion policy in Backtest Studio; policy is disabled by default and audit outcomes are visible.
- Only explicitly promoted strategies send entry alerts; exit behavior is consistent with the persisted entry decision.
- Every enabled strategy can continue collecting independent performance evidence without being promoted.
- A user can hide a strategy from reports and stop its future research trades without deleting historical data.
- Auto-promotion uses editable persisted criteria, is opt-in, fail-closed, auditable, and cannot promote an unmapped or unprofitable candidate.
- Screener and wallet alerts remain unchanged.
- CI verifies the settings, accounting, alert, report, and promotion behaviors.
