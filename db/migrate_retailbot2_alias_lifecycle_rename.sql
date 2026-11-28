-- Migration 040: rename persisted RetailBot2 alias lifecycle rows to canonical ids.
--
-- Background (RetailBot2 migration step 1): the strategy registry
-- (backtest/strategies/retail_death_trap.py, formerly
-- retailbot2_strategies.py) dropped its 10 legacy alias keys. Two of them
-- can be persisted in the database as rdt_strategy_lifecycle.strategy_id
-- values: 'BB_REV_INV_inverse' and 'DBL_PAT_INV_inverse'. Their canonical
-- ids are 'BB_REV_INV' and 'DBL_PAT_INV'. This migration renames the
-- persisted rows so the user's saved evaluation/paper/telegram/report
-- choices survive under the canonical ids.
--
-- Collision safety: rdt_strategy_lifecycle has PRIMARY KEY
-- (strategy_id, mode), and rdt_strategy_stats has UNIQUE KEY
-- uk_strat_mode (strategy, mode), so a plain UPDATE ... SET
-- strategy_id = '<canonical>' would fail with duplicate-key error 1062
-- wherever a canonical row already exists for the same mode -- and 1062
-- is NOT a tolerated errno in db/migrate.py. Each pair below therefore
-- runs in three steps, per (alias -> canonical) pair and per mode:
--   1. MERGE the alias row's flags/counters into the canonical row where
--      a canonical row already exists for the same mode.
--   2. DELETE only the alias rows that collided (same mode as a canonical
--      row).
--   3. UPDATE the remaining alias rows to the canonical id (no canonical
--      row exists for those modes, so no collision is possible).
-- The whole file is a no-op when no alias rows exist, and re-running it
-- after a successful apply changes nothing.
--
-- Merge semantics for rdt_strategy_lifecycle: the four user-facing flags
-- (evaluation/paper/telegram/report) are OR-ed, so a choice enabled under
-- either id stays enabled. auto_promote_enabled is intentionally NOT
-- merged: the alias row's auto-promote is treated as FALSE and the
-- canonical row's value is preserved. A plain rename (step 3, no
-- canonical row exists) keeps all flags, including auto_promote_enabled,
-- exactly as the user saved them.
--
-- Defensive sections: rdt_trades.strategy and rdt_strategy_stats.strategy
-- hold canonical strategy ids plus a separate mode column in live code
-- (paper/retailbot2.py writes signal['strategy'], with '_INV' appended
-- for inverse signals -- the '_inverse' suffix is never produced), so
-- alias values are not expected there. The statements below are included
-- only as defence in depth; they are expected no-ops. rdt_trades has no
-- unique key on strategy, so a plain UPDATE is collision-safe there;
-- rdt_strategy_stats counters are merged (summed) before the rename,
-- mirroring the lifecycle pattern.
--
-- Tables referenced here are created by migration 022
-- (migrate_retailbot2_runtime_schema.sql), which always runs before 040.
--
-- DO NOT run against a live database blindly: back up first
-- (mysqldump the rdt_strategy_lifecycle, rdt_trades and
-- rdt_strategy_stats tables), then apply with `python -m db.migrate`.
--
-- Verification SQL (run BEFORE applying, on the target database):
--   SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
--          telegram_enabled, report_enabled, auto_promote_enabled
--     FROM rdt_strategy_lifecycle
--    WHERE strategy_id IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse')
--    ORDER BY strategy_id, mode;
--   SELECT strategy, mode, COUNT(*) AS rows
--     FROM rdt_trades
--    WHERE strategy IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse')
--    GROUP BY strategy, mode;
--   SELECT strategy, mode, total_trades
--     FROM rdt_strategy_stats
--    WHERE strategy IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse')
--    ORDER BY strategy, mode;
-- Verification SQL (run AFTER applying -- all three must return 0 rows):
--   SELECT strategy_id, mode FROM rdt_strategy_lifecycle
--    WHERE strategy_id IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse');
--   SELECT strategy, mode FROM rdt_trades
--    WHERE strategy IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse') LIMIT 1;
--   SELECT strategy, mode FROM rdt_strategy_stats
--    WHERE strategy IN ('BB_REV_INV_inverse', 'DBL_PAT_INV_inverse');
-- And the renamed rows must exist with the merged flags:
--   SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
--          telegram_enabled, report_enabled, auto_promote_enabled
--     FROM rdt_strategy_lifecycle
--    WHERE strategy_id IN ('BB_REV_INV', 'DBL_PAT_INV')
--    ORDER BY strategy_id, mode;

-- ---------------------------------------------------------------------------
-- rdt_strategy_lifecycle: BB_REV_INV_inverse -> BB_REV_INV
-- ---------------------------------------------------------------------------

-- 1. Merge alias flags into the canonical row where one exists (same mode).
UPDATE rdt_strategy_lifecycle AS c
JOIN rdt_strategy_lifecycle AS a
  ON a.strategy_id = 'BB_REV_INV_inverse'
 AND c.strategy_id = 'BB_REV_INV'
 AND a.mode = c.mode
SET c.evaluation_enabled = c.evaluation_enabled OR a.evaluation_enabled,
    c.paper_enabled = c.paper_enabled OR a.paper_enabled,
    c.telegram_enabled = c.telegram_enabled OR a.telegram_enabled,
    c.report_enabled = c.report_enabled OR a.report_enabled;
-- NOTE: c.auto_promote_enabled is deliberately untouched (alias
-- auto-promote treated as FALSE; canonical value preserved).

-- 2. Delete only the alias rows that collided with a canonical row.
DELETE a FROM rdt_strategy_lifecycle AS a
JOIN rdt_strategy_lifecycle AS c
  ON c.strategy_id = 'BB_REV_INV'
 AND c.mode = a.mode
WHERE a.strategy_id = 'BB_REV_INV_inverse';

-- 3. Rename any remaining alias rows (no canonical row for those modes).
UPDATE rdt_strategy_lifecycle
SET strategy_id = 'BB_REV_INV'
WHERE strategy_id = 'BB_REV_INV_inverse';

-- ---------------------------------------------------------------------------
-- rdt_strategy_lifecycle: DBL_PAT_INV_inverse -> DBL_PAT_INV
-- ---------------------------------------------------------------------------

UPDATE rdt_strategy_lifecycle AS c
JOIN rdt_strategy_lifecycle AS a
  ON a.strategy_id = 'DBL_PAT_INV_inverse'
 AND c.strategy_id = 'DBL_PAT_INV'
 AND a.mode = c.mode
SET c.evaluation_enabled = c.evaluation_enabled OR a.evaluation_enabled,
    c.paper_enabled = c.paper_enabled OR a.paper_enabled,
    c.telegram_enabled = c.telegram_enabled OR a.telegram_enabled,
    c.report_enabled = c.report_enabled OR a.report_enabled;
-- NOTE: c.auto_promote_enabled is deliberately untouched (see above).

DELETE a FROM rdt_strategy_lifecycle AS a
JOIN rdt_strategy_lifecycle AS c
  ON c.strategy_id = 'DBL_PAT_INV'
 AND c.mode = a.mode
WHERE a.strategy_id = 'DBL_PAT_INV_inverse';

UPDATE rdt_strategy_lifecycle
SET strategy_id = 'DBL_PAT_INV'
WHERE strategy_id = 'DBL_PAT_INV_inverse';

-- ---------------------------------------------------------------------------
-- Defensive: rdt_trades.strategy (no unique key on strategy -- plain
-- UPDATE is collision-safe; expected no-op, see header).
-- ---------------------------------------------------------------------------

UPDATE rdt_trades
SET strategy = 'BB_REV_INV'
WHERE strategy = 'BB_REV_INV_inverse';

UPDATE rdt_trades
SET strategy = 'DBL_PAT_INV'
WHERE strategy = 'DBL_PAT_INV_inverse';

-- ---------------------------------------------------------------------------
-- Defensive: rdt_strategy_stats.strategy (UNIQUE KEY (strategy, mode) --
-- merge counters, delete collided rows, then rename; expected no-op).
-- ---------------------------------------------------------------------------

UPDATE rdt_strategy_stats AS c
JOIN rdt_strategy_stats AS a
  ON a.strategy = 'BB_REV_INV_inverse'
 AND c.strategy = 'BB_REV_INV'
 AND a.mode = c.mode
SET c.total_trades = c.total_trades + a.total_trades,
    c.wins = c.wins + a.wins,
    c.losses = c.losses + a.losses,
    c.total_pnl = c.total_pnl + a.total_pnl;

DELETE a FROM rdt_strategy_stats AS a
JOIN rdt_strategy_stats AS c
  ON c.strategy = 'BB_REV_INV'
 AND c.mode = a.mode
WHERE a.strategy = 'BB_REV_INV_inverse';

UPDATE rdt_strategy_stats
SET strategy = 'BB_REV_INV'
WHERE strategy = 'BB_REV_INV_inverse';

UPDATE rdt_strategy_stats AS c
JOIN rdt_strategy_stats AS a
  ON a.strategy = 'DBL_PAT_INV_inverse'
 AND c.strategy = 'DBL_PAT_INV'
 AND a.mode = c.mode
SET c.total_trades = c.total_trades + a.total_trades,
    c.wins = c.wins + a.wins,
    c.losses = c.losses + a.losses,
    c.total_pnl = c.total_pnl + a.total_pnl;

DELETE a FROM rdt_strategy_stats AS a
JOIN rdt_strategy_stats AS c
  ON c.strategy = 'DBL_PAT_INV'
 AND c.mode = a.mode
WHERE a.strategy = 'DBL_PAT_INV_inverse';

UPDATE rdt_strategy_stats
SET strategy = 'DBL_PAT_INV'
WHERE strategy = 'DBL_PAT_INV_inverse';
