"""
tests/test_paper_feature_fallback.py

Comprehensive regression test suite for Work Order 71917:
Dynamic Feature Calculation Fallback and Cache in Paper Engine.

Verifies:
1. Fresh persisted features are reused correctly without unnecessary recalculation.
2. Completely missing features trigger in-memory calculation via compute_all_features().
3. Partially missing features are reconstructed correctly (persisted preserved, missing filled).
4. Stale persisted features do not override newer candles.
5. Feature-to-candle alignment is strictly preserved across timestamps.
6. Futures and spot data cannot be mixed (strict SQL market binding).
7. Different symbols and timeframes cannot be mixed.
8. Forming candles are excluded from finalized signal evaluation (is_closed=1 enforcement).
9. Warm-up periods and NaN values are handled safely.
10. Insufficient OHLCV history (< 51 bars) produces a clear warning diagnostic.
11. In-memory calculation errors fail safely without crashing the engine.
12. Existing strategy dataframe contracts remain compatible across all standard strategies.
13. Strategy outputs remain identical when valid persisted features are supplied.
14. Per-cycle series cache in sync_and_evaluate_paper_trading prevents duplicate queries.
"""
import time
import logging
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import pytest

import db.db as db_mod
import paper.engine as pe_mod
from config import (
    RSI_WINDOW, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD,
    VOLUME_SMA_PERIOD, HTF_S_R_LOOKBACK_BARS
)
from features.indicators import compute_all_features
from backtest.strategies import STRATEGIES
from backtest.params import BASE_PARAMS

FEATURE_COLUMNS = [
    "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
    "volume_sma", "volume_ratio", "support", "resistance",
    "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
    "fib_5", "fib_618", "fib_786", "fib_1",
]


def _create_synthetic_ohlcv(n_bars=300, start_time=1700000000000, step_ms=3600000):
    """Creates a deterministic synthetic OHLCV dataframe."""
    np.random.seed(42)
    open_times = [start_time + i * step_ms for i in range(n_bars)]
    base_price = 50000.0 + np.cumsum(np.random.normal(0, 100, n_bars))
    highs = base_price + np.random.uniform(50, 200, n_bars)
    lows = base_price - np.random.uniform(50, 200, n_bars)
    closes = base_price + np.random.uniform(-40, 40, n_bars)
    volumes = np.random.uniform(10, 100, n_bars)

    df = pd.DataFrame({
        "open_time": open_times,
        "open": base_price,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "is_closed": [1] * n_bars,
    })
    return df


# --- Test 1: Fresh Persisted Features Reused ---

def test_fresh_persisted_features_reused():
    """When all persisted features are present and non-null, in-memory calculation is bypassed."""
    df_raw = _create_synthetic_ohlcv(100)
    params = {
        "RSI_WINDOW": RSI_WINDOW, "MACD_FAST": MACD_FAST, "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL, "ATR_PERIOD": ATR_PERIOD,
        "VOLUME_SMA_PERIOD": VOLUME_SMA_PERIOD, "HTF_S_R_LOOKBACK_BARS": HTF_S_R_LOOKBACK_BARS,
    }
    feats = compute_all_features(df_raw, params)
    full_df = pd.concat([df_raw, feats], axis=1)

    with patch("pandas.read_sql", return_value=full_df), \
         patch("db.db.get_sqlalchemy_engine"), \
         patch("features.indicators.compute_all_features") as mock_compute:

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=100)
        assert len(res) == 100
        # compute_all_features must NOT be called when persisted features are fresh
        assert not mock_compute.called
        assert not res["rsi"].iloc[-1] is None
        assert not pd.isna(res["rsi"].iloc[-1])


# --- Test 2: Completely Missing Features Trigger In-Memory Calculation ---

def test_completely_missing_features_trigger_calculation():
    """When persisted feature columns are entirely null (or missing), in-memory calculation runs."""
    df_raw = _create_synthetic_ohlcv(100)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=100)
        assert len(res) == 100
        # The latest rows must have finite, valid calculated features
        assert pd.notna(res["rsi"].iloc[-1])
        assert pd.notna(res["atr"].iloc[-1])
        assert pd.notna(res["macd"].iloc[-1])
        assert pd.notna(res["resistance"].iloc[-1])


# --- Test 3 & 4: Partially Missing and Stale Features Reconstructed Correctly ---

def test_partially_missing_and_stale_features():
    """When persisted features exist on older bars but are missing on newer bars,
    persisted values on older bars are preserved while newer bars are reconstructed."""
    df_raw = _create_synthetic_ohlcv(100)
    params = {
        "RSI_WINDOW": RSI_WINDOW, "MACD_FAST": MACD_FAST, "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL, "ATR_PERIOD": ATR_PERIOD,
        "VOLUME_SMA_PERIOD": VOLUME_SMA_PERIOD, "HTF_S_R_LOOKBACK_BARS": HTF_S_R_LOOKBACK_BARS,
    }
    feats = compute_all_features(df_raw, params)
    full_df = pd.concat([df_raw, feats], axis=1)

    # Simulate stale features: rows 60..99 have NaNs (missing in database)
    for col in FEATURE_COLUMNS:
        full_df.loc[60:99, col] = np.nan

    persisted_bar_59_rsi = full_df.loc[59, "rsi"]

    with patch("pandas.read_sql", return_value=full_df), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=100)
        assert len(res) == 100
        # Persisted row 59 preserved
        assert res.loc[59, "rsi"] == persisted_bar_59_rsi
        # Newer row 99 was reconstructed and is finite
        assert pd.notna(res.loc[99, "rsi"])
        assert pd.notna(res.loc[99, "atr"])


# --- Test 5: Feature-to-Candle Alignment ---

def test_feature_to_candle_alignment():
    """Features must align strictly row-by-row with open_time timestamps."""
    df_raw = _create_synthetic_ohlcv(80)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=80)
        assert (res["open_time"] == df_raw["open_time"]).all()
        # Verify index alignment
        assert res.index.equals(df_raw.index)


# --- Test 6 & 7: Market, Symbol, and Timeframe Isolation ---

def test_market_symbol_timeframe_isolation():
    """SQL queries must bind the exact market, symbol, and timeframe requested."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine"):

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "is_closed"] + FEATURE_COLUMNS)

        db_mod.fetch_ohlcv_with_features_df("ETHUSDT", "futures", "4h", limit=50)

        assert mock_read_sql.called
        sql = mock_read_sql.call_args[0][0]
        params = mock_read_sql.call_args[1].get("params") or mock_read_sql.call_args[0][2]

        assert "LEFT JOIN features f" in sql
        assert params == {"symbol": "ETHUSDT", "market": "futures", "timeframe": "4h", "limit": 50}


# --- Test 8: Forming Candles Excluded from Finalized Signals ---

def test_forming_candles_excluded_from_finalized():
    """closed_only=True must push is_closed=1 into SQL to exclude forming candles."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine"):

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "is_closed"] + FEATURE_COLUMNS)

        db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", closed_only=True, limit=100)
        sql_closed = mock_read_sql.call_args[0][0]
        assert "AND is_closed = 1" in sql_closed

        db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", closed_only=False, limit=100)
        sql_all = mock_read_sql.call_args[0][0]
        assert "AND is_closed = 1" not in sql_all


# --- Test 9: Warm-up Periods and NaN Handling ---

def test_warmup_periods_and_nan_handling():
    """Early warm-up rows (0..13 for RSI, 0..49 for HTF S/R) contain expected NaNs without crashing."""
    df_raw = _create_synthetic_ohlcv(100)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=100)
        # Bar 0 is NaN for RSI (period 14)
        assert pd.isna(res.loc[0, "rsi"])
        # Bar 14 is non-NaN for RSI
        assert pd.notna(res.loc[14, "rsi"])
        # Bar 49 is still NaN for S/R due to lookback=50 and shift(1)
        assert pd.isna(res.loc[49, "resistance"])
        # Bar 50 is non-NaN for S/R
        assert pd.notna(res.loc[50, "resistance"])


# --- Test 10: Insufficient History Produces Warning ---

def test_insufficient_history_warning(caplog):
    """When fewer than 51 bars are available, an explicit warning diagnostic is emitted."""
    df_raw = _create_synthetic_ohlcv(20)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"), \
         caplog.at_level(logging.WARNING):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=20)
        assert len(res) == 20
        assert any("Insufficient OHLCV history for indicator warm-up" in rec.message for rec in caplog.records)


# --- Test 11: Calculation Errors Fail Safely ---

def test_calculation_errors_fail_safely(caplog):
    """If compute_all_features raises an unexpected error, it is logged and does not crash."""
    df_raw = _create_synthetic_ohlcv(60)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"), \
         patch("features.indicators.compute_all_features", side_effect=ValueError("Simulated math error")), \
         caplog.at_level(logging.ERROR):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=60)
        assert len(res) == 60
        assert any("In-memory feature calculation failed" in rec.message for rec in caplog.records)


# --- Test 12: Strategy DataFrame Contracts Compatible ---

def test_strategy_dataframe_contracts():
    """Calculated DataFrames can be safely consumed by standard strategies without KeyError."""
    df_raw = _create_synthetic_ohlcv(200)
    for col in FEATURE_COLUMNS:
        df_raw[col] = np.nan

    with patch("pandas.read_sql", return_value=df_raw), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=200)

        run_params = dict(BASE_PARAMS, symbol="BTCUSDT", market="futures", timeframe="1h")
        for strat in ["trend_ema_v1", "breakout_continuation_v1", "confluence_ensemble_v1"]:
            fn = STRATEGIES[strat]
            trades = fn(res.copy(), run_params)
            assert isinstance(trades, list)


# --- Test 13: Strategy Results Unchanged with Valid Persisted Features ---

def test_strategy_results_identical_with_valid_persisted():
    """When valid persisted features are present, strategy execution produces identical output."""
    df_raw = _create_synthetic_ohlcv(200)
    params = {
        "RSI_WINDOW": RSI_WINDOW, "MACD_FAST": MACD_FAST, "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL, "ATR_PERIOD": ATR_PERIOD,
        "VOLUME_SMA_PERIOD": VOLUME_SMA_PERIOD, "HTF_S_R_LOOKBACK_BARS": HTF_S_R_LOOKBACK_BARS,
    }
    feats = compute_all_features(df_raw, params)
    df_with_feats = pd.concat([df_raw, feats], axis=1)

    run_params = dict(BASE_PARAMS, symbol="BTCUSDT", market="futures", timeframe="1h")
    trades_before = STRATEGIES["trend_ema_v1"](df_with_feats.copy(), run_params)

    with patch("pandas.read_sql", return_value=df_with_feats), \
         patch("db.db.get_sqlalchemy_engine"):

        res = db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "1h", limit=200)
        trades_after = STRATEGIES["trend_ema_v1"](res.copy(), run_params)

        assert len(trades_before) == len(trades_after)
        if trades_before:
            assert trades_before[0]["entry_price"] == trades_after[0]["entry_price"]
            assert trades_before[0]["stop_loss"] == trades_after[0]["stop_loss"]


# --- Test 14: Per-Cycle Series Cache in Paper Engine ---

def test_series_cache_prevents_duplicate_queries():
    """sync_and_evaluate_paper_trading caches market series across multiple strategies in one cycle."""
    cfg1 = {"id": 1, "symbol": "BTCUSDT", "market": "futures", "timeframe": "1h", "strategy_name": "trend_ema_v1", "is_active": 1, "allocated_capital": 5000, "risk_per_trade_pct": 1.0}
    cfg2 = {"id": 2, "symbol": "BTCUSDT", "market": "futures", "timeframe": "1h", "strategy_name": "breakout_continuation_v1", "is_active": 1, "allocated_capital": 5000, "risk_per_trade_pct": 1.0}

    df_synth = _create_synthetic_ohlcv(100)
    for col in FEATURE_COLUMNS:
        df_synth[col] = 50.0

    with patch("paper.engine.get_paper_configs", return_value=[cfg1, cfg2]), \
         patch("paper.engine._process_pending_paper_orders", return_value={"filled": 0, "expired": 0, "rejected": 0}), \
         patch("paper.engine.get_active_positions", return_value=[]), \
         patch("paper.engine.fetch_ohlcv_with_features_df", return_value=df_synth) as mock_fetch, \
         patch("paper.engine.fetch_latest_open_candle", return_value=None):

        result = pe_mod.sync_and_evaluate_paper_trading()

        assert result["evaluated_configs"] == 2
        # mock_fetch must be called only ONCE for (BTCUSDT, futures, 1h, False)
        assert mock_fetch.call_count == 1
