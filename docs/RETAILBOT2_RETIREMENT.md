# RetailBot2 retirement status

Owner decision (2026-11-25): RetailBot2 is no longer used for trading. Strategy-based
Paper Trading (`paper_configs`, one row per symbol / market / timeframe / strategy)
replaces it because it supports per-strategy **and per-pair** enable/disable and
per strategy+pair reporting. RetailBot2 lifecycle flags are keyed by
`(strategy_id, mode)` only.

## Already decoupled (this PR)
| Shared piece | New home | Consumers |
|---|---|---|
| `Indicators` | `paper/indicators.py` | Backtest Studio strategies |
| Paper performance report text | `paper/paper_report.py` | hourly `paper/report_scheduler.py` |

Both modules are guarded by `tests/test_retailbot2_decoupling.py`, which fails if
they start importing `paper.retailbot2` again. `paper.retailbot2` re-exports
`Indicators` and `TelegramAlert` delegates, so nothing else changed.

## Intentionally NOT removed yet
- `paper/retailbot2.py` still hosts the **Telegram outbox store** (`DatabaseManager`
  outbox methods, `OUTBOX_LOCK_NAME`, `format_entry_message/exit`) and its
  fail-closed `RETAILBOT2_*` production gates. `paper/outbox_worker.py` and
  `paper/report_scheduler.py` depend on them; the hourly Paper Trading Telegram
  report is delivered through this outbox.
- `rdt_trades`, `rdt_strategy_*`, `rdt_telegram_outbox` hold historical rows (the
  owner reported 122 rows in the queried table(s)). No data is dropped.
- `/api/research/retailbot2/*` routes stay: GET routes serve history, POST routes
  drive the report-visibility toggles in the Research archive view.
- The worker remains opt-in (`RETAILBOT2_ENABLED=1`) and never trades without
  `RETAILBOT2_ALLOW_TRADING=1`.

## To finish retirement (separate, reviewed work)
1. Move the outbox store + gates into `paper/outbox_store.py` under neutral
   `TELEGRAM_OUTBOX_*` names (keep `RETAILBOT2_*` as read-only aliases for one release).
2. Export/archive `rdt_*` history, then remove worker, routes and tables via a
   dedicated migration.
