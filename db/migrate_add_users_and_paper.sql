-- ============================================================================
-- Migration: Add users, paper_positions, paper_trades, and paper_config tables
-- ============================================================================

USE crypto_signals;

-- Users table for secure authentication
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50) NOT NULL UNIQUE,
    email         VARCHAR(100) UNIQUE NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('admin', 'trader', 'viewer') NOT NULL DEFAULT 'trader',
    is_active     TINYINT(1) NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_users_lookup (username, is_active)
) ENGINE=InnoDB;

-- Paper trading active positions
CREATE TABLE IF NOT EXISTS paper_positions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    market          ENUM('spot', 'futures') NOT NULL DEFAULT 'spot',
    timeframe       VARCHAR(10) NOT NULL DEFAULT '1d',
    strategy_name   VARCHAR(50) NOT NULL,
    direction       ENUM('LONG', 'SHORT') NOT NULL,
    entry_time      BIGINT NOT NULL,       -- ms epoch
    entry_time_wib  DATETIME GENERATED ALWAYS AS
                        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((entry_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    entry_price     DECIMAL(24,10) NOT NULL,
    current_price   DECIMAL(24,10) NOT NULL,
    stop_loss       DECIMAL(24,10) NOT NULL,
    take_profit     DECIMAL(24,10) NOT NULL,
    current_stop    DECIMAL(24,10) NOT NULL,
    trailing_active TINYINT(1) NOT NULL DEFAULT 0,
    trail_activation_r DECIMAL(10,4) NOT NULL DEFAULT 1.0,
    trail_distance_atr_mult DECIMAL(10,4) NOT NULL DEFAULT 1.5,
    atr_at_signal   DECIMAL(24,10) NOT NULL,
    initial_risk    DECIMAL(24,10) NOT NULL,
    unrealized_pnl_pct DECIMAL(12,6) NOT NULL DEFAULT 0.0,
    unrealized_r    DECIMAL(10,4) NOT NULL DEFAULT 0.0,
    holding_bars    INT NOT NULL DEFAULT 0,
    max_hold_bars   INT NOT NULL DEFAULT 200,
    last_update_time BIGINT NOT NULL,
    status          ENUM('OPEN', 'CLOSED') NOT NULL DEFAULT 'OPEN',
    INDEX idx_paper_pos_lookup (symbol, market, timeframe, strategy_name, status)
) ENGINE=InnoDB;

-- Paper trading closed trades ledger
CREATE TABLE IF NOT EXISTS paper_trades (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    position_id     BIGINT NULL,
    symbol          VARCHAR(20) NOT NULL,
    market          ENUM('spot', 'futures') NOT NULL,
    timeframe       VARCHAR(10) NOT NULL,
    strategy_name   VARCHAR(50) NOT NULL,
    direction       ENUM('LONG', 'SHORT') NOT NULL,
    entry_time      BIGINT NOT NULL,
    entry_time_wib  DATETIME GENERATED ALWAYS AS
                        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((entry_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    entry_price     DECIMAL(24,10) NOT NULL,
    exit_time       BIGINT NOT NULL,
    exit_time_wib   DATETIME GENERATED ALWAYS AS
                        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((exit_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    exit_price      DECIMAL(24,10) NOT NULL,
    exit_reason     ENUM('SL', 'TP', 'TRAIL', 'TIMEOUT', 'MANUAL') NOT NULL,
    stop_loss       DECIMAL(24,10) NOT NULL,
    take_profit     DECIMAL(24,10) NOT NULL,
    net_return_pct  DECIMAL(12,6) NOT NULL,
    r_multiple      DECIMAL(10,4) NOT NULL,
    holding_bars    INT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_paper_trades_lookup (symbol, market, timeframe, strategy_name, created_at DESC)
) ENGINE=InnoDB;

-- Paper trading active runner configuration
CREATE TABLE IF NOT EXISTS paper_configs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    market          ENUM('spot', 'futures') NOT NULL DEFAULT 'spot',
    timeframe       VARCHAR(10) NOT NULL DEFAULT '1d',
    strategy_name   VARCHAR(50) NOT NULL,
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    allocated_capital DECIMAL(18,2) NOT NULL DEFAULT 5000.00,
    risk_per_trade_pct DECIMAL(6,2) NOT NULL DEFAULT 1.00,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_paper_cfg (symbol, market, timeframe, strategy_name)
) ENGINE=InnoDB;
