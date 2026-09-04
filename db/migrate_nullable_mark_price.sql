-- ============================================================================
-- Migration: make funding_rate.mark_price nullable. Binance's historical
-- funding rate endpoint sometimes omits markPrice (returns an empty
-- string) -- the column was NOT NULL, which rejected those rows entirely
-- and silently failed the whole backfill for a symbol on the first such
-- record. Safe to run once.
-- ============================================================================

USE crypto_signals;

ALTER TABLE funding_rate
    MODIFY COLUMN mark_price DECIMAL(24,10) NULL;
