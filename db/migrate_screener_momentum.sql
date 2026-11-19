ALTER TABLE smart_money_signals
    ADD COLUMN IF NOT EXISTS watch_context JSON NULL,
    ADD COLUMN IF NOT EXISTS parent_watch_id BIGINT NULL,
    ADD UNIQUE INDEX IF NOT EXISTS uq_smsig_parent (parent_watch_id),
    ADD INDEX IF NOT EXISTS idx_smsig_delivery (delivery_status, delivered_at);
