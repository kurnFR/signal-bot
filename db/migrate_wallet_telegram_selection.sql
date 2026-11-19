-- NULL means alerts are off. On selection, capture the latest ingested swap.
ALTER TABLE tracked_wallets ADD COLUMN IF NOT EXISTS telegram_after_id BIGINT NULL;
