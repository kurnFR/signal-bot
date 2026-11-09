-- Durable, idempotent RetailBot2 Telegram delivery queue.
CREATE TABLE IF NOT EXISTS rdt_telegram_outbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_key VARCHAR(128) NOT NULL,
    trade_id INT NOT NULL,
    event_type VARCHAR(16) NOT NULL,
    payload_json LONGTEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_until DATETIME NULL,
    last_error VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_rdt_telegram_outbox_event (event_key),
    KEY idx_rdt_telegram_outbox_due (status, next_attempt_at, locked_until, id),
    KEY idx_rdt_telegram_outbox_trade (trade_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
