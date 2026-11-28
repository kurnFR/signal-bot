-- Migration 041: remove the retired RetailBot2 `rdt_*` tables for good.
--
-- What it does
--   1. Renames `rdt_telegram_outbox` to `telegram_outbox` (same columns,
--      same rows, same indexes -- only the names change). The Telegram
--      outbox is a live service: `paper/outbox_store.py` (OutboxStore),
--      `paper/outbox_worker.py`, `paper/report_scheduler.py`,
--      `web/routes/telegram_routes.py` (`GET /api/telegram/status`),
--      `paper/outbox_health.py`, `paper/outbox_retention.py` and
--      `tools/outbox_status.py` all read/write the renamed table after
--      this PR. No outbox row is lost: pending/processing rows keep their
--      claim tokens and leases across the rename, so the worker drains
--      them after the deploy instead of re-enqueueing.
--   2. Drops `rdt_trades`, `rdt_strategy_stats`, `rdt_strategy_lifecycle`
--      and `bot_control`. Their readers are repointed in this PR, not
--      deleted blindly:
--        - trade history now comes from `paper_trades` (the canonical
--          ledger written by `paper/engine.py` on every close) and open
--          positions from `paper_positions`, with the per-deployment
--          `paper_configs.report_enabled` filter replacing the old
--          `rdt_strategy_lifecycle.report_enabled` filter
--          (`db/db.py`: `get_paper_open_positions`,
--          `get_paper_recent_trades`, `get_paper_trade_stats`,
--          `get_paper_archive_summary`, `iter_paper_archive_batches`);
--          the `/api/research/retailbot2/*` URLs are unchanged.
--        - `GET /api/research/retailbot2/strategies` and
--          `GET /api/research/retailbot2/control` are deleted together
--          with their UI calls: the lifecycle flags they served now live
--          in `paper_configs` (per symbol / market / timeframe /
--          strategy) and are managed by the Paper Trading UI, and
--          `bot_control` had no live reader left (the worker that polled
--          it is deleted). Their POST counterparts were already retired
--          (403) and stay retired.
--        - `scripts/migrate_retailbot2_lifecycle.py` is deleted: it only
--          read `rdt_strategy_lifecycle` to map flags into
--          `paper_configs`, and that source table is gone.
--   3. Historical `rdt_*` rows are NOT migrated into `paper_trades`:
--      different schema, and the `rdt_trades` rows are shadow/legacy rows,
--      not paper-engine trades. The archive lives on only in the
--      pre-migration export (see below).
--
-- PRE-MIGRATION PREREQUISITE -- DO NOT SKIP (run on the CMS host first):
--   1. Back up the live files outside the checkout:
--      `custom_strategies.json` and `Screen/signals.jsonl`.
--   2. Dump the tables that are about to disappear:
--        mysqldump -h 127.0.0.1 -u <user> -p crypto_signals \
--          rdt_trades rdt_strategy_stats rdt_telegram_outbox bot_control \
--          rdt_strategy_lifecycle > rdt_archive_pre_removal.sql
--   3. Export the archive CSVs as a second copy (Settings / Research
--      archive view, or the API while the server still runs the old
--      code):
--        GET /api/research/retailbot2/archive/trades.csv
--        GET /api/research/retailbot2/archive/stats.csv
--   4. Verify the dump restores (import it into a scratch database and
--      compare row counts) and verify the CSVs open. Only then apply.
--   5. Note the `telegram_outbox` pending/processing counts
--      (`GET /api/telegram/status`) -- after the upgrade the standalone
--      outbox worker must drain them; the rename preserves the rows.
--
-- Deploy order (code + migration go together):
--   Apply this migration and deploy the new code in one maintenance
--   window, then restart the supervisor (`run_all.py`) immediately.
--   The old code references `rdt_telegram_outbox`, the new code
--   references `telegram_outbox`: between the migration and the restart
--   the outbox worker fails closed (schema preflight) and enqueue
--   attempts return False and log -- nothing is written to a wrong
--   table. Do NOT apply this migration while the old code is still
--   running unattended, and do NOT deploy the new code before the
--   migration: both directions are fail-closed, not silent.
--
-- Fail-closed guard (rename target already exists):
--   If `telegram_outbox` already exists while `rdt_telegram_outbox`
--   still exists, the migration aborts BEFORE renaming or dropping
--   anything instead of silently merging/overwriting two queues. This
--   is enforced with a temporary table carrying a CHECK constraint:
--   the guard INSERT stores 0 exactly in that state, MariaDB rejects
--   it with CHECK-constraint error 4025, and the migration runner
--   (which tolerates only "already applied" errnos) aborts the run and
--   records nothing. Operator action then: inspect both tables, decide
--   which one holds the live queue, drop the spurious empty one, and
--   re-run. The guard is a no-op in the normal state (only the source
--   table exists) and in the already-renamed state (only the target
--   exists).
--
-- Re-run safety / crash window:
--   `DROP TABLE IF EXISTS` is idempotent, and the index renames run
--   before the table rename so a failure there leaves nothing changed.
--   The one non-re-runnable step is the atomic `RENAME TABLE` itself:
--   if the connection dies after the rename but before the migration
--   is recorded, re-running fails at the rename with error 1146
--   (table `rdt_telegram_outbox` does not exist). That state is
--   unambiguous -- `telegram_outbox` exists, `rdt_telegram_outbox`
--   does not -- so record the migration manually instead of forcing
--   the rename:
--     python -c "from db.migrate import checksum; print(checksum(open('db/migrate_remove_rdt_tables.sql', encoding='utf-8').read()))"
--     INSERT INTO schema_migrations (version, filename, checksum, status)
--       VALUES ('041_remove_rdt_tables', 'migrate_remove_rdt_tables.sql', '<checksum above>', 'applied');
--   then run the remaining `DROP TABLE IF EXISTS` statements by hand if
--   they did not run yet, and verify with the post-apply checks below.
--
-- Index renames require MariaDB >= 10.5.2 (`ALTER TABLE ... RENAME
-- INDEX` / `RENAME KEY`). If they fail on an older server, delete those
-- three statements and re-run: the old index names keep working, they
-- are cosmetic only, and nothing has been renamed yet at that point.
--
-- Verification SQL (run AFTER applying -- all must hold):
--   SHOW TABLES LIKE 'rdt\_%';            -- expect 0 rows
--   SHOW TABLES LIKE 'telegram_outbox';    -- expect 1 row
--   SELECT status, COUNT(*) FROM telegram_outbox GROUP BY status;
--     -- expect the same counts as the pre-migration rdt_telegram_outbox
--   SELECT COUNT(*) FROM paper_trades;     -- unchanged by this migration
--   SELECT version FROM schema_migrations WHERE version = '041_remove_rdt_tables';
--     -- expect 1 row
--
-- Historical migrations 022 (`migrate_retailbot2_runtime_schema.sql`),
-- 026 (`migrate_retailbot2_telegram_outbox.sql`), 027
-- (`migrate_retailbot2_outbox_claim_token.sql`), 028
-- (`migrate_retailbot2_safe_control_defaults.sql`) and 040
-- (`migrate_retailbot2_alias_lifecycle_rename.sql`) are intentionally
-- left untouched: they are the history of how these tables came to be,
-- and the runner flags edited applied migrations as "changed".

-- ---------------------------------------------------------------------------
-- Fail closed if the rename target already exists alongside the source.
-- ---------------------------------------------------------------------------

CREATE TEMPORARY TABLE _rdt_removal_guard (ok TINYINT NOT NULL, CHECK (ok = 1));

INSERT INTO _rdt_removal_guard (ok)
SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables
                          WHERE table_schema = DATABASE() AND table_name = 'telegram_outbox')
                 AND EXISTS (SELECT 1 FROM information_schema.tables
                              WHERE table_schema = DATABASE() AND table_name = 'rdt_telegram_outbox')
            THEN 0 ELSE 1 END;

-- ---------------------------------------------------------------------------
-- Rename the outbox indexes to the new table name (cosmetic; runs before
-- the table rename so a failure here leaves nothing changed).
-- ---------------------------------------------------------------------------

ALTER TABLE rdt_telegram_outbox RENAME INDEX idx_rdt_telegram_outbox_due TO idx_telegram_outbox_due;
ALTER TABLE rdt_telegram_outbox RENAME KEY uk_rdt_telegram_outbox_event TO uk_telegram_outbox_event;
ALTER TABLE rdt_telegram_outbox RENAME KEY idx_rdt_telegram_outbox_trade TO idx_telegram_outbox_trade;

-- ---------------------------------------------------------------------------
-- Rename the live outbox table (atomic; preserves every row, including
-- pending/processing rows with their claim tokens and leases).
-- ---------------------------------------------------------------------------

RENAME TABLE rdt_telegram_outbox TO telegram_outbox;

-- ---------------------------------------------------------------------------
-- Drop the retired archive/control tables. Their readers were repointed
-- to paper_trades / paper_positions / paper_configs in this PR; nothing
-- in live code references them after the rename above.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS rdt_trades, rdt_strategy_stats, rdt_strategy_lifecycle, bot_control;

DROP TEMPORARY TABLE _rdt_removal_guard;
