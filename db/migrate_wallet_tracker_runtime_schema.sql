-- Wallet Tracker schema upgrades that were historically applied by runtime startup.
-- Runtime ensure_tables() is now read-only; apply this migration with db.migrate.
ALTER TABLE tracked_wallets
    MODIFY status ENUM('ACTIVE','IGNORED','INACTIVE') NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE wallet_tracker_settings
    ADD COLUMN birdeye_api_key VARCHAR(255) NULL;
