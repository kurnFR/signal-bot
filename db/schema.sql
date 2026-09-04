-- ============================================================================
-- Crypto Signal Bot — Step 1 schema (raw data layer)
-- Indicators/features come in Step 2, in a separate table, computed from
-- this data. Never mix raw exchange data with derived values.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS crypto_signals
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE crypto_signals;

-- ----------------------------------------------------------------------------
-- OHLCV candles — spot AND futures, all timeframes, one table.
-- open_time is the Binance candle open timestamp in milliseconds (exact,
-- so there is never ambiguity about which candle a row represents).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol              VARCHAR(20)  NOT NULL,
    market              ENUM('spot','futures') NOT NULL,
    timeframe           VARCHAR(5)   NOT NULL,
    open_time           BIGINT       NOT NULL,   -- ms epoch, candle open
    open_time_wib       DATETIME GENERATED ALWAYS AS
                            (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((open_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    close_time          BIGINT       NOT NULL,   -- ms epoch, candle close
    close_time_wib      DATETIME GENERATED ALWAYS AS
                            (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((close_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    open                DECIMAL(24,10) NOT NULL,
    high                DECIMAL(24,10) NOT NULL,
    low                 DECIMAL(24,10) NOT NULL,
    close               DECIMAL(24,10) NOT NULL,
    volume              DECIMAL(30,10) NOT NULL,
    quote_volume        DECIMAL(30,10) NOT NULL,
    num_trades          INT UNSIGNED NOT NULL,
    taker_buy_base_vol  DECIMAL(30,10) NOT NULL,
    taker_buy_quote_vol DECIMAL(30,10) NOT NULL,
    is_closed           TINYINT(1)   NOT NULL DEFAULT 1,  -- 0 while candle still forming (live WS only)
    updated_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, market, timeframe, open_time),
    INDEX idx_ohlcv_lookup (symbol, market, timeframe, open_time DESC)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Funding rate + mark price history (futures only).
-- Live WS gives mark price every second. We throttle writes to one row
-- per FUNDING_WRITE_INTERVAL_SECONDS to avoid bloating the table.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS funding_rate (
    symbol          VARCHAR(20)   NOT NULL,
    event_time      BIGINT        NOT NULL,   -- ms epoch
    event_time_wib  DATETIME GENERATED ALWAYS AS
                        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    mark_price      DECIMAL(24,10) NULL,
    index_price     DECIMAL(24,10) NULL,
    funding_rate    DECIMAL(12,10) NOT NULL,
    next_funding_time BIGINT      NULL,
    PRIMARY KEY (symbol, event_time)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Open interest snapshots (futures only). REST-polled — no WS stream exists.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS open_interest (
    symbol              VARCHAR(20)    NOT NULL,
    event_time          BIGINT         NOT NULL,  -- ms epoch (poll time)
    event_time_wib      DATETIME GENERATED ALWAYS AS
                            (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    open_interest       DECIMAL(30,10) NOT NULL,   -- in contracts/base asset
    open_interest_value DECIMAL(30,10) NULL,       -- in quote asset (if available)
    PRIMARY KEY (symbol, event_time)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Liquidations (futures only), from !forceOrder@arr.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS liquidations (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(20)    NOT NULL,
    side            ENUM('BUY','SELL') NOT NULL,   -- side of the liquidation order
    order_type      VARCHAR(20)    NOT NULL,
    time_in_force   VARCHAR(10)    NULL,
    orig_qty        DECIMAL(30,10) NOT NULL,
    price            DECIMAL(24,10) NOT NULL,
    avg_price        DECIMAL(24,10) NULL,
    order_status     VARCHAR(20)   NULL,
    event_time       BIGINT        NOT NULL,       -- ms epoch
    event_time_wib   DATETIME GENERATED ALWAYS AS
                         (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((event_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    INDEX idx_liq_symbol_time (symbol, event_time DESC)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Computed indicators/features -- one row per (symbol, market, timeframe,
-- open_time), same grain as ohlcv, computed FROM ohlcv by features/build_features.py.
-- Never write raw exchange data here, and never let the backtester or live
-- signal engine compute indicators any other way than through
-- features/indicators.py -- this table is the single shared source both
-- read from.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
    symbol          VARCHAR(20)  NOT NULL,
    market          ENUM('spot','futures') NOT NULL,
    timeframe       VARCHAR(5)   NOT NULL,
    open_time       BIGINT       NOT NULL,   -- matches ohlcv.open_time exactly
    open_time_wib   DATETIME GENERATED ALWAYS AS
                        (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((open_time DIV 1000) + 25200) SECOND)) VIRTUAL,

    rsi             DECIMAL(10,4) NULL,

    macd            DECIMAL(24,10) NULL,
    macd_signal     DECIMAL(24,10) NULL,
    macd_hist       DECIMAL(24,10) NULL,

    atr             DECIMAL(24,10) NULL,
    atr_pct         DECIMAL(10,4) NULL,

    volume_sma      DECIMAL(30,10) NULL,
    volume_ratio    DECIMAL(12,6) NULL,

    support         DECIMAL(24,10) NULL,
    resistance      DECIMAL(24,10) NULL,

    swing_high      DECIMAL(24,10) NULL,
    swing_low       DECIMAL(24,10) NULL,
    fib_0           DECIMAL(24,10) NULL,
    fib_236         DECIMAL(24,10) NULL,
    fib_382         DECIMAL(24,10) NULL,
    fib_5           DECIMAL(24,10) NULL,
    fib_618         DECIMAL(24,10) NULL,
    fib_786         DECIMAL(24,10) NULL,
    fib_1           DECIMAL(24,10) NULL,

    computed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (symbol, market, timeframe, open_time),
    INDEX idx_features_lookup (symbol, market, timeframe, open_time DESC)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Backtest runs and their trades (Step 3). One row in backtest_runs per
-- invocation of the backtester against one symbol/market/timeframe with a
-- given parameter set; backtest_trades holds every simulated trade from
-- that run so results are fully auditable, not just a summary number.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol          VARCHAR(20)  NOT NULL,
    market          ENUM('spot','futures') NOT NULL,
    timeframe       VARCHAR(5)   NOT NULL,
    strategy_name   VARCHAR(50)  NOT NULL DEFAULT 'confluence_reversal_v1',
    data_segment    ENUM('train','holdout','full') NOT NULL DEFAULT 'train',
    params_json     JSON         NOT NULL,   -- exact parameter set used, for reproducibility
    data_start_time BIGINT       NOT NULL,   -- ms epoch of first candle used
    data_end_time   BIGINT       NOT NULL,   -- ms epoch of last candle used
    total_trades    INT          NOT NULL DEFAULT 0,
    win_rate_pct    DECIMAL(6,3) NULL,
    expectancy_r    DECIMAL(10,4) NULL,      -- average R-multiple per trade
    profit_factor   DECIMAL(10,4) NULL,
    max_drawdown_r  DECIMAL(10,4) NULL,      -- max drawdown in cumulative R-multiples
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_backtest_runs_lookup (symbol, market, timeframe, created_at DESC)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS backtest_trades (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id            BIGINT NOT NULL,
    direction         ENUM('LONG','SHORT') NOT NULL,
    signal_open_time  BIGINT NOT NULL,
    entry_time        BIGINT NOT NULL,
    entry_time_wib    DATETIME GENERATED ALWAYS AS
                          (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((entry_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    entry_price       DECIMAL(24,10) NOT NULL,
    exit_time         BIGINT NOT NULL,
    exit_time_wib     DATETIME GENERATED ALWAYS AS
                          (DATE_ADD('1970-01-01 00:00:00', INTERVAL ((exit_time DIV 1000) + 25200) SECOND)) VIRTUAL,
    exit_price        DECIMAL(24,10) NOT NULL,
    exit_reason       ENUM('SL','TP','TIMEOUT','END_OF_DATA','TRAIL') NOT NULL,
    stop_loss         DECIMAL(24,10) NOT NULL,
    take_profit       DECIMAL(24,10) NOT NULL,
    net_return_pct    DECIMAL(12,6) NOT NULL,
    r_multiple        DECIMAL(10,4) NULL,
    holding_bars      INT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE,
    INDEX idx_backtest_trades_run (run_id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- Collector health / bookkeeping — lets you see at a glance whether a
-- collector is alive and how stale its data is (used in Step 1 QA).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collector_heartbeat (
    collector_name  VARCHAR(50) PRIMARY KEY,
    last_beat_time  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status          VARCHAR(20) NOT NULL DEFAULT 'ok',
    detail          VARCHAR(255) NULL
) ENGINE=InnoDB;
