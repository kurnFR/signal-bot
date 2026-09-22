-- ============================================================================
-- Smart Money Volume Screener migration
-- Adds storage for real-time institutional volume, RVOL spikes, and divergence signals.
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
-- ============================================================================

USE crypto_signals;

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
    candle_time VARCHAR(20) NOT NULL,
    telegram_sent BOOLEAN NOT NULL DEFAULT FALSE,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_smsig_symbol_time (symbol, detected_at),
    INDEX idx_smsig_type_time (signal_type, detected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
