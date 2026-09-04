-- ============================================================================
-- Migration: fix WIB (Asia/Jakarta) generated columns.
--
-- Bug: the original expression used FROM_UNIXTIME(), whose output depends
-- on the MySQL session's active time_zone. If that session tz is already
-- WIB (common when the server's OS timezone is Asia/Jakarta and MySQL's
-- time_zone is set to 'SYSTEM'), the column ended up double-shifted +14h
-- from UTC instead of the intended +7h.
--
-- Fix: compute directly from the Unix epoch using pure calendar arithmetic
-- ('1970-01-01' + INTERVAL seconds), which is NOT affected by session
-- timezone at all -- gives the correct WIB time regardless of how the
-- server or the client session is configured.
--
-- Safe to run multiple times.
-- ============================================================================

USE crypto_signals;

ALTER TABLE ohlcv
    MODIFY COLUMN open_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((open_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    MODIFY COLUMN close_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((close_time DIV 1000) + 25200) SECOND)) VIRTUAL;

ALTER TABLE funding_rate
    MODIFY COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL;

ALTER TABLE open_interest
    MODIFY COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL;

ALTER TABLE liquidations
    MODIFY COLUMN event_time_wib DATETIME GENERATED ALWAYS AS
        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL;
