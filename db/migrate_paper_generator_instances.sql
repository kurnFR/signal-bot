-- ============================================================================
-- 024: Independent ML generator model instances in paper trading.
--
-- Preserve legacy config uniqueness while allowing different ML model
-- instances for the same symbol/market/timeframe/strategy. Position and trade
-- attribution follows the stable paper config ID, not only strategy_name.
-- ============================================================================

USE crypto_signals;

-- 1. A config may reference either the legacy signal-filter model or the new
--    independent signal-generator model. The generated identity keeps legacy
--    NULL/NULL configs unique while allowing distinct model instances.
ALTER TABLE paper_configs
    ADD COLUMN IF NOT EXISTS ml_generator_model_id VARCHAR(100) NULL;

ALTER TABLE paper_configs
    ADD COLUMN IF NOT EXISTS model_instance_key VARCHAR(202)
        GENERATED ALWAYS AS (
            CONCAT(COALESCE(ml_model_id, ''), '|', COALESCE(ml_generator_model_id, ''))
        ) STORED;

ALTER TABLE paper_configs
    DROP INDEX IF EXISTS uq_paper_cfg;

ALTER TABLE paper_configs
    ADD UNIQUE INDEX IF NOT EXISTS uq_paper_cfg_instance
        (symbol, market, timeframe, strategy_name, model_instance_key);

-- 2. Attribute every paper position to the config instance that created it.
ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS config_id INT NULL,
    ADD COLUMN IF NOT EXISTS ml_generator_model_id VARCHAR(100) NULL;

-- Existing configs were unique by the four legacy identity fields, so this
-- backfill is deterministic for positions created before model instances.
UPDATE paper_positions p
JOIN paper_configs c
  ON c.symbol = p.symbol
 AND c.market = p.market
 AND c.timeframe = p.timeframe
 AND c.strategy_name = p.strategy_name
SET p.config_id = c.id
WHERE p.config_id IS NULL;

-- The old key prevented two configs from holding separate positions. Replace
-- it with a config-scoped key while retaining one OPEN position per instance.
ALTER TABLE paper_positions
    DROP INDEX IF EXISTS uq_paper_open_position,
    DROP COLUMN IF EXISTS open_position_key;

ALTER TABLE paper_positions
    ADD COLUMN IF NOT EXISTS open_position_key VARCHAR(255)
        GENERATED ALWAYS AS (
            CASE WHEN status = 'OPEN'
                 THEN CONCAT(
                     COALESCE(config_id, 0), '|',
                     symbol, '|', market, '|', timeframe, '|', strategy_name
                 )
                 ELSE NULL
            END
        ) STORED,
    ADD UNIQUE INDEX IF NOT EXISTS uq_paper_open_position (open_position_key),
    ADD INDEX IF NOT EXISTS idx_paper_positions_config (config_id, status);

-- 3. Keep closed trade reporting and alert preferences tied to the same
--    config instance. Existing trades inherit attribution from their position.
ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS config_id INT NULL,
    ADD COLUMN IF NOT EXISTS ml_generator_model_id VARCHAR(100) NULL;

UPDATE paper_trades t
JOIN paper_positions p ON p.id = t.position_id
SET t.config_id = p.config_id,
    t.ml_generator_model_id = p.ml_generator_model_id
WHERE t.config_id IS NULL;

ALTER TABLE paper_trades
    ADD INDEX IF NOT EXISTS idx_paper_trades_config (config_id, created_at);

-- 4. Pending orders retain generator model identity across restart recovery.
ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS ml_generator_model_id VARCHAR(100) NULL;
