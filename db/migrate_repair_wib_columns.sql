-- Repair partially applied migration 010.
--
-- Migration 010 adds two generated columns to ohlcv in one ALTER statement.
-- If one column already exists, a duplicate-column error can prevent the other
-- column from being added while the runner records the statement as idempotent.
-- Keep each addition in its own statement so an existing column cannot block
-- the missing sibling. The runner tolerates duplicate-column errors per statement.
--
-- Fresh installs already have these columns in schema.sql; upgrade_only skips
-- this repair for a fresh baseline, but applies it to existing databases.

USE crypto_signals;

ALTER TABLE ohlcv
    ADD COLUMN open_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((open_time DIV 1000) + 25200) SECOND)) VIRTUAL AFTER open_time;

ALTER TABLE ohlcv
    ADD COLUMN close_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((close_time DIV 1000) + 25200) SECOND)) VIRTUAL AFTER close_time;

ALTER TABLE funding_rate
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL AFTER event_time;

ALTER TABLE open_interest
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL AFTER event_time;

ALTER TABLE liquidations
    ADD COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL AFTER event_time;
