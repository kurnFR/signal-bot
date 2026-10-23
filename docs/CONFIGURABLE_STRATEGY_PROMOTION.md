# Configurable Backtest Winner Promotion

## Scope

The Backtest Studio's Battle Royale can optionally promote its single top-ranked eligible strategy to active paper trading. This is an opt-in user setting, not a hardcoded strategy allowlist. The policy applies to every registered strategy using the same configurable criteria.

The policy is stored in `strategy_auto_promotion_policy`; each unique metrics/policy decision is claimed in `strategy_promotion_audit` before side effects, preventing duplicate deployments and duplicate Telegram deployment alerts for identical inputs.

## Default behavior

- Automatic promotion is disabled.
- Minimum trades: 30.
- Minimum profit factor: 1.20.
- Minimum expectancy: +0.05R.
- Maximum drawdown: 8R.
- Minimum rank score: 0.10.
- Holdout segment required.
- Telegram enabled for an auto-promoted paper config.
- The user can change each criterion in the Backtest Studio UI. Server-side validation still enforces bounded, finite values.

A candidate must first qualify for the existing Battle Royale ranking, then pass every saved promotion criterion. Only the top-ranked eligible candidate is considered; the system does not fall back to a lower-ranked strategy when the winner fails a gate. Unknown strategies, missing/non-finite metrics, inadequate trade counts, non-holdout segments when required, or any failed criterion prevent promotion.

The holdout is the most recent 20% of the historical dataset. Selecting a winner among many strategies on the same holdout still introduces multiple-comparison bias; it is not a guarantee of future profitability. Use walk-forward evaluation and reserve fresh data for later confirmation.

## Telegram controls

Each main paper-trading config now has its own Telegram alert toggle. Turning alerts off does not pause the config or stop paper performance collection. Manual deployment's Telegram option sets both the immediate deployment notification and the config's ongoing entry-alert preference. Auto-promotion uses the saved policy's Telegram setting.

RetailBot2 has its separate lifecycle controls in the Research tab. Smart Money Screener and wallet-tracking alerts are not controlled by either strategy allowlist.

## Database migration

Before deploying this code, apply `db/migrate_strategy_auto_promotion.sql` using the repository's established migration procedure. It adds `paper_configs.telegram_enabled` (default off for existing configs), the editable policy row, and the promotion audit table. Do not enable auto-promotion until the migration has succeeded.

## Audit states

- `rejected`: candidate did not pass policy criteria; no paper config was activated.
- `processing`: decision claimed; activation is in progress.
- `promoted`: active paper config created/updated.
- `promoted_alert_failed`: paper config activated but Telegram deployment notification failed.
- `failed`: paper config activation failed.
- `already_processed`: identical metrics and policy snapshot was already audited; no repeated side effect.

The audit keeps a snapshot of candidate metrics and the policy used. Changing the policy creates a distinct decision identity and allows explicit re-evaluation under the new criteria.
