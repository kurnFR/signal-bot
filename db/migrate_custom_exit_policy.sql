-- NULL retains the existing exit behavior for previously created orders and positions.
ALTER TABLE paper_orders ADD COLUMN IF NOT EXISTS custom_exit_policy JSON NULL;
ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS custom_exit_policy JSON NULL;
