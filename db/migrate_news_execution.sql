-- ============================================================================
-- News AI Overlay Strategy -- Phase 3 (execution wiring) migration.
-- news_ai_signals needs market/timeframe to actually open a position from a
-- signal (Phase 1/2 only stored symbol, which wasn't enough on its own).
-- skip_reason records why a signal that WAS acted upon didn't result in a
-- trade (confluence block, daily cap, still-pending macro release, etc.),
-- for observability -- see NEWS_AI_STRATEGY_PLAN.md §4.
-- Safe to run after migrate_news_events.sql.
-- ============================================================================

USE crypto_signals;

ALTER TABLE news_ai_signals
    ADD COLUMN IF NOT EXISTS market VARCHAR(10) NULL AFTER symbol,
    ADD COLUMN IF NOT EXISTS timeframe VARCHAR(10) NULL AFTER market,
    ADD COLUMN IF NOT EXISTS skip_reason VARCHAR(100) NULL AFTER paper_position_id;


-- Concurrency hardening: execution workers claim signals atomically so
-- duplicate workers cannot open the same news-driven paper position.
ALTER TABLE news_ai_signals
    ADD COLUMN IF NOT EXISTS processing_at DATETIME NULL AFTER acted_on;

CREATE INDEX IF NOT EXISTS idx_news_signal_processing
    ON news_ai_signals (acted_on, processing_at);
