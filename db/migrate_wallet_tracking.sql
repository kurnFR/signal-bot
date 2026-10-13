-- ============================================================================
-- Wallet Tracking migration (on-chain smart-money accumulation / distribution)
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS). The same DDL is also
-- applied automatically by wallet_tracker.store.ensure_tables().
-- Wallet columns use utf8mb4_bin: Solana addresses are case-sensitive.
-- ============================================================================

USE crypto_signals;

CREATE TABLE IF NOT EXISTS wallet_swaps (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chain VARCHAR(20) NOT NULL,
    wallet VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    pool_address VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    pair VARCHAR(80) NOT NULL,
    side ENUM('buy','sell') NOT NULL,
    usd_value DECIMAL(24,2) NOT NULL,
    token_amount DECIMAL(40,12) NOT NULL,
    price_usd DECIMAL(36,18) NOT NULL,
    tx_hash VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    block_time DATETIME NOT NULL,
    ingested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_wswap (chain, tx_hash, pool_address, side, wallet),
    INDEX idx_wswap_time (block_time),
    INDEX idx_wswap_wallet_time (chain, wallet, block_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wallet_positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chain VARCHAR(20) NOT NULL,
    wallet VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    pool_address VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    pair VARCHAR(80) NOT NULL,
    period_hours INT NOT NULL,
    side ENUM('BUY','SELL') NOT NULL,
    bias VARCHAR(16) NOT NULL,
    buy_usd DECIMAL(24,2) NOT NULL,
    sell_usd DECIMAL(24,2) NOT NULL,
    net_usd DECIMAL(24,2) NOT NULL,
    total_usd DECIMAL(24,2) NOT NULL,
    buy_tokens DECIMAL(40,12) NOT NULL,
    sell_tokens DECIMAL(40,12) NOT NULL,
    net_tokens DECIMAL(40,12) NOT NULL,
    avg_buy_price DECIMAL(36,18) NULL,
    avg_sell_price DECIMAL(36,18) NULL,
    avg_price DECIMAL(36,18) NULL,
    trade_count INT NOT NULL,
    buy_count INT NOT NULL,
    sell_count INT NOT NULL,
    first_trade_at DATETIME NOT NULL,
    last_trade_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_wpos (period_hours, chain, wallet, pool_address),
    INDEX idx_wpos_period_net (period_hours, net_usd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tracked_wallets (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chain VARCHAR(20) NOT NULL,
    wallet VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    label VARCHAR(80) NULL,
    source ENUM('auto','manual') NOT NULL DEFAULT 'auto',
    status ENUM('ACTIVE','IGNORED') NOT NULL DEFAULT 'ACTIVE',
    last_net_usd DECIMAL(24,2) NULL,
    first_flagged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_tracked (chain, wallet)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wallet_tracker_status (
    id TINYINT PRIMARY KEY,
    last_cycle_at DATETIME NULL,
    last_cycle_seconds DECIMAL(10,2) NULL,
    pools_scanned INT NOT NULL DEFAULT 0,
    swaps_ingested INT NOT NULL DEFAULT 0,
    candidates INT NOT NULL DEFAULT 0,
    last_error VARCHAR(500) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wallet_tracker_settings (
    id TINYINT PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    networks VARCHAR(255) NOT NULL DEFAULT 'eth,solana,base,bsc',
    pools_per_network INT NOT NULL DEFAULT 4,
    min_trade_usd DECIMAL(20,2) NOT NULL DEFAULT 10000,
    min_net_usd DECIMAL(20,2) NOT NULL DEFAULT 150000,
    auto_track_net_usd DECIMAL(20,2) NOT NULL DEFAULT 500000,
    poll_interval_seconds INT NOT NULL DEFAULT 300,
    retention_days INT NOT NULL DEFAULT 8,
    api_delay_seconds DECIMAL(8,3) NOT NULL DEFAULT 4.0,
    token_mode ENUM('all','selected') NOT NULL DEFAULT 'all',
    token_addresses TEXT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
