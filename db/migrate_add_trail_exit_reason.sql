-- ============================================================================
-- Migration: add 'TRAIL' to backtest_trades.exit_reason enum (trailing-stop
-- support added to the simulation engine). Safe to run once.
-- ============================================================================

USE crypto_signals;

ALTER TABLE backtest_trades
    MODIFY COLUMN exit_reason ENUM('SL','TP','TIMEOUT','END_OF_DATA','TRAIL') NOT NULL;
