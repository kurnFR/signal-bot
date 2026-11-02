-- Invalidate access tokens issued before an administrative password reset
-- or account role/active-state change. Existing sessions retain version zero.
ALTER TABLE users
    ADD COLUMN session_version INT UNSIGNED NOT NULL DEFAULT 0
    AFTER must_change_password;
