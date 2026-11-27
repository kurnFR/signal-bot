ALTER TABLE news_ai_settings
    MODIFY COLUMN min_confidence TINYINT UNSIGNED NULL,
    MODIFY COLUMN max_trades_per_day TINYINT UNSIGNED NULL,
    ADD COLUMN IF NOT EXISTS stop_atr_mult DECIMAL(5,3) NULL CHECK (stop_atr_mult BETWEEN 0.5 AND 5),
    ADD COLUMN IF NOT EXISTS tp_r_scalp DECIMAL(6,3) NULL CHECK (tp_r_scalp BETWEEN 0.5 AND 10),
    ADD COLUMN IF NOT EXISTS tp_r_intraday DECIMAL(6,3) NULL CHECK (tp_r_intraday BETWEEN 0.5 AND 10),
    ADD COLUMN IF NOT EXISTS tp_r_swing DECIMAL(6,3) NULL CHECK (tp_r_swing BETWEEN 0.5 AND 10),
    ADD COLUMN IF NOT EXISTS poll_interval_seconds SMALLINT UNSIGNED NULL CHECK (poll_interval_seconds BETWEEN 30 AND 3600),
    ADD COLUMN IF NOT EXISTS execution_poll_interval_seconds SMALLINT UNSIGNED NULL CHECK (execution_poll_interval_seconds BETWEEN 30 AND 3600);
