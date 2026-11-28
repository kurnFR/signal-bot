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
  (Superseded by Step 4 below: migration 041 drops the `rdt_*` tables,
  the strategies/control GETs are deleted, and the archive reads
  `paper_trades`.)
- `db/db.py` `set_retailbot2_strategy_lifecycle_setting`,
  `set_retailbot2_control`, and the dead `get_retailbot2_outbox_counts`
  are deleted. The read helpers (`get_retailbot2_open_trades`,
  `get_retailbot2_recent_trades`, `get_retailbot2_stats`,
  `get_retailbot2_archive_summary`, `iter_retailbot2_table_batches`,
  `get_retailbot2_strategy_lifecycle_settings`, `get_retailbot2_control`)
  and `verify_retailbot2_schema()` stay -- the `rdt_*` tables are **not**
  dropped in this PR. (Superseded by Step 4 below: the helpers are
  renamed to `get_paper_*` / `verify_paper_archive_schema()` and the
  lifecycle/control readers are deleted with the tables.)
- The web startup archive-schema check (`verify_retailbot2_schema`,
  warn-only) now runs on every startup instead of only when
  `RETAILBOT2_ENABLED=1`. (Renamed to `verify_paper_archive_schema` in
  Step 4.)
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

### Step 4 -- rdt_* tables dropped, outbox renamed (migration 041)
- Migration `041_remove_rdt_tables` (`db/migrate_remove_rdt_tables.sql`)
  renames `rdt_telegram_outbox` to `telegram_outbox` (same columns, same
  rows, same indexes -- only the names change; the advisory lock is
  renamed to `telegram_outbox_worker_lock`) and drops `rdt_trades`,
  `rdt_strategy_stats`, `rdt_strategy_lifecycle` and `bot_control`.
  Historical `rdt_*` rows are NOT migrated into `paper_trades` (different
  schema, shadow/legacy rows) -- the archive lives on only in the
  pre-migration export (see below).
- `paper/outbox_store.py`, `paper/outbox_worker.py`,
  `paper/outbox_health.py`, `paper/outbox_retention.py`,
  `tools/outbox_status.py` and the `GET /api/telegram/status` path move
  to `telegram_outbox` with no behaviour change. The rename is guarded:
  if `telegram_outbox` already exists alongside `rdt_telegram_outbox`
  the migration aborts before renaming or dropping anything (temporary
  table + CHECK constraint, error 4025) instead of silently merging two
  queues.
- The Research archive reads `paper_positions` / `paper_trades` now
  (`db/db.py`: `get_paper_open_positions`, `get_paper_recent_trades`,
  `get_paper_trade_stats`, `get_paper_archive_summary`,
  `iter_paper_archive_batches`), with the per-deployment
  `paper_configs.report_enabled` filter replacing the old
  `rdt_strategy_lifecycle.report_enabled` filter. The
  `/api/research/retailbot2/*` URLs are unchanged; `mode` in every
  response is now always `"paper"`.
- `GET /api/research/retailbot2/strategies` and
  `GET /api/research/retailbot2/control` are deleted together with their
  UI calls (the Research panel's lifecycle section is gone): the flags
  they served now live in `paper_configs` and are managed by the Paper
  Trading UI, and `bot_control` had no live reader left. Their POST
  counterparts stay retired (403). `scripts/migrate_retailbot2_lifecycle.py`
  is deleted -- it only read `rdt_strategy_lifecycle`.
- The retired separate `retailbot2` connection pool and
  `RETAILBOT2_DB_CONFIG` are removed; everything reads through the
  shared `get_pool()` / `MYSQL_CONFIG`. Web startup now verifies the
  Paper Trading schema (`verify_paper_archive_schema`: `paper_trades`,
  `paper_positions`, `paper_configs`, `telegram_outbox`).

## Intentionally NOT removed yet
- The `RETAILBOT2_*` enqueue/observation/production variable names in
  `paper/outbox_store.py` and the deprecated ceiling alias
  `RETAILBOT2_ALLOW_TELEGRAM_DELIVERY` are kept (separate follow-up).
- The `UI_SHOW_RETAILBOT2_HISTORY` UI flag and the `/api/research/retailbot2/*`
  URL prefix are kept (the flag gates the Research workspace group; the
  URLs stay stable so the UI change is minimal). Neither references a table.
- `.github/actions/provision-mariadb/action.yml` still grants privileges
  on the four dropped `rdt_*` tables (workflow files are out of scope
  for this PR -- the grants are harmless on non-existent tables but dead;
  a local agent should replace `rdt_telegram_outbox` with
  `telegram_outbox` there and drop the other three grants).

## CMS deployment: backup + migration steps (migration 041)

MySQL runs on the owner's local server, **not** in this VM and not in
Docker -- nothing here was checked against the live database. Run these
on the CMS host before and after pulling this PR.

**Before pulling (backup -- the rdt_* tables ARE dropped by this PR, so
export while the data is known-good):**
1. Back up the live files outside the checkout first:
   `custom_strategies.json` and `Screen/signals.jsonl`.
2. Dump the tables that are about to disappear (adjust credentials to
   the local server):
   ```bash
   mysqldump -h 127.0.0.1 -u <user> -p crypto_signals \
     rdt_trades rdt_strategy_stats rdt_telegram_outbox bot_control \
     rdt_strategy_lifecycle > rdt_archive_pre_removal.sql
   ```
3. Export the CSVs as a second copy (Settings / Research archive view, or
   the API while the server still runs the old code):
   `GET /api/research/retailbot2/archive/trades.csv` and
   `GET /api/research/retailbot2/archive/stats.csv`.
4. Verify the dump restores (import it into a scratch database and
   compare row counts) and verify the CSVs open. Only then apply.
5. Note the `rdt_telegram_outbox` pending/processing counts
   (`GET /api/telegram/status`) -- after the upgrade the standalone
   outbox worker must drain the renamed `telegram_outbox` to zero before
   the old rows are trusted as delivered. The rename preserves the rows.

**After pulling:**
1. Re-check the branch head is still the reviewed SHA and CI is green
   before deploying (same gate as the earlier upgrade).
2. Deploy the new code AND apply the migration in one maintenance
   window, then restart the supervisor (`run_all.py`) immediately:
   ```bash
   python -m db.migrate
   ```
   This applies migration `041_remove_rdt_tables`. The old code
   references `rdt_telegram_outbox` and the new code references
   `telegram_outbox`: between the migration and the restart the outbox
   worker fails closed (schema preflight) and enqueue attempts return
   False and log -- nothing is written to a wrong table. Do NOT apply
   the migration while the old code is still running unattended, and do
   NOT deploy the new code before the migration.
3. Verify:
   ```sql
   SHOW TABLES LIKE 'rdt\_%';            -- expect 0 rows
   SHOW TABLES LIKE 'telegram_outbox';    -- expect 1 row
   SELECT status, COUNT(*) FROM telegram_outbox GROUP BY status;
   -- expect the same counts as the pre-migration rdt_telegram_outbox
   SELECT COUNT(*) FROM paper_trades;     -- unchanged by this migration
   ```
   `python -m db.migrate` a second time must report "Database is up to
   date" (idempotent).
4. Post-pull checks:
   - `GET /api/research/retailbot2/archive/summary` returns the
     paper_trades counts; the CSV exports download as
     `paper_trades_archive.csv` / `paper_strategy_stats_archive.csv`.
   - `GET /api/research/retailbot2/stats` returns `by_mode.paper`
     (no more `shadow` / `inverse` keys).
   - `GET /api/research/retailbot2/strategies` and
     `GET /api/research/retailbot2/control` return 404; their POSTs are
     still rejected (403).
   - `GET /api/telegram/status` shows the outbox draining; the hourly
     paper report still arrives through the outbox.
   - The Research panel and the Settings archive section load and show
     Paper Trading data.
5. If the migration fails after the `RENAME TABLE` (e.g. connection
   loss before it is recorded), re-running fails at the rename with
   error 1146 -- that state is unambiguous (`telegram_outbox` exists,
   `rdt_telegram_outbox` does not), so record the migration manually
   instead of forcing the rename; see the recovery note in the
   migration file header.

## CMS deployment (PR #821 record -- superseded by Step 4 above)

The steps below are the record of the PR #821 deployment (worker
deletion, migration 040). They are kept for history; the current
backup + migration steps for the `rdt_*` removal are in the Step 4
section above. In particular, item 5 below ("do not delete the `rdt_*`
tables yet") is superseded: migration 041 deletes them.

MySQL runs on the owner's local server, **not** in this VM and not in
Docker -- nothing here was checked against the live database.

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
3. ~~Export/archive `rdt_*` history, verify the export restores, then remove
   the tables and the read-only archive routes via a dedicated migration.~~
   **Done in Step 4 above** (migration 041; the archive routes now read
   `paper_trades` instead of the dropped tables).
4. Update `.github/actions/provision-mariadb/action.yml` (workflow files
   are out of scope for this PR): replace the `rdt_telegram_outbox`
   grant with `telegram_outbox` and drop the grants on `rdt_trades`,
   `rdt_strategy_stats` and `rdt_strategy_lifecycle`.
