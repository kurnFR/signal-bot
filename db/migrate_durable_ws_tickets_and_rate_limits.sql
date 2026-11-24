-- Migration 035: Durable multi-worker WebSocket tickets and compute rate limiting
-- Ensures single-use WebSocket auth tickets and compute-heavy endpoint rate limits
-- remain consistent across multiple Uvicorn/Gunicorn workers.

CREATE TABLE IF NOT EXISTS ws_auth_tickets (
    ticket_hash  CHAR(64)     NOT NULL PRIMARY KEY,
    user_payload JSON         NOT NULL,
    expires_at   DOUBLE       NOT NULL,
    consumed_at  DOUBLE       NULL,
    created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ws_auth_tickets_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS compute_rate_limits (
    id           BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    rate_key     VARCHAR(160) NOT NULL,
    requested_at DOUBLE       NOT NULL,
    INDEX idx_compute_rate_limits_key_time (rate_key, requested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
