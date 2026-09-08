-- ============================================================================
-- P0 Paper Trading Accounting Migration
-- Adds explicit position-sizing and realized/unrealized accounting fields.
-- Safe to run after migrate_add_users_and_paper.sql.
-- ============================================================================

USE crypto_signals;

ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS allocated_capital DECIMAL(18,2) NOT NULL DEFAULT 5000.00,
    ADD COLUMN IF NOT EXISTS risk_per_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 1.0000,
    ADD COLUMN IF NOT EXISTS risk_amount DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS stop_distance DECIMAL(24,10) NOT NULL DEFAULT 0.0000000000,
    ADD COLUMN IF NOT EXISTS quantity DECIMAL(30,12) NOT NULL DEFAULT 0.000000000000,
    ADD COLUMN IF NOT EXISTS notional_value DECIMAL(24,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS entry_fee DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS exit_fee DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS funding_pnl DECIMAL(18,8) NOT NULL DEFAULT 0.00000000;

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS allocated_capital DECIMAL(18,2) NOT NULL DEFAULT 5000.00,
    ADD COLUMN IF NOT EXISTS risk_per_trade_pct DECIMAL(8,4) NOT NULL DEFAULT 1.0000,
    ADD COLUMN IF NOT EXISTS risk_amount DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS stop_distance DECIMAL(24,10) NOT NULL DEFAULT 0.0000000000,
    ADD COLUMN IF NOT EXISTS quantity DECIMAL(30,12) NOT NULL DEFAULT 0.000000000000,
    ADD COLUMN IF NOT EXISTS notional_value DECIMAL(24,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS gross_pnl DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS entry_fee DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS exit_fee DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS funding_pnl DECIMAL(18,8) NOT NULL DEFAULT 0.00000000,
    ADD COLUMN IF NOT EXISTS net_pnl DECIMAL(18,8) NOT NULL DEFAULT 0.00000000;

-- Existing lookup index does not protect against concurrent duplicate OPEN
-- positions. A generated active-position key makes the uniqueness constraint
-- explicit while allowing multiple historical CLOSED positions.
ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS open_position_key VARCHAR(255)
        GENERATED ALWAYS AS (
            CASE WHEN status = 'OPEN'
                 THEN CONCAT(symbol, '|', market, '|', timeframe, '|', strategy_name)
                 ELSE NULL
            END
        ) STORED,
    ADD UNIQUE INDEX IF NOT EXISTS uq_paper_open_position (open_position_key);
