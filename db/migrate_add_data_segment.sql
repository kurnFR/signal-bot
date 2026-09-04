-- ============================================================================
-- Migration: add data_segment column to backtest_runs (train/holdout split
-- introduced in Step 4). Safe to run once -- errors harmlessly with
-- "duplicate column" if already applied.
-- ============================================================================

USE crypto_signals;

ALTER TABLE backtest_runs
    ADD COLUMN data_segment ENUM('train','holdout','full') NOT NULL DEFAULT 'train' AFTER strategy_name;
