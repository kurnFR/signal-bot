# UI-Managed Strategy Lifecycle and Telegram Promotion

Status: phased implementation in progress (PR #302)
Owner-facing behavior: all lifecycle controls are persisted in the application database and editable in the authenticated dashboard. Strategy names and promotion decisions must not be embedded as backend allowlists.

## Goals

1. Let a user independently control whether each strategy is evaluated, paper-traded, eligible to send Telegram entry/exit alerts, and included in performance reports.
2. Allow a completed, eligible backtest to auto-promote a strategy according to user-configured criteria. Auto-promotion is opt-in and must never bypass validation or profitability gates.
3. Keep non-promoted strategies running in research/paper mode when the user wants performance evidence, without sending their trade alerts.
4. Let users remove a strategy from future evaluation and reporting without deleting historical trades or mutating accounting history.
5. Preserve unrelated notification streams, including Smart Money Screener and wallet tracking.

## Current implementation findings

- `paper/retailbot2.py` runs seven strategies through `RetailStrategyEngine.run_all()`, then combines same-direction signals in `build_composite()`. The first implementation step now labels those combined trades `COMPOSITE` and retains `strategies_triggered` only as contributor metadata. This prevents future composite P&L from being misrepresented as independent P&L for the first contributor. Existing historical rows remain unchanged; independent per-strategy trading is a later phase.
- RetailBot2 sends entry alerts from `_open_trade()`, exit alerts from `_close_trade()`, and a periodic report from `send_stats()`. These paths currently have no strategy-level promotion/report controls.
- RetailBot2 already has a database-backed `bot_control` row polled by the running process. This is useful for process-level controls, but promotion/lifecycle configuration needs a distinct validated schema and API contract, not arbitrary tuning overrides.
- The Strategy Library and main paper engine are separate from RetailBot2. A strategy in the library does not automatically become a RetailBot2 strategy or an active `paper_configs` row. Backtest winner promotion must explicitly map a backtest strategy identity to an executable strategy and reject unmapped strategies.
- Historical reports alone cannot prove that every library strategy is generating signals or has an active paper configuration. Runtime DB state and worker logs are required for live verification.

## User-facing lifecycle model

Each registered strategy has persisted controls:

- **Evaluation**: run the strategy against incoming market data.
- **Paper tracking**: open and manage independent shadow/inverse research trades for this strategy.
- **Telegram alerts**: send entry alerts only when enabled for this strategy; exit alerts inherit the same decision captured at entry so changing a toggle does not orphan an existing position's notification lifecycle.
- **Performance report**: include/exclude this strategy from future report output without deleting trades or changing equity/accounting history.
- **Auto-promote**: whether a qualifying backtest can automatically enable paper tracking and Telegram alerts.
- **Promotion status/source**: research, manually promoted, auto-promoted, or paused, with timestamps and the backtest/run ID that caused a promotion.

Controls must be editable in the UI and saved in the database. Missing configuration must fail safe: evaluation may continue, but Telegram promotion is off by default for strategies without an explicit promotion decision. Existing Smart Money Screener and wallet notification paths are not governed by this strategy allowlist.

## Auto-promotion contract

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

1. **Correct attribution and define contract**: implemented in this PR by labeling new composite trades `COMPOSITE`, retaining contributors as metadata, and adding a regression test. Historical rows remain unchanged. Independent per-strategy trading remains incomplete.
2. **Persistent lifecycle API and UI**: expose per-strategy evaluation, paper, Telegram, and report controls plus audit metadata.
3. **Independent paper accounting**: open/close each strategy's own shadow/inverse paper trade, with per-strategy risk/cooldown and restart recovery.
4. **Backtest-driven auto-promotion**: connect completed backtest results to configurable criteria, validate strategy identity mapping, and record auditable promotion decisions.
5. **Verification**: automated tests, CI, read-only runtime inventory, then user-authorized runtime validation. Do not deploy or mutate production runtime settings automatically.

## Acceptance criteria

- A user can promote/demote any executable strategy in the dashboard without code edits or process restart.
- Only explicitly promoted strategies send entry alerts; exit behavior is consistent with the persisted entry decision.
- Every enabled strategy can continue collecting independent performance evidence without being promoted.
- A user can hide a strategy from reports and stop its future research trades without deleting historical data.
- Auto-promotion uses editable persisted criteria, is opt-in, fail-closed, auditable, and cannot promote an unmapped or unprofitable candidate.
- Screener and wallet alerts remain unchanged.
- CI verifies the settings, accounting, alert, report, and promotion behaviors.
