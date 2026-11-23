-- Migration 033: Store validated backtest strategy parameter overrides for paper trading
ALTER TABLE paper_configs ADD COLUMN IF NOT EXISTS strategy_params JSON NULL DEFAULT NULL;
