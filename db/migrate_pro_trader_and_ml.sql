-- ============================================================================
-- Migration: Professional Trader Review Improvements & ML Integration
-- Adds audit logging, token revocation, circuit breakers, and ML model gating.
-- ============================================================================

USE crypto_signals;

-- 1. User security: forced password change flag
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS must_change_password TINYINT(1) NOT NULL DEFAULT 0;

-- 2. Paper configs: ML filter model ID association
ALTER TABLE paper_configs
    ADD COLUMN IF NOT EXISTS ml_model_id VARCHAR(100) NULL;

-- 3. Paper positions: ML attribution
ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS ml_model_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS ml_probability DECIMAL(8,4) NULL;

-- 4. Paper trades: ML attribution
ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS ml_model_id VARCHAR(100) NULL,
    ADD COLUMN IF NOT EXISTS ml_probability DECIMAL(8,4) NULL;

-- 5. Revoked tokens table (server-side logout / token blacklist)
CREATE TABLE IF NOT EXISTS revoked_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    INDEX idx_revoked_exp (expires_at)
) ENGINE=InnoDB;

-- 6. Audit logs table (immutable action history for all trader/admin actions)
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    username VARCHAR(50) NOT NULL,
    action VARCHAR(100) NOT NULL,
    details JSON NULL,
    ip_address VARCHAR(45) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_created (created_at DESC),
    INDEX idx_audit_user (user_id)
) ENGINE=InnoDB;
