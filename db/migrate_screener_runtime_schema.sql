-- Smart Money Screener runtime schema is migration-managed.
-- Runtime startup performs read-only information_schema verification only.

CREATE TABLE IF NOT EXISTS smart_money_signals (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    signal_type VARCHAR(30) NOT NULL,
    rvol DECIMAL(12,4) NOT NULL,
    volume DECIMAL(30,8) NOT NULL,
    quote_volume DECIMAL(20,2) NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    price_change_pct DECIMAL(10,4) NOT NULL,
    volume_velocity DECIMAL(12,4) NOT NULL,
    quality_score DECIMAL(6,2) NOT NULL DEFAULT 0,
    candle_time VARCHAR(32) NOT NULL,
    telegram_sent BOOLEAN NOT NULL DEFAULT FALSE,
    direction VARCHAR(10) NOT NULL DEFAULT 'NEUTRAL',
    taker_buy_ratio DECIMAL(8,6) NULL,
    htf_alignment TINYINT NOT NULL DEFAULT 0,
    htf_context VARCHAR(64) NOT NULL DEFAULT 'UNAVAILABLE',
    delivery_status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    delivery_attempts INT NOT NULL DEFAULT 0,
    next_attempt_at DATETIME NULL,
    delivery_updated_at DATETIME NULL,
    delivered_at DATETIME NULL,
    last_delivery_error VARCHAR(500) NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_smsig_symbol_time (symbol, detected_at),
    INDEX idx_smsig_type_time (signal_type, detected_at),
    UNIQUE KEY uq_smsig_candle (symbol, signal_type, candle_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE smart_money_signals
    ADD COLUMN quality_score DECIMAL(6,2) NOT NULL DEFAULT 0;
ALTER TABLE smart_money_signals
    ADD COLUMN direction VARCHAR(10) NOT NULL DEFAULT 'NEUTRAL';
ALTER TABLE smart_money_signals
    ADD COLUMN taker_buy_ratio DECIMAL(8,6) NULL;
ALTER TABLE smart_money_signals
    ADD COLUMN htf_alignment TINYINT NOT NULL DEFAULT 0;
ALTER TABLE smart_money_signals
    ADD COLUMN htf_context VARCHAR(64) NOT NULL DEFAULT 'UNAVAILABLE';
ALTER TABLE smart_money_signals
    ADD COLUMN delivery_status VARCHAR(16) NOT NULL DEFAULT 'PENDING';
ALTER TABLE smart_money_signals
    ADD COLUMN delivery_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE smart_money_signals
    ADD COLUMN next_attempt_at DATETIME NULL;
ALTER TABLE smart_money_signals
    ADD COLUMN delivery_updated_at DATETIME NULL;
ALTER TABLE smart_money_signals
    ADD COLUMN delivered_at DATETIME NULL;
ALTER TABLE smart_money_signals
    ADD COLUMN last_delivery_error VARCHAR(500) NULL;
ALTER TABLE smart_money_signals
    MODIFY COLUMN candle_time VARCHAR(32) NOT NULL;

-- Preserve historical Telegram delivery state when upgrading legacy rows.
UPDATE smart_money_signals
SET delivery_status = 'SENT'
WHERE telegram_sent = TRUE AND delivery_status = 'PENDING';
UPDATE smart_money_signals
SET delivered_at = detected_at
WHERE telegram_sent = TRUE AND delivered_at IS NULL;

CREATE TABLE IF NOT EXISTS screener_control (
    id INT PRIMARY KEY DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    overrides_json TEXT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT chk_screener_control_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS screener_heartbeat (
    id INT PRIMARY KEY DEFAULT 1,
    pid INT NULL,
    last_beat_at DATETIME NULL,
    timeframe VARCHAR(20) NULL,
    symbols_monitored INT NOT NULL DEFAULT 0,
    last_signal_at DATETIME NULL,
    last_universe_refresh_at DATETIME NULL,
    CONSTRAINT chk_screener_heartbeat_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE screener_heartbeat
    ADD COLUMN timeframe VARCHAR(20) NULL;

INSERT IGNORE INTO screener_heartbeat (id) VALUES (1);
INSERT IGNORE INTO screener_control (id, enabled, overrides_json)
VALUES (1, TRUE, NULL);
