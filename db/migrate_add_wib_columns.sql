-- ============================================================================
-- Migration: add Asia/Jakarta (WIB, UTC+7) generated datetime columns to
-- tables that already exist from a previous schema run. Safe to run once --
-- re-running will error "duplicate column" if already applied (harmless,
-- just means it's already done).
-- ============================================================================

USE crypto_signals;

ALTER TABLE ohlcv
    ADD COLUMN open_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD(FROM_UNIXTIME(open_time/1000), INTERVAL 7 HOUR)) VIRTUAL AFTER open_time,
    ADD COLUMN close_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD(FROM_UNIXTIME(close_time/1000), INTERVAL 7 HOUR)) VIRTUAL AFTER close_time;

ALTER TABLE funding_rate
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD(FROM_UNIXTIME(event_time/1000), INTERVAL 7 HOUR)) VIRTUAL AFTER event_time;

ALTER TABLE open_interest
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD(FROM_UNIXTIME(event_time/1000), INTERVAL 7 HOUR)) VIRTUAL AFTER event_time;

ALTER TABLE liquidations
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD(FROM_UNIXTIME(event_time/1000), INTERVAL 7 HOUR)) VIRTUAL AFTER event_time;
