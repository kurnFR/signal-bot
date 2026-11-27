# Pure TP/SL Mode

Pure TP/SL is an exit mode in which a trade can only close on its take-profit
or stop-loss. There is no trailing stop and no maximum-hold timeout. A trade
that never reaches TP or SL stays open until it does (in a backtest it ends
as `END_OF_DATA`).

## Why it exists

The headline paper win rate counts *any* trade with `r_multiple > 0` as a win,
so a trailing-stop or timeout partial win inflates it. Pure TP/SL reporting
separates the clean TP/SL outcome from every other exit, so the strategy's
planned risk:reward can be judged on its own terms. (Planned RR is 2.0; with
0.05% entry slippage the realised risk is wider, so a clean TP nets ~+1.61R
and a clean SL ~-1.24R after 0.1%/side fees and 0.05% exit slippage.)

## Reporting (all strategies)

`paper/engine.py get_paper_metrics()` keeps every existing field unchanged
and adds, overall and per `byStrategy` / `byStrategySymbol` row:

- `pureTrades`, `pureWins`, `pureLosses`, `pureWinRatePct`, `pureNetPnl`,
  `pureSumR` (alias `pureCumulativeR`), `pureExpectancyR`, `avgWinR`,
  `avgLossR` — filtered to `exit_reason IN ('TP', 'SL')`.
- `exitReasonBreakdown`: one row per exit reason (`TP`, `SL`, `TRAIL`,
  `TIMEOUT`, `MANUAL`, plus any other reason present) with trades, wins,
  losses, net PnL and sum R. The `closed_since_ms` filter applies to the
  pure aggregates and the breakdown too.

`backtest/metrics.py compute_metrics()` adds the same aggregates in
snake_case (`pure_trades`, `pure_win_rate_pct`, `pure_net_pnl`,
`pure_expectancy_r`, `pure_avg_win_r`, `pure_avg_loss_r`,
`exit_reason_breakdown`). `END_OF_DATA` trades are excluded from pure
metrics and shown separately in the breakdown.

The Telegram paper report (`paper/paper_report.py`) shows a Pure TP/SL
section and the other-exits breakdown below the existing totals.

## Backtest exit mode — `POST /api/backtest/run`

Field `exit_mode`: `"pure" | "dynamic" | "both"`, default `"dynamic"`.
An unknown value is a 400.

- **dynamic** — current behaviour, unchanged (trailing stop when enabled,
  `BACKTEST_MAX_HOLD_BARS` timeout).
- **pure** — `USE_TRAILING_STOP=False`, no timeout; trades run to TP/SL or
  `END_OF_DATA`. Custom strategies run with `custom_exit_policy` disabled.
- **both** — the simulation runs twice on the same data and parameters and
  returns `results: { dynamic: { metrics, equity, trades }, pure: {...} }`.
  The top-level `metrics` / `trades` / `equity` stay the **dynamic** run so
  old clients keep working, and only the dynamic run is persisted to the
  database (`runId` refers to it). `exitMode` is echoed top-level.

## Paper trading — per deployment

Stored in `paper_configs.strategy_params` (JSON column, **no DB migration**)
as the bool key `PURE_TP_SL` (default `False`). It is a `BASE_PARAMS` key
(`backtest/params.py`), so `validate_param_overrides()` accepts it — and
because the default is a bool, only real booleans pass validation (never
`1`/`0`).

When `PURE_TP_SL` is true for a deployment, **new** trades are pure only:

- The simulator attaches a disabled `custom_exit_policy`
  (`enabled: False`, `activation_r: 9999.0` — a never-arming sentinel,
  `max_hold_bars: 0`) to every trade, retail runners included.
- `paper/engine.py _insert_position` forces the same policy when the config
  flag is set (covers strategies that do not go through the simulator), stores
  `trail_activation_r = 9999.0` and `max_hold_bars = 0`. `0` means "no
  timeout": `paper_positions.max_hold_bars` is `INT NOT NULL DEFAULT 200`
  with no CHECK constraint, so `0` stores cleanly, and Phase 1 skips the
  TIMEOUT check when `max_hold_bars == 0`. For ML generator deployments the
  pure flag intentionally overrides the artifact's max-hold; the fee/slippage
  contract check still applies.
- TP/SL still close through `resolve_stop_target_exit`, including gap fills
  and SL-wins-ties.

**Open positions keep the mode they were opened with.** Changing a config's
exit mode affects only new trades; the mode lives on the position
(`custom_exit_policy` / `trail_activation_r` / `max_hold_bars`). Manual close
(`MANUAL`) remains available as an emergency escape and is excluded from pure
statistics.

Telegram entry alerts state the mode: `Exit: Pure TP/SL` vs `Exit: Dynamic`
(`notifications/telegram_notifier.py send_signal_alert(..., exit_mode=...)`).
