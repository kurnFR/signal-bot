-- Distinguish in-flight login reservations from completed failed attempts.
-- Existing rows represent completed failures and retain that meaning.
ALTER TABLE login_attempts
    ADD COLUMN attempt_state VARCHAR(12) NOT NULL DEFAULT 'failed' AFTER attempted_at;

CREATE INDEX idx_login_attempts_key_state_time
    ON login_attempts (rate_key, attempt_state, attempted_at);

-- Supports bounded global cleanup of expired attempts across usernames.
CREATE INDEX idx_login_attempts_attempted_at
    ON login_attempts (attempted_at);
