-- ============================================================================
-- News AI Overlay Strategy -- Phase 1 (data plumbing) migration.
-- Adds storage for raw news/economic-calendar events. `news_ai_signals` is
-- created now too (schema-only) so the Phase 2 AI reasoning layer has
-- somewhere to write without a second migration, but nothing populates it
-- yet -- see NEWS_AI_STRATEGY_PLAN.md.
-- Safe to run after migrate_p0_paper_accounting.sql.
-- ============================================================================

USE crypto_signals;

CREATE TABLE IF NOT EXISTS news_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(50) NOT NULL,                 -- 'cryptopanic' | 'finnhub_econ_calendar'
    category ENUM('macro_calendar', 'crypto_news') NOT NULL,
    external_id VARCHAR(100) NOT NULL,            -- provider's own id/slug, used for idempotent upsert
    headline TEXT NOT NULL,
    url VARCHAR(1000) NULL,
    symbols VARCHAR(200) NULL,                    -- comma-separated tickers this event is tagged to, if any
    country VARCHAR(10) NULL,                     -- for macro_calendar rows (e.g. 'US')
    impact VARCHAR(10) NULL,                      -- 'low' | 'medium' | 'high' (macro_calendar only)
    published_at DATETIME NULL,                   -- when the news item was published
    scheduled_at DATETIME NULL,                   -- for macro_calendar: the known release time
    actual_value VARCHAR(50) NULL,                -- macro_calendar: released figure, once available
    forecast_value VARCHAR(50) NULL,
    previous_value VARCHAR(50) NULL,
    raw_payload JSON NULL,
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,                   -- set once the Phase 2 AI layer has evaluated this row
    UNIQUE KEY uq_news_source_external (source, external_id),
    INDEX idx_news_category_time (category, published_at),
    INDEX idx_news_scheduled (scheduled_at),
    INDEX idx_news_processed (processed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS news_ai_signals (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    news_event_id BIGINT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    bias ENUM('long', 'short', 'neutral') NOT NULL,
    confidence TINYINT UNSIGNED NOT NULL,          -- 0-100
    reasoning TEXT NULL,
    invalidation_condition TEXT NULL,
    time_horizon VARCHAR(20) NULL,                 -- 'scalp' | 'intraday' | 'swing'
    trade_timing VARCHAR(20) NULL,                 -- 'wait_for_reaction' | 'trade_immediately'
    acted_on BOOLEAN NOT NULL DEFAULT FALSE,
    paper_position_id BIGINT NULL,                 -- FK to paper_positions once/if a trade is opened
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_news_signal_event FOREIGN KEY (news_event_id) REFERENCES news_events(id),
    INDEX idx_signal_symbol_time (symbol, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
