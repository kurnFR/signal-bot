-- Fence stale RetailBot2 workers after a lease is reclaimed.
ALTER TABLE rdt_telegram_outbox
    ADD COLUMN claim_token CHAR(32) NULL AFTER locked_until;
