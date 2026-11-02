-- RetailBot2 schema is managed by the versioned migration runner.
-- Runtime startup only verifies these objects; it never creates or alters them.

CREATE TABLE IF NOT EXISTS rdt_trades (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    mode VARCHAR(10) NOT NULL DEFAULT 'shadow',
    entry_price DECIMAL(20,8) NOT NULL,
    stop_price DECIMAL(20,8) NOT NULL,
    target_price DECIMAL(20,8) NOT NULL,
    atr DECIMAL(20,8) NOT NULL,
    rr DECIMAL(10,2) NOT NULL,
    units DECIMAL(20,8) NOT NULL,
    strategy VARCHAR(30) NOT NULL,
    strategies_json TEXT NULL,
    telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    confluence_score INT DEFAULT 1,
    reason TEXT NULL,
    status VARCHAR(10) DEFAULT 'OPEN',
    exit_price DECIMAL(20,8) NULL,
    exit_reason VARCHAR(20) NULL,
    net_pnl DECIMAL(20,8) NULL,
    pnl_percent DECIMAL(10,4) NULL,
    entry_time DATETIME,
    exit_time DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sym_status (symbol, status),
    INDEX idx_mode_status (mode, status),
    INDEX idx_strategy (strategy)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rdt_strategy_stats (
    id INT AUTO_INCREMENT PRIMARY KEY,
    strategy VARCHAR(30) NOT NULL,
    mode VARCHAR(10) NOT NULL,
    total_trades INT DEFAULT 0,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    total_pnl DECIMAL(20,8) DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_strat_mode (strategy, mode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bot_control (
    id INT PRIMARY KEY DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    overrides_json TEXT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT chk_bot_control_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO bot_control (id, enabled, overrides_json)
VALUES (1, TRUE, NULL);

CREATE TABLE IF NOT EXISTS rdt_strategy_lifecycle (
    strategy_id VARCHAR(64) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    evaluation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    paper_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    auto_promote_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_id, mode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Existing tables may predate the Telegram approval gate. Duplicate-column
-- errno 1060 is explicitly tolerated by the migration runner.
ALTER TABLE rdt_trades
    ADD COLUMN telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE;
