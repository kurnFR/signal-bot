# RetailBot2 retirement status

Owner decision (2026-11-25): RetailBot2 is no longer used for trading. Strategy-based
Paper Trading (`paper_configs`, one row per symbol / market / timeframe / strategy)
replaces it because it supports per-strategy **and per-pair** enable/disable and
per strategy+pair reporting. RetailBot2 lifecycle flags are keyed by
`(strategy_id, mode)` only.

## Done

### Step 1 -- outbox move + ceiling alias (commit `b5f11244`)
- The Telegram outbox store moved from `paper/retailbot2.py` to
  `paper/outbox_store.py` (`OutboxStore`, `OUTBOX_LOCK_NAME`, the enqueue
  gates, and the entry/exit message formatters). `paper/outbox_worker.py`,
  `paper/report_scheduler.py`, and `web/routes/telegram_routes.py` import
  from there; the decoupling test
  (`tests/test_retailbot2_decoupling.py`) fails if they start importing
  `paper.retailbot2` again.
- `TELEGRAM_DELIVERY_ENABLED` is the canonical ceiling flag;
  `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` still works as a deprecated alias
  (see `docs/SECURITY.md` §5). The ceiling var was **not** fully renamed.

### Step 2 -- mirror, aliases, COMPOSITE guard (commits `fefd7ba3`, `20917731`)
- The 16 RetailBot2 strategies live in the strategy library
  (`backtest/strategies/retail_death_trap.py`) as the implementation of
  record; the 10 alias keys and the dead `backtest/strategy.py` are gone.
- Migration `040_retailbot2_alias_lifecycle_rename` renames the persisted
  `BB_REV_INV_inverse` / `DBL_PAT_INV_inverse` lifecycle rows to the
  canonical `*_INV` keys.
- Mirror fidelity was verified bar-for-bar against the live worker
  (`tests/test_retailbot2_mirror_fidelity.py`); the live-free properties
  are now pinned in `tests/test_retailbot2_mirror_guards.py`.
- `set_paper_config` rejects COMPOSITE / COMPOSITE_INV deployments and the
  paper evaluation loop skips them even if a row somehow exists. Backtest
  COMPOSITE stays research-only.
- RSI runs at effective 35/65 through the paper and backtest paths
  (`RB2_RSI_OVERSOLD` / `RB2_RSI_OVERBOUGHT`); the `RSI_OVERSOLD=30` /
  `RSI_OVERBOUGHT=70` keys in `BASE_PARAMS` are generic-strategy keys, not
  retail keys.

### Step 3 -- worker deleted, lifecycle writes retired (this PR)
- `paper/retailbot2.py` is **deleted** (whole file) and
  `tools/retailbot2_parity.py` is deleted with it -- there is no live
  implementation left to compare the mirror against.
- `run_all.py` has no `retailbot2` supervisor entry anymore;
  `RETAILBOT2_ENABLED` is ignored. `PAPER_REPORT_ENABLED` is the only gate
  for the hourly report (the `RETAILBOT2_ENABLED` fallback is gone).
- POST `/api/research/retailbot2/strategies` and POST
  `/api/research/retailbot2/control` are retired (the security boundary
  fails closed with 403 for writes to those paths). The GET routes stay as
  a **read-only archive**: positions, trades, stats, strategies, control,
  archive summary, and the `rdt_trades` / `rdt_strategy_stats` CSV exports.
- `db/db.py` `set_retailbot2_strategy_lifecycle_setting`,
  `set_retailbot2_control`, and the dead `get_retailbot2_outbox_counts`
  are deleted. The read helpers (`get_retailbot2_open_trades`,
  `get_retailbot2_recent_trades`, `get_retailbot2_stats`,
  `get_retailbot2_archive_summary`, `iter_retailbot2_table_batches`,
  `get_retailbot2_strategy_lifecycle_settings`, `get_retailbot2_control`)
  and `verify_retailbot2_schema()` stay -- the `rdt_*` tables are **not**
  dropped in this PR.
- The web startup archive-schema check (`verify_retailbot2_schema`,
  warn-only) now runs on every startup instead of only when
  `RETAILBOT2_ENABLED=1`.
- `/api/paper/deploy` maps `ValueError` from `set_paper_config` to 400,
  like `/api/paper/configs` (a COMPOSITE deploy used to surface as a 500).
- Outbox health/retention loggers are `telegram.outbox.*`
  (`retailbot2.auth` in `paper/outbox_store.py` is unchanged -- tests pin
  it -- and the `RETAILBOT2_*` enqueue/observation/production variable
  names in `paper/outbox_store.py` are unchanged by design; renaming them
  is a separate follow-up).
- The React Research panel is archive-only (no lifecycle management UI)
  and the legacy static UI renders the saved lifecycle rows read-only.
  The Settings panel label is now "Telegram outbox".

## Intentionally NOT removed yet
- `rdt_trades`, `rdt_strategy_stats`, `rdt_telegram_outbox`, `bot_control`,
  and `rdt_strategy_lifecycle` hold historical rows (the owner reported 122
  rows in the queried table(s)). No data is dropped in this PR. The tables
  are dropped only by a dedicated migration after the archive export below
  is verified.
- The `RETAILBOT2_*` enqueue/observation/production variable names in
  `paper/outbox_store.py` and the deprecated ceiling alias
  `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` are kept (separate follow-up).

## CMS deployment: backup + migration steps (run on the local MySQL server)

MySQL runs on the owner's local server, **not** in this VM and not in
Docker -- nothing here was checked against the live database. Run these
on the CMS host before and after pulling this PR.

**Before pulling (backup -- the rdt_* tables are not dropped, but the
worker that wrote them is being deleted, so export while the data is
known-good):**
1. Back up the live files outside the checkout first:
   `custom_strategies.json` and `Screen/signals.jsonl`.
2. Dump the archive tables (adjust credentials to the local server):
   ```bash
   mysqldump -h 127.0.0.1 -u <user> -p crypto_signals \
     rdt_trades rdt_strategy_stats rdt_telegram_outbox bot_control \
     rdt_strategy_lifecycle > rdt_archive_pre_retirement.sql
   ```
3. Export the CSVs as a second copy (Settings / Research archive view, or
   the API while the server still runs the old code):
   `GET /api/research/retailbot2/archive/trades.csv` and
   `GET /api/research/retailbot2/archive/stats.csv`.
4. Note the current `rdt_telegram_outbox` pending count
   (`GET /api/telegram/status`) -- after the upgrade, the standalone
   outbox worker must drain it to zero before the old rows are trusted as
   delivered.

**After pulling:**
1. Re-check the branch head is still the reviewed SHA and CI is green
   before deploying (same gate as the earlier upgrade).
2. Apply migrations as a separate authorized step (never auto-migrated at
   startup):
   ```bash
   python -m db.migrate
   ```
   This applies migration `040_retailbot2_alias_lifecycle_rename` (plus
   any later migrations). Verify `rdt_strategy_lifecycle` has no
   `*_inverse` rows left:
   ```sql
   SELECT strategy_id FROM rdt_strategy_lifecycle
   WHERE strategy_id LIKE '%_inverse';
   -- expect 0 rows
   ```
3. Restart the supervisor (`run_all.py`). Confirm the process list has
   **no** `retailbot2` entry and the outbox worker (`retailbot2_outbox`)
   is still present when enabled.
4. Post-pull checks:
   - `GET /api/research/retailbot2/archive/summary` returns the same
     counts as the pre-pull export; the CSV exports download.
   - `POST /api/research/retailbot2/strategies` and
     `POST /api/research/retailbot2/control` are rejected (403).
   - `GET /api/telegram/status` shows the outbox draining; the hourly
     paper report still arrives through the outbox.
   - `GET /api/paper/deploy` with `strategy_name: "COMPOSITE"` returns
     400, not 500.
5. Do **not** delete the `rdt_*` tables yet -- that is a separate,
   reviewed migration after the archive export is verified restorable.

## To finish retirement (separate, reviewed work)
1. **Workflow fix (required before merge, not done here):**
   `.github/workflows/retailbot-accounting.yml` still lists the deleted
   `tests/test_retailbot_accounting.py` in its pytest invocation -- that
   job will fail at collection until the filename is removed from the
   list. Workflow edits are out of scope for this PR (they need a local
   agent / work order, not the GitHub App); apply that one-line removal
   separately.
2. Rename the remaining `RETAILBOT2_*` enqueue/observation/production
   variable names in `paper/outbox_store.py` to neutral `TELEGRAM_OUTBOX_*`
   names (keeping `RETAILBOT2_*` as read-only aliases for one release),
   and fully retire the deprecated ceiling alias
   `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY`.
3. Export/archive `rdt_*` history, verify the export restores, then remove
   the tables and the read-only archive routes via a dedicated migration.
