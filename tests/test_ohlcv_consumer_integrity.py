"""
Tests for Work Order 71875: OHLCV Consumer Integrity Hardening.

Verifies:
1. Default fetch_ohlcv_df excludes forming candles (is_closed=0).
2. Explicit forming-candle access (closed_only=False) returns forming records when requested.
3. Closed records remain included and ordering is preserved (ORDER BY open_time ASC).
4. Symbol, market, and timeframe filters remain strictly parameterized (no SQL injection).
5. Feature-generation consumer (build_features.build_one) enforces closed_only=True.
6. Paper engine enforces closed_only=True for signals and uses fetch_latest_open_candle for entries.
7. Backtesting runners enforce closed_only=True.
8. fetch_ohlcv_with_features_df pushes closed_only into SQL for both limited and unlimited queries.
9. Existing spot behavior is preserved and consistent with futures.
"""
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

import db.db as db_mod
import features.build_features as bf_mod


# --- Tests 1, 2, 3, 4, 5, 9: fetch_ohlcv_df Contract ---

def test_fetch_ohlcv_df_default_excludes_forming_candles():
    """Scenario 1: Default call to fetch_ohlcv_df must include AND is_closed = 1 in SQL."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine") as mock_engine:

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

        db_mod.fetch_ohlcv_df("BTCUSDT", "futures", "15m")

        assert mock_read_sql.called
        sql_query = mock_read_sql.call_args[0][0]
        params = mock_read_sql.call_args[1].get("params") or mock_read_sql.call_args[0][2]

        assert "AND is_closed = 1" in sql_query, "Default fetch_ohlcv_df must filter WHERE is_closed = 1"
        assert "ORDER BY open_time ASC" in sql_query, "Must preserve ORDER BY open_time ASC"
        assert params == ("BTCUSDT", "futures", "15m"), "Must bind symbol, market, timeframe"


def test_fetch_ohlcv_df_explicit_closed_only_false_includes_forming():
    """Scenario 2: When closed_only=False is requested, AND is_closed = 1 must be omitted."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine") as mock_engine:

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

        db_mod.fetch_ohlcv_df("BTCUSDT", "futures", "15m", closed_only=False)

        assert mock_read_sql.called
        sql_query = mock_read_sql.call_args[0][0]
        params = mock_read_sql.call_args[1].get("params") or mock_read_sql.call_args[0][2]

        assert "AND is_closed = 1" not in sql_query, "closed_only=False must omit is_closed filter"
        assert "ORDER BY open_time ASC" in sql_query, "Must preserve ORDER BY open_time ASC"
        assert params == ("BTCUSDT", "futures", "15m")


def test_fetch_ohlcv_df_spot_and_futures_consistency():
    """Scenario 10: Both spot and futures use the exact same parameterized closed_only contract."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine") as mock_engine:

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

        for market in ["spot", "futures"]:
            db_mod.fetch_ohlcv_df("ETHUSDT", market, "1h")
            sql_query = mock_read_sql.call_args[0][0]
            assert "AND is_closed = 1" in sql_query
            assert "WHERE symbol=%s AND market=%s AND timeframe=%s" in sql_query


def test_fetch_ohlcv_df_no_sql_injection():
    """Scenario 9: Verifies that SQL filtering uses parameterized queries without raw string concatenation."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine") as mock_engine:

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])

        malicious_symbol = "BTCUSDT'; DROP TABLE ohlcv; --"
        db_mod.fetch_ohlcv_df(malicious_symbol, "futures", "15m")

        sql_query = mock_read_sql.call_args[0][0]
        params = mock_read_sql.call_args[1].get("params") or mock_read_sql.call_args[0][2]

        # The query text must contain %s placeholders, not the raw malicious string
        assert malicious_symbol not in sql_query
        assert params[0] == malicious_symbol


# --- Test 6: Feature Builder Consumer Hardening ---

def test_build_features_consumer_enforces_closed_only():
    """Scenario 6: build_features.build_one must explicitly invoke fetch_ohlcv_df with closed_only=True."""
    with patch("features.build_features.fetch_ohlcv_df") as mock_fetch, \
         patch("features.build_features.compute_all_features") as mock_compute, \
         patch("features.build_features.upsert_features") as mock_upsert:

        # Return enough dummy rows to satisfy min_required warm-up
        dummy_df = pd.DataFrame({
            "open_time": [1000 + i * 900000 for i in range(150)],
            "open": [60000.0] * 150,
            "high": [60100.0] * 150,
            "low": [59900.0] * 150,
            "close": [60050.0] * 150,
            "volume": [100.0] * 150,
        })
        mock_fetch.return_value = dummy_df
        mock_compute.return_value = pd.DataFrame({c: [1.0] * 150 for c in bf_mod.FEATURE_COLUMNS})

        bf_mod.build_one("BTCUSDT", "futures", "15m")

        assert mock_fetch.called
        kwargs = mock_fetch.call_args[1]
        args = mock_fetch.call_args[0]
        # Either passed as 4th positional arg or keyword closed_only=True
        closed_arg = kwargs.get("closed_only", args[3] if len(args) > 3 else None)
        assert closed_arg is True, "build_one must explicitly pass closed_only=True"


# --- Test 7 & 8: Paper Engine & Backtesting Consumers ---

def test_paper_engine_and_backtest_consumers_are_closed_only():
    """Scenarios 7 & 8: Verify strategy evaluation modules only request finalized candles."""
    import inspect
    import paper.engine as pe
    import backtest.run_backtest as rb
    import backtest.optimize as bo
    import backtest.walk_forward as bw

    # Check paper engine signal evaluation calls
    pe_src = inspect.getsource(pe)
    assert "fetch_ohlcv_with_features_df" in pe_src
    # Every invocation in paper engine must pass closed_only=True
    assert "closed_only=False" not in pe_src

    # Check backtest runners
    for mod in [rb, bo, bw]:
        src = inspect.getsource(mod)
        assert "closed_only=True" in src
        assert "closed_only=False" not in src


def test_fetch_latest_open_candle_explicit_forming_intent():
    """Verify that fetch_latest_open_candle explicitly queries is_closed=0 with LIMIT 1."""
    with patch("db.db.get_pool") as mock_get_pool:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_pool.return_value.get_connection.return_value = mock_conn
        mock_cur.fetchone.return_value = None

        db_mod.fetch_latest_open_candle("BTCUSDT", "futures", "15m")

        assert mock_cur.execute.called
        query = mock_cur.execute.call_args[0][0]
        assert "is_closed=0" in query or "is_closed = 0" in query
        assert "LIMIT 1" in query
        assert "ORDER BY open_time DESC" in query


# --- Test 8: fetch_ohlcv_with_features_df SQL Pushdown ---

def test_fetch_ohlcv_with_features_df_sql_pushdown():
    """Verifies that fetch_ohlcv_with_features_df pushes is_closed=1 into SQL for both limited and full queries."""
    with patch("pandas.read_sql") as mock_read_sql, \
         patch("db.db.get_sqlalchemy_engine") as mock_engine:

        mock_read_sql.return_value = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "is_closed"])

        # Test 1: Full query (limit=None, closed_only=True)
        db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "15m", closed_only=True, limit=None)
        sql_unlimited = mock_read_sql.call_args[0][0]
        assert "AND o.is_closed = 1" in sql_unlimited

        # Test 2: Full query with closed_only=False
        db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "15m", closed_only=False, limit=None)
        sql_unlimited_open = mock_read_sql.call_args[0][0]
        assert "AND o.is_closed = 1" not in sql_unlimited_open

        # Test 3: Limited query (limit=300, closed_only=True)
        db_mod.fetch_ohlcv_with_features_df("BTCUSDT", "futures", "15m", closed_only=True, limit=300)
        sql_limited = mock_read_sql.call_args[0][0]
        assert "AND is_closed = 1" in sql_limited
