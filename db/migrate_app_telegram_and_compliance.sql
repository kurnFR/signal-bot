-- ============================================================================
-- Migration 034: Database-backed Telegram settings & user terms compliance
-- ============================================================================

CREATE TABLE IF NOT EXISTS app_telegram_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    encrypted_bot_token TEXT NULL,
    chat_id VARCHAR(128) NULL,
    delivery_authorized TINYINT(1) NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMP NULL DEFAULT NULL;
