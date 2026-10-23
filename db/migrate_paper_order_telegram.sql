-- Persist the Telegram choice made when a paper signal becomes a pending order.
-- Existing orders default off because their original promotion decision is unknown.
ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS telegram_enabled TINYINT(1) NOT NULL DEFAULT 0;
