-- Durable login rate limiting. Shared by every web worker and survives restarts.
-- Failed attempts are rows; the limiter counts rows inside its time window.
CREATE TABLE IF NOT EXISTS login_attempts (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    rate_key     VARCHAR(190) NOT NULL,
    attempted_at DOUBLE       NOT NULL,
    INDEX idx_login_attempts_key_time (rate_key, attempted_at)
) ENGINE=InnoDB;
