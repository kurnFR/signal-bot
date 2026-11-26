-- Runtime overrides for the News AI guardrails. No row == use the env/config defaults.
CREATE TABLE IF NOT EXISTS news_ai_settings (
    id INT NOT NULL PRIMARY KEY DEFAULT 1,
    min_confidence TINYINT UNSIGNED NOT NULL,
    max_trades_per_day TINYINT UNSIGNED NOT NULL,
    updated_by VARCHAR(100) NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT chk_news_ai_settings_singleton CHECK (id = 1),
    CONSTRAINT chk_news_ai_min_conf CHECK (min_confidence BETWEEN 50 AND 100),
    CONSTRAINT chk_news_ai_max_trades CHECK (max_trades_per_day <= 20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
