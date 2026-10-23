-- Configurable, auditable Battle Royale winner promotion and per-config Telegram gating.
-- Apply with the repository's normal DB migration procedure before enabling this feature.

ALTER TABLE paper_configs
    ADD COLUMN IF NOT EXISTS telegram_enabled TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE paper_configs
    ADD COLUMN IF NOT EXISTS report_enabled TINYINT(1) NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS strategy_auto_promotion_policy (
    id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    min_test_trades INT UNSIGNED NOT NULL DEFAULT 30,
    min_profit_factor DECIMAL(8,4) NOT NULL DEFAULT 1.2000,
    min_expectancy_r DECIMAL(8,4) NOT NULL DEFAULT 0.0500,
    max_drawdown_r DECIMAL(8,4) NOT NULL DEFAULT 8.0000,
    min_rank_score DECIMAL(8,4) NOT NULL DEFAULT 0.1000,
    require_holdout TINYINT(1) NOT NULL DEFAULT 1,
    send_telegram TINYINT(1) NOT NULL DEFAULT 1,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

INSERT IGNORE INTO strategy_auto_promotion_policy
    (id, enabled, min_test_trades, min_profit_factor, min_expectancy_r,
     max_drawdown_r, min_rank_score, require_holdout, send_telegram)
VALUES
    (1, 0, 30, 1.2000, 0.0500, 8.0000, 0.1000, 1, 1);

CREATE TABLE IF NOT EXISTS strategy_promotion_audit (
    decision_hash CHAR(64) NOT NULL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    market VARCHAR(12) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    data_segment VARCHAR(12) NOT NULL,
    strategy_name VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    metrics_json LONGTEXT NOT NULL,
    criteria_json LONGTEXT NOT NULL,
    paper_config_id INT NULL,
    telegram_result VARCHAR(32) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_strategy_promotion_audit_created (created_at),
    KEY idx_strategy_promotion_audit_strategy (strategy_name, created_at)
) ENGINE=InnoDB;
