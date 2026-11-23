"""
MySQL connection pool + upsert helpers shared by every collector.
Using INSERT ... ON DUPLICATE KEY UPDATE everywhere so re-running the
backfill or restarting a WS collector is always safe (idempotent).
"""
import mysql.connector
from mysql.connector import pooling
import logging
from contextlib import closing, contextmanager
from threading import Lock
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MYSQL_CONFIG, RETAILBOT2_DB_CONFIG

logger = logging.getLogger("db")

_pool = None
_retailbot2_pool = None
_funding_pool = None
_pool_lock = Lock()


def get_funding_pool(config=None, reset=False):
    """
    Dedicated connection pool for Binance Futures Funding Rate REST Collector.
    Uses only dedicated credentials loaded via load_funding_collector_config().
    Never falls back to global _pool or cms.
    """
    global _funding_pool
    with _pool_lock:
        if reset and _funding_pool is not None:
            _funding_pool = None

        if _funding_pool is None:
            from config import load_funding_collector_config
            cfg = config or load_funding_collector_config()
            _funding_pool = pooling.MySQLConnectionPool(
                pool_name="funding_collector_pool",
                pool_size=3,
                init_command="SET time_zone = '+00:00'",
                **cfg.to_dict(),
            )
        return _funding_pool


def get_retailbot2_pool():
    """Compatibility pool for RetailBot dashboard helpers.
    It uses the same centralized MYSQL_CONFIG as every other component;
    this separate pool preserves existing callers only."""
    global _retailbot2_pool
    with _pool_lock:
        if _retailbot2_pool is None:
            import config
            _retailbot2_pool = pooling.MySQLConnectionPool(
                pool_name="retailbot2_pool",
                pool_size=3,
                init_command="SET time_zone = '+00:00'",
                **config.RETAILBOT2_DB_CONFIG,
            )
        return _retailbot2_pool


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            import config
            _pool = pooling.MySQLConnectionPool(
                pool_name="crypto_bot_pool",
                pool_size=10,
                init_command="SET time_zone = '+00:00'",  # force UTC session tz -- see note in schema.sql
                **config.MYSQL_CONFIG,
            )
        return _pool


@contextmanager
def _owned_cursor(conn, **kwargs):
    """Release a helper-owned connection on all paths, rolling back failed work."""
    with closing(conn):
        try:
            with closing(conn.cursor(**kwargs)) as cursor:
                yield cursor
        except Exception:
            conn.rollback()
            raise


def _strip_sql_comments(sql_text):
    """
    Remove full-line '--' comments before splitting on ';'. Without this,
    a semicolon written inside a comment (e.g. explanatory prose) would be
    misread as a statement terminator and corrupt the split.
    """
    lines = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines)


def init_schema():
    """Run schema.sql against the target DB. Safe to run repeatedly."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        raw = f.read()

    cleaned = _strip_sql_comments(raw)
    statements = cleaned.split(";")

    cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    conn = mysql.connector.connect(init_command="SET time_zone = '+00:00'", **cfg)
    with _owned_cursor(conn) as cur:
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
    logger.info("Schema initialized.")


def upsert_ohlcv(rows, pool=None, conn=None, commit=None):
    """
    rows: list of tuples matching the ohlcv table column order
    (symbol, market, timeframe, open_time, close_time, open, high, low,
     close, volume, quote_volume, num_trades, taker_buy_base_vol,
     taker_buy_quote_vol, is_closed)

    State Transition Rules (ON DUPLICATE KEY UPDATE):
    1. A partial / forming candle (is_closed=0) must NEVER overwrite an already verified
       closed candle (ohlcv.is_closed=1) with partial OHLCV data.
    2. A verified closed candle (VALUES(is_closed)=1) can update an earlier forming candle
       (ohlcv.is_closed=0) and transition is_closed from 0 to 1.
    3. Live in-progress updates (0 -> 0) update ongoing bar metrics.
    4. Duplicate finalized updates (1 -> 1) remain idempotent.

    Transaction Ownership Semantics:
    1. Function-created or Pooled connection (conn is None):
       Function acquires connection and owns the transaction boundary.
       Commits on success, rolls back on failure, and closes connection in finally.
    2. Caller-owned transaction (conn is not None, and either commit=False or conn.in_transaction):
       Caller owns transaction boundary. Function does NOT commit. Function creates a
       MariaDB SAVEPOINT before executemany; on batch failure, rolls back strictly to
       the savepoint to protect caller's prior uncommitted operations, then re-raises
       the original exception.
    3. Recovery-owned transaction (conn is not None, commit=True, or commit=None with not in_transaction):
       Function manages batch transaction on conn. Commits on success, rolls back on failure.
    4. Rollback safety:
       Rollback errors never mask or suppress the original database exception.
    """
    if not rows:
        return
    sql = """
        INSERT INTO ohlcv (
            symbol, market, timeframe, open_time, close_time,
            open, high, low, close, volume, quote_volume, num_trades,
            taker_buy_base_vol, taker_buy_quote_vol, is_closed
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            close_time = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.close_time, VALUES(close_time)),
            open = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.open, VALUES(open)),
            high = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.high, VALUES(high)),
            low = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.low, VALUES(low)),
            close = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.close, VALUES(close)),
            volume = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.volume, VALUES(volume)),
            quote_volume = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.quote_volume, VALUES(quote_volume)),
            num_trades = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.num_trades, VALUES(num_trades)),
            taker_buy_base_vol = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.taker_buy_base_vol, VALUES(taker_buy_base_vol)),
            taker_buy_quote_vol = IF(ohlcv.is_closed = 1 AND VALUES(is_closed) = 0, ohlcv.taker_buy_quote_vol, VALUES(taker_buy_quote_vol)),
            is_closed = GREATEST(ohlcv.is_closed, VALUES(is_closed))
    """
    if conn is not None:
        c = conn
        should_close = False
        if commit is None:
            should_commit = not getattr(c, "in_transaction", False)
        else:
            should_commit = bool(commit)
    else:
        p = pool or get_pool()
        c = p.get_connection()
        should_close = True
        should_commit = True if commit is None else bool(commit)

    cur = c.cursor()
    sp_name = None
    try:
        if not should_commit:
            sp_name = f"sp_upsert_ohlcv_{abs(hash(c)) % 10000000}"
            cur.execute(f"SAVEPOINT {sp_name};")

        cur.executemany(sql, rows)

        if should_commit:
            c.commit()
        elif sp_name:
            cur.execute(f"RELEASE SAVEPOINT {sp_name};")
    except Exception as original_exc:
        try:
            if should_commit:
                c.rollback()
            elif sp_name:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
        except Exception as rb_exc:
            logger.warning(f"Error during rollback/savepoint rollback: {rb_exc}")
        raise original_exc
    finally:
        try:
            cur.close()
        except Exception:
            pass
        if should_close:
            try:
                c.close()
            except Exception:
                pass


def upsert_funding(rows, pool=None):
    """rows: (symbol, event_time, mark_price, index_price, funding_rate, next_funding_time)"""
    if not rows:
        return
    sql = """
        INSERT INTO funding_rate (symbol, event_time, mark_price, index_price, funding_rate, next_funding_time)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            mark_price=VALUES(mark_price),
            index_price=VALUES(index_price),
            funding_rate=VALUES(funding_rate),
            next_funding_time=VALUES(next_funding_time)
    """
    p = pool or get_funding_pool()
    conn = p.get_connection()
    with _owned_cursor(conn) as cur:
        cur.executemany(sql, rows)
        conn.commit()


def get_latest_funding_time(symbol, pool=None):
    """Return max event_time (ms) for symbol, or None if no records exist."""
    p = pool or get_funding_pool()
    conn = p.get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute("SELECT MAX(event_time) FROM funding_rate WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def fetch_settled_funding_events(
    symbol: str,
    start_time: int,
    end_time: int,
    pool: Any = None,
) -> List[Tuple[int, float, float]]:
    """
    Fetch authentic settled funding events for a symbol between start_time and end_time (in ms).
    Bounded single database query for the entire simulation interval:
      SELECT event_time, mark_price, funding_rate
      FROM funding_rate
      WHERE symbol = %s
        AND event_time > %s
        AND event_time <= %s
        AND mark_price IS NOT NULL
      ORDER BY event_time ASC

    Validates:
    - mark_price must be non-null, numeric, and > 0.
    - funding_rate must be numeric and finite.
    Rejects non-finite funding data (raises ValueError).
    Returns list of (event_time, mark_price, funding_rate) tuples sorted by event_time asc.
    """
    p = pool or get_pool()
    conn = p.get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT event_time, mark_price, funding_rate
            FROM funding_rate
            WHERE symbol = %s
              AND event_time > %s
              AND event_time <= %s
              AND mark_price IS NOT NULL
            ORDER BY event_time ASC
            """,
            (symbol.strip().upper(), int(start_time), int(end_time)),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    events: List[Tuple[int, float, float]] = []
    for r in rows:
        evt_time = int(r["event_time"])
        mp_raw = r["mark_price"]
        fr_raw = r["funding_rate"]

        if mp_raw is None:
            continue
        try:
            mp = float(mp_raw)
            fr = float(fr_raw)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid non-numeric funding data for {symbol} at {evt_time}: {e}")

        if not math.isfinite(mp) or not math.isfinite(fr):
            raise ValueError(f"Non-finite funding data for {symbol} at {evt_time}: mark_price={mp}, funding_rate={fr}")
        if mp <= 0:
            raise ValueError(f"Invalid non-positive mark price for {symbol} at {evt_time}: mark_price={mp}")

        events.append((evt_time, mp, fr))

    return events


def upsert_open_interest(rows):
    """rows: (symbol, event_time, open_interest, open_interest_value)"""
    if not rows:
        return
    sql = """
        INSERT INTO open_interest (symbol, event_time, open_interest, open_interest_value)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            open_interest=VALUES(open_interest),
            open_interest_value=VALUES(open_interest_value)
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.executemany(sql, rows)
        conn.commit()


def insert_liquidations(rows):
    """rows: (symbol, side, order_type, time_in_force, orig_qty, price, avg_price, order_status, event_time)"""
    if not rows:
        return
    sql = """
        INSERT INTO liquidations
            (symbol, side, order_type, time_in_force, orig_qty, price, avg_price, order_status, event_time)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.executemany(sql, rows)
        conn.commit()


def heartbeat(collector_name, status="ok", detail=None, pool=None):
    sql = """
        INSERT INTO collector_heartbeat (collector_name, status, detail, last_beat_time)
        VALUES (%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
            status=VALUES(status),
            detail=VALUES(detail),
            last_beat_time=NOW()
    """
    p = pool or (get_funding_pool() if collector_name == "funding_poller" else get_pool())
    conn = p.get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute(sql, (collector_name, status, detail))
        conn.commit()


_sqlalchemy_engine = None


def get_sqlalchemy_engine():
    """
    Separate from the mysql-connector pool used everywhere else. pandas'
    read_sql does not reliably support a raw mysql-connector pooled
    connection object (pandas prints its own warning about this) -- it can
    intermittently throw "MySQL Connection not available" on the first
    query against a connection. A SQLAlchemy engine manages connection
    lifecycle correctly and avoids the issue entirely.
    """
    global _sqlalchemy_engine
    if _sqlalchemy_engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL

        # URL.create() properly escapes special characters (@, :, /, % etc.)
        # in the username/password -- building this with plain string
        # formatting breaks if the password contains any of those.
        url = URL.create(
            drivername="mysql+mysqlconnector",
            username=MYSQL_CONFIG["user"],
            password=MYSQL_CONFIG["password"],
            host=MYSQL_CONFIG["host"],
            port=MYSQL_CONFIG["port"],
            database=MYSQL_CONFIG["database"],
        )
        _sqlalchemy_engine = create_engine(
            url,
            pool_size=5,
            pool_recycle=3600,   # avoid stale/timed-out connections in long-running processes
            pool_pre_ping=True,  # test each connection before handing it out; transparently
                                  # replaces a stale/dead one instead of raising "Connection not available"
            connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
    return _sqlalchemy_engine



def fetch_latest_open_candle(symbol, market, timeframe):
    """Return the current, still-forming candle's raw OHLCV, or None.

    Strategy signals must use fully closed candles, but a paper entry is
    simulated at the *next* candle's open. The forming candle's feature row
    may not exist yet, so query raw OHLCV rather than joining the features
    table. The caller must validate that this candle immediately follows the
    latest closed candle before using it for an entry.
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol=%s AND market=%s AND timeframe=%s AND is_closed=0
            ORDER BY open_time DESC
            LIMIT 1
            """,
            (symbol, market, timeframe),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return {
            "open_time": int(row["open_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
    finally:
        conn.close()



def fetch_ohlcv_df(symbol, market, timeframe, closed_only=True):
    """
    Returns the OHLCV history for one symbol/market/timeframe as a
    pandas DataFrame, sorted ascending by open_time, numeric dtypes (not
    Decimal) so indicator math works correctly.

    closed_only: if True (default), filters WHERE is_closed = 1 so that
    forming / incomplete candles are excluded from indicator computation
    and strategy feature pipelines. Pass closed_only=False only when
    explicitly required for diagnostics or forming-candle analysis.
    """
    import pandas as pd

    closed_clause = "AND is_closed = 1" if closed_only else ""
    sql = f"""
        SELECT open_time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol=%s AND market=%s AND timeframe=%s {closed_clause}
        ORDER BY open_time ASC
    """
    engine = get_sqlalchemy_engine()
    df = pd.read_sql(sql, engine, params=(symbol, market, timeframe))

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def upsert_features(rows, batch_size=2000):
    """
    rows: list of tuples matching the features table column order
    (symbol, market, timeframe, open_time, rsi, macd, macd_signal, macd_hist,
     atr, atr_pct, volume_sma, volume_ratio, support, resistance,
     swing_high, swing_low, fib_0, fib_236, fib_382, fib_5, fib_618,
     fib_786, fib_1)

    Batched to avoid exceeding MySQL's max_allowed_packet on large result
    sets (e.g. a 15m timeframe's full history can be 70,000+ rows -- sending
    that as one giant executemany() can silently kill the connection with
    "MySQL Connection not available" if the combined packet is too large).
    """
    if not rows:
        return
    sql = """
        INSERT INTO features (
            symbol, market, timeframe, open_time, rsi, macd, macd_signal, macd_hist,
            atr, atr_pct, volume_sma, volume_ratio, support, resistance,
            swing_high, swing_low, fib_0, fib_236, fib_382, fib_5, fib_618, fib_786, fib_1
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            rsi=VALUES(rsi), macd=VALUES(macd), macd_signal=VALUES(macd_signal),
            macd_hist=VALUES(macd_hist), atr=VALUES(atr), atr_pct=VALUES(atr_pct),
            volume_sma=VALUES(volume_sma), volume_ratio=VALUES(volume_ratio),
            support=VALUES(support), resistance=VALUES(resistance),
            swing_high=VALUES(swing_high), swing_low=VALUES(swing_low),
            fib_0=VALUES(fib_0), fib_236=VALUES(fib_236), fib_382=VALUES(fib_382),
            fib_5=VALUES(fib_5), fib_618=VALUES(fib_618), fib_786=VALUES(fib_786),
            fib_1=VALUES(fib_1)
    """
    # MySQL/MariaDB can't take NaN directly -- convert to None so NULL is stored
    clean_rows = [
        tuple(None if isinstance(v, float) and v != v else v for v in row)
        for row in rows
    ]

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        for i in range(0, len(clean_rows), batch_size):
            chunk = clean_rows[i:i + batch_size]
            cur.executemany(sql, chunk)
            conn.commit()
        cur.close()
    finally:
        conn.close()


def fetch_ohlcv_with_features_df(symbol, market, timeframe, closed_only=True, include_funding=False, limit=None):
    """
    Returns ohlcv joined with features for one symbol/market/timeframe,
    sorted ascending by open_time -- the exact dataset the backtester (and
    later, the live signal engine) evaluates the strategy against.

    closed_only=True excludes any still-forming candle (is_closed=0) --
    always True for backtesting historical data; the live engine will want
    this too, since a still-forming candle's indicators aren't final yet.

    limit: if specified, fetches only the most recent `limit` closed candles,
    enabling high-performance incremental signal evaluation for live paper trading.

    include_funding=True adds a 'funding_rate' column, sourced from the
    funding_rate table (a futures-market concept, but usable as a
    sentiment/positioning overlay on spot OHLCV too -- this is a common
    real technique, and the funding_rate table has no 'market' column
    since funding is inherently a futures-only concept regardless of which
    market's price data it's being joined onto). No lookahead: uses
    pd.merge_asof(direction='backward') on funding's own event_time --
    only funding settlements that had already occurred by a given candle's
    open_time are visible to it.
    """
    import pandas as pd

    params = {"symbol": symbol, "market": market, "timeframe": timeframe}
    if limit is not None and int(limit) > 0:
        params["limit"] = int(limit)
        closed_clause = "AND is_closed = 1" if closed_only else ""
        sql = f"""
            SELECT o.open_time, o.close_time, o.open, o.high, o.low, o.close, o.volume, o.is_closed,
                   f.rsi, f.macd, f.macd_signal, f.macd_hist, f.atr, f.atr_pct,
                   f.volume_sma, f.volume_ratio, f.support, f.resistance,
                   f.swing_high, f.swing_low, f.fib_0, f.fib_236, f.fib_382,
                   f.fib_5, f.fib_618, f.fib_786, f.fib_1
            FROM (
                SELECT open_time, close_time, open, high, low, close, volume, is_closed, symbol, market, timeframe
                FROM ohlcv
                WHERE symbol=%(symbol)s AND market=%(market)s AND timeframe=%(timeframe)s {closed_clause}
                ORDER BY open_time DESC
                LIMIT %(limit)s
            ) o
            LEFT JOIN features f
              ON f.symbol = o.symbol AND f.market = o.market AND f.timeframe = o.timeframe
             AND f.open_time = o.open_time
            ORDER BY o.open_time ASC
        """
    else:
        closed_clause = "AND o.is_closed = 1" if closed_only else ""
        sql = f"""
            SELECT o.open_time, o.close_time, o.open, o.high, o.low, o.close, o.volume, o.is_closed,
                   f.rsi, f.macd, f.macd_signal, f.macd_hist, f.atr, f.atr_pct,
                   f.volume_sma, f.volume_ratio, f.support, f.resistance,
                   f.swing_high, f.swing_low, f.fib_0, f.fib_236, f.fib_382,
                   f.fib_5, f.fib_618, f.fib_786, f.fib_1
            FROM ohlcv o
            LEFT JOIN features f
              ON f.symbol = o.symbol AND f.market = o.market AND f.timeframe = o.timeframe
             AND f.open_time = o.open_time
            WHERE o.symbol=%(symbol)s AND o.market=%(market)s AND o.timeframe=%(timeframe)s {closed_clause}
            ORDER BY o.open_time ASC
        """
    engine = get_sqlalchemy_engine()
    df = pd.read_sql(sql, engine, params=params)

    if closed_only and (limit is None or int(limit) <= 0):
        df = df[df["is_closed"] == 1].reset_index(drop=True)

    if df.empty:
        if include_funding and "funding_rate" not in df.columns:
            df["funding_rate"] = pd.Series(dtype=float)
        return df

    df["open_time"] = df["open_time"].astype("int64")

    feature_cols = [
        "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct",
        "volume_sma", "volume_ratio", "support", "resistance",
        "swing_high", "swing_low", "fib_0", "fib_236", "fib_382",
        "fib_5", "fib_618", "fib_786", "fib_1",
    ]
    for col in feature_cols:
        if col not in df.columns:
            df[col] = float("nan")

    # In-memory feature calculation fallback:
    # Triggered when persisted feature rows are missing, stale, or incomplete on recent bars.
    needs_calc = False
    if len(df) > 0:
        latest_features = df[feature_cols].iloc[-1]
        if latest_features.isna().any():
            needs_calc = True
        elif len(df) > 50 and df[feature_cols].iloc[50:].isna().any().any():
            needs_calc = True

    if needs_calc:
        from config import (
            RSI_WINDOW, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD,
            VOLUME_SMA_PERIOD, HTF_S_R_LOOKBACK_BARS
        )
        from features.indicators import compute_all_features
        import time

        calc_params = {
            "RSI_WINDOW": RSI_WINDOW,
            "MACD_FAST": MACD_FAST,
            "MACD_SLOW": MACD_SLOW,
            "MACD_SIGNAL": MACD_SIGNAL,
            "ATR_PERIOD": ATR_PERIOD,
            "VOLUME_SMA_PERIOD": VOLUME_SMA_PERIOD,
            "HTF_S_R_LOOKBACK_BARS": HTF_S_R_LOOKBACK_BARS,
        }
        min_required = max(
            calc_params["RSI_WINDOW"],
            calc_params["MACD_SLOW"],
            calc_params["ATR_PERIOD"],
            calc_params["VOLUME_SMA_PERIOD"],
            calc_params["HTF_S_R_LOOKBACK_BARS"]
        ) + 1  # 51 bars

        if len(df) < min_required:
            logger.warning(
                "Insufficient OHLCV history for indicator warm-up for %s %s %s: "
                "available=%d, required>=%d bars",
                symbol, market, timeframe, len(df), min_required
            )

        missing_mask = df[feature_cols].isna().any(axis=1)
        missing_count = int(missing_mask.sum())
        t0 = time.perf_counter()

        try:
            ohlcv_input = pd.DataFrame({
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "volume": df["volume"].astype(float),
            }, index=df.index)

            computed_df = compute_all_features(ohlcv_input, calc_params)

            for col in feature_cols:
                if col in computed_df.columns:
                    mask = df[col].isna()
                    if mask.all():
                        df[col] = computed_df[col].astype(float)
                    elif mask.any():
                        df.loc[mask, col] = computed_df.loc[mask, col].astype(float)

            duration_ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "Calculated in-memory features for %s %s %s: %d/%d bars missing features (computed in %.2f ms)",
                symbol, market, timeframe, missing_count, len(df), duration_ms
            )
        except Exception as exc:
            logger.error(
                "In-memory feature calculation failed for %s %s %s: %s",
                symbol, market, timeframe, exc, exc_info=True
            )

    numeric_cols = [c for c in df.columns if c not in ("open_time", "is_closed")]
    for col in numeric_cols:
        df[col] = df[col].astype(float)

    if include_funding:
        funding_sql = """
            SELECT event_time, funding_rate
            FROM funding_rate
            WHERE symbol=%(symbol)s AND funding_rate IS NOT NULL
            ORDER BY event_time ASC
        """
        funding_df = pd.read_sql(funding_sql, engine, params={"symbol": symbol})

        if len(funding_df) == 0:
            # No funding data yet for this symbol (not backfilled, or all
            # NULL) -- an empty DataFrame defaults numeric columns to
            # 'object' dtype, which merge_asof can't merge against ohlcv's
            # int64 open_time. Add the column as NaN instead of merging.
            df["funding_rate"] = float("nan")
        else:
            funding_df["event_time"] = funding_df["event_time"].astype("int64")
            funding_df["funding_rate"] = funding_df["funding_rate"].astype(float)
            df = df.sort_values("open_time")
            funding_df = funding_df.sort_values("event_time")
            df = pd.merge_asof(
                df, funding_df,
                left_on="open_time", right_on="event_time",
                direction="backward",
            )
            df = df.drop(columns=["event_time"])

    return df


def insert_backtest_run(symbol, market, timeframe, strategy_name, params, data_start_time, data_end_time,
                         total_trades, win_rate_pct, expectancy_r, profit_factor, max_drawdown_r,
                         data_segment="train"):
    import json

    sql = """
        INSERT INTO backtest_runs (
            symbol, market, timeframe, strategy_name, data_segment, params_json,
            data_start_time, data_end_time, total_trades, win_rate_pct,
            expectancy_r, profit_factor, max_drawdown_r
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute(sql, (
            symbol, market, timeframe, strategy_name, data_segment, json.dumps(params),
            data_start_time, data_end_time, total_trades, win_rate_pct,
            expectancy_r, profit_factor, max_drawdown_r,
        ))
        conn.commit()
        run_id = cur.lastrowid
        return run_id



def insert_backtest_trades(run_id, trades, batch_size=2000):
    """trades: list of dicts as produced by backtest/strategies/*.py's run() functions."""
    if not trades:
        return
    sql = """
        INSERT INTO backtest_trades (
            run_id, direction, signal_open_time, entry_time, entry_price,
            exit_time, exit_price, exit_reason, stop_loss, take_profit,
            net_return_pct, r_multiple, holding_bars
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    rows = [
        (
            run_id, t["direction"], t["signal_open_time"], t["entry_time"], t["entry_price"],
            t["exit_time"], t["exit_price"], t["exit_reason"], t["stop_loss"], t["take_profit"],
            t["net_return_pct"], t["r_multiple"], t["holding_bars"],
        )
        for t in trades
    ]
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        for i in range(0, len(rows), batch_size):
            cur.executemany(sql, rows[i:i + batch_size])
            conn.commit()
        cur.close()
    finally:
        conn.close()


def upsert_news_events(rows):
    """
    rows: list of dicts with keys matching news_events columns:
        source, category, external_id, headline, url, symbols, country,
        impact, published_at, scheduled_at, actual_value, forecast_value,
        previous_value, raw_payload (already-serialized JSON string or None)
    Idempotent on (source, external_id) -- safe to re-poll the same window
    repeatedly (e.g. a calendar event's actual_value gets updated after
    release without creating a duplicate row).
    """
    if not rows:
        return
    sql = """
        INSERT INTO news_events (
            source, category, external_id, headline, url, symbols, country,
            impact, published_at, scheduled_at, actual_value, forecast_value,
            previous_value, raw_payload
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            headline=VALUES(headline),
            url=VALUES(url),
            symbols=VALUES(symbols),
            impact=VALUES(impact),
            actual_value=VALUES(actual_value),
            forecast_value=VALUES(forecast_value),
            previous_value=VALUES(previous_value),
            raw_payload=VALUES(raw_payload)
    """
    values = [
        (
            r["source"], r["category"], r["external_id"], r["headline"], r.get("url"),
            r.get("symbols"), r.get("country"), r.get("impact"), r.get("published_at"),
            r.get("scheduled_at"), r.get("actual_value"), r.get("forecast_value"),
            r.get("previous_value"), r.get("raw_payload"),
        )
        for r in rows
    ]
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.executemany(sql, values)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def get_active_paper_symbols():
    """(symbol, market, timeframe) tuples for currently active paper_configs.
    Deliberately implemented as a direct query here rather than importing
    paper.engine.get_paper_configs(), so that a lightweight poller such as
    ai/news_strategy_engine.py does not depend on paper.engine's import-time
    behaviour."""
    sql = "SELECT DISTINCT symbol, market, timeframe FROM paper_configs WHERE is_active = 1"
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def get_unprocessed_news_events(limit=100):
    """
    Rows not yet evaluated by the Phase 2 AI reasoning layer
    (processed_at IS NULL), newest first.
    """
    sql = """
        SELECT id, source, category, external_id, headline, url, symbols, country,
               impact, published_at, scheduled_at, actual_value, forecast_value,
               previous_value, raw_payload, fetched_at
        FROM news_events
        WHERE processed_at IS NULL
        ORDER BY COALESCE(published_at, scheduled_at, fetched_at) DESC
        LIMIT %s
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        return rows


def mark_news_events_processed(event_ids):
    """Mark news_events rows as evaluated so they aren't re-sent to the AI
    layer on the next poll, regardless of whether they crossed the
    confidence threshold."""
    if not event_ids:
        return
    sql = "UPDATE news_events SET processed_at = NOW() WHERE id IN (%s)" % (
        ",".join(["%s"] * len(event_ids))
    )
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute(sql, tuple(event_ids))
        conn.commit()


def insert_news_ai_signal(signal):
    """
    signal: dict with keys news_event_id, symbol, market, timeframe, bias,
    confidence, reasoning, invalidation_condition, time_horizon, trade_timing.
    acted_on defaults to FALSE / paper_position_id NULL -- Phase 3
    (ai/news_execution.py) updates those via mark_news_signal_acted() once
    it's made a final decision (opened a position or deliberately skipped).
    """
    sql = """
        INSERT INTO news_ai_signals (
            news_event_id, symbol, market, timeframe, bias, confidence, reasoning,
            invalidation_condition, time_horizon, trade_timing
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (
            signal["news_event_id"], signal["symbol"], signal.get("market"),
            signal.get("timeframe"), signal["bias"], signal["confidence"],
            signal.get("reasoning"), signal.get("invalidation_condition"),
            signal.get("time_horizon"), signal.get("trade_timing"),
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.close()
        return new_id
    finally:
        conn.close()


def get_unacted_news_signals(limit=20):
    """Return signals that are not finalized and are not currently claimed.

    A stale processing claim (>10 minutes) is eligible again so a crashed
    execution worker cannot strand a signal forever.
    """
    sql = """
        SELECT id, news_event_id, symbol, market, timeframe, bias, confidence,
               reasoning, invalidation_condition, time_horizon, trade_timing, created_at
        FROM news_ai_signals
        WHERE acted_on = FALSE
          AND (processing_at IS NULL OR processing_at < UTC_TIMESTAMP() - INTERVAL 10 MINUTE)
        ORDER BY created_at ASC
        LIMIT %s
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def claim_news_signal(signal_id):
    """Atomically claim one signal for execution.

    Returns True for exactly one concurrent worker; stale claims are
    recoverable after 10 minutes.
    """
    sql = """
        UPDATE news_ai_signals
        SET processing_at = UTC_TIMESTAMP()
        WHERE id = %s
          AND acted_on = FALSE
          AND (processing_at IS NULL OR processing_at < UTC_TIMESTAMP() - INTERVAL 10 MINUTE)
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (signal_id,))
        conn.commit()
        claimed = cur.rowcount == 1
        cur.close()
        return claimed
    finally:
        conn.close()


def release_news_signal_claim(signal_id):
    """Release a claim without finalizing the signal (used for future
    scheduled macro releases that must be retried on the next cycle)."""
    sql = "UPDATE news_ai_signals SET processing_at = NULL WHERE id = %s AND acted_on = FALSE"
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (signal_id,))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def get_news_event(event_id):
    sql = """
        SELECT id, source, category, external_id, headline, url, symbols, country,
               impact, published_at, scheduled_at, actual_value, forecast_value, previous_value
        FROM news_events WHERE id = %s
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (event_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


def mark_news_signal_acted(signal_id, paper_position_id=None, skip_reason=None):
    sql = """
        UPDATE news_ai_signals
        SET acted_on = TRUE, processing_at = NULL, paper_position_id = %s, skip_reason = %s
        WHERE id = %s AND acted_on = FALSE
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (paper_position_id, skip_reason, signal_id))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def count_news_trades_opened_today(strategy_name="news_ai_overlay"):
    """UTC-day count of positions opened by the given overlay strategy --
    used to enforce a daily trade cap (news headlines can cluster around a
    single narrative; this bounds how much of that a single day can act on)."""
    sql = """
        SELECT COUNT(*) AS n FROM paper_positions
        WHERE strategy_name = %s
          AND entry_time >= UNIX_TIMESTAMP(UTC_DATE()) * 1000
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute(sql, (strategy_name,))
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def get_technical_paper_config(symbol, market, timeframe, exclude_strategy="news_ai_overlay"):
    """The active *technical* (backtestable) strategy config for this
    symbol/market/timeframe, if any -- i.e. the Battle Royale winner. Used
    by Phase 3 both for the confluence guardrail (is there already an open
    technical position here?) and to mirror its capital allocation when
    auto-creating a paper_configs row for the news overlay strategy."""
    sql = """
        SELECT id, symbol, market, timeframe, strategy_name, is_active,
               allocated_capital, risk_per_trade_pct
        FROM paper_configs
        WHERE symbol = %s AND market = %s AND timeframe = %s
          AND strategy_name != %s AND is_active = 1
        LIMIT 1
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (symbol, market, timeframe, exclude_strategy))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


def ensure_overlay_paper_config(symbol, market, timeframe, strategy_name, allocated_capital, risk_per_trade_pct):
    """Idempotently creates (or leaves untouched) a paper_configs row for an
    overlay strategy like news_ai_overlay, so it has somewhere to read
    allocated_capital/risk_per_trade_pct from -- the same accounting contract
    every other strategy uses (see backtest/accounting.py). is_active=1 so
    the existing exit-management loop in sync_and_evaluate_paper_trading()
    picks it up automatically."""
    sql = """
        INSERT INTO paper_configs (symbol, market, timeframe, strategy_name, is_active,
                                     allocated_capital, risk_per_trade_pct)
        VALUES (%s,%s,%s,%s,1,%s,%s)
        ON DUPLICATE KEY UPDATE id = id
    """
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute(sql, (symbol, market, timeframe, strategy_name, allocated_capital, risk_per_trade_pct))
        conn.commit()


# ----------------------------------------------------------------------------
# News AI Overlay -- visibility panel read helpers (web/routes/news_routes.py)
# ----------------------------------------------------------------------------

def get_recent_news_signals(limit=50):
    """news_ai_signals joined with the news_event that triggered them, newest
    first -- everything the AI has evaluated highly enough to store, with
    enough context to judge whether it was a reasonable call."""
    limit = max(1, min(int(limit), 200))
    sql = """
        SELECT
            s.id, s.symbol, s.market, s.timeframe, s.bias, s.confidence,
            s.reasoning, s.invalidation_condition, s.time_horizon, s.trade_timing,
            s.acted_on, s.paper_position_id, s.skip_reason, s.created_at,
            e.headline, e.url, e.source, e.category, e.country, e.impact,
            e.scheduled_at, e.actual_value, e.forecast_value, e.previous_value
        FROM news_ai_signals s
        LEFT JOIN news_events e ON e.id = s.news_event_id
        ORDER BY s.created_at DESC
        LIMIT %s
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["confidence"] = int(r["confidence"]) if r["confidence"] is not None else None
            r["acted_on"] = bool(r["acted_on"])
            for dt_key in ("created_at", "scheduled_at"):
                if r.get(dt_key) is not None:
                    r[dt_key] = str(r[dt_key])
        return rows
    finally:
        conn.close()


def get_recent_news_events(limit=30):
    """Raw news_events feed, newest first -- what's actually been collected,
    independent of whether the AI layer judged it worth a signal. Gives a
    visibility panel a way to show "AI is running and seeing data" even
    during a quiet stretch with no high-confidence signals."""
    limit = max(1, min(int(limit), 200))
    sql = """
        SELECT id, source, category, headline, url, symbols, country, impact,
               published_at, scheduled_at, actual_value, forecast_value,
               previous_value, processed_at, fetched_at
        FROM news_events
        ORDER BY COALESCE(published_at, scheduled_at, fetched_at) DESC
        LIMIT %s
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for dt_key in ("published_at", "scheduled_at", "processed_at", "fetched_at"):
                if r.get(dt_key) is not None:
                    r[dt_key] = str(r[dt_key])
        return rows
    finally:
        conn.close()


def _serialize_news_heartbeat_rows(heartbeats):
    """Normalize DB heartbeat timestamps without turning SQL NULL into 'None'."""
    for heartbeat in heartbeats:
        timestamp = heartbeat.get("last_beat_time")
        heartbeat["last_beat_time"] = timestamp.isoformat(sep=" ") if isinstance(timestamp, datetime) else (
            str(timestamp) if timestamp is not None else None
        )
    return heartbeats


def get_news_overlay_stats():
    """Return dashboard KPIs for collection, AI decisions, execution and realized
    paper performance. Performance is intentionally based on CLOSED
    news_ai_overlay trades only; open positions are reported separately."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT COUNT(*) AS n FROM news_events")
        total_events = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM news_events WHERE processed_at IS NOT NULL")
        processed_events = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n, AVG(confidence) AS avg_conf FROM news_ai_signals")
        sig_row = cur.fetchone()
        total_signals = sig_row["n"]
        avg_confidence = round(float(sig_row["avg_conf"]), 1) if sig_row["avg_conf"] is not None else None

        cur.execute("SELECT COUNT(*) AS n FROM news_ai_signals WHERE paper_position_id IS NOT NULL")
        trades_opened = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM news_ai_signals WHERE acted_on = FALSE")
        pending_signals = cur.fetchone()["n"]

        # Realized performance comes from the canonical paper_trades ledger.
        cur.execute(
            """
            SELECT
                COUNT(*) AS closed_trades,
                COALESCE(SUM(net_pnl), 0) AS net_pnl,
                COALESCE(SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN r_multiple < 0 THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(CASE WHEN r_multiple > 0 THEN r_multiple ELSE 0 END), 0) AS gross_win_r,
                COALESCE(-SUM(CASE WHEN r_multiple < 0 THEN r_multiple ELSE 0 END), 0) AS gross_loss_r,
                COALESCE(SUM(r_multiple), 0) AS total_r
            FROM paper_trades
            WHERE strategy_name = 'news_ai_overlay'
            """
        )
        perf = cur.fetchone()
        closed_trades = int(perf["closed_trades"] or 0)
        total_r = float(perf["total_r"] or 0)
        gross_win_r = float(perf["gross_win_r"] or 0)
        gross_loss_r = float(perf["gross_loss_r"] or 0)
        profit_factor = round(gross_win_r / gross_loss_r, 3) if gross_loss_r > 0 else None
        expectancy_r = round(total_r / closed_trades, 3) if closed_trades else None
        win_rate = round((int(perf["wins"] or 0) / closed_trades) * 100, 1) if closed_trades else None

        # Max drawdown is calculated over realized cumulative R in close-time order.
        cur.execute(
            """
            SELECT r_multiple
            FROM paper_trades
            WHERE strategy_name = 'news_ai_overlay'
            ORDER BY exit_time, id
            """
        )
        peak = 0.0
        equity = 0.0
        max_dd_r = 0.0
        for row in cur.fetchall():
            equity += float(row["r_multiple"] or 0)
            peak = max(peak, equity)
            max_dd_r = max(max_dd_r, peak - equity)

        cur.execute(
            """
            SELECT collector_name, status, detail, last_beat_time
            FROM collector_heartbeat
            WHERE collector_name IN (
                'news_poller', 'econ_calendar_poller',
                'news_strategy_engine', 'news_execution'
            )
            """
        )
        heartbeats = _serialize_news_heartbeat_rows(cur.fetchall())
        cur.close()

        return {
            "total_events_collected": total_events,
            "events_evaluated_by_ai": processed_events,
            "high_confidence_signals": total_signals,
            "avg_confidence": avg_confidence,
            "trades_opened": trades_opened,
            "pending_signals": pending_signals,
            "performance": {
                "closed_trades": closed_trades,
                "wins": int(perf["wins"] or 0),
                "losses": int(perf["losses"] or 0),
                "win_rate": win_rate,
                "expectancy_r": expectancy_r,
                "profit_factor": profit_factor,
                "total_r": round(total_r, 3),
                "net_pnl": round(float(perf["net_pnl"] or 0), 2),
                "max_drawdown_r": round(max_dd_r, 3),
            },
            "heartbeats": heartbeats,
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# RetailBot -- read helpers for the web dashboard visibility panel.
# Uses the shared MYSQL_CONFIG database, same as the bot and all other components.
# ----------------------------------------------------------------------------

def get_retailbot2_open_trades(limit=100):
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute("""
            SELECT id, symbol, side, mode, entry_price, stop_price, target_price,
                   atr, rr, units, strategy, strategies_json, confluence_score,
                   reason, entry_time
            FROM rdt_trades WHERE status='OPEN'
            ORDER BY entry_time DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal):
                    r[k] = float(v)
            r["entry_time"] = str(r["entry_time"]) if r.get("entry_time") else None
        return rows


def get_retailbot2_recent_trades(limit=50):
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute("""
            SELECT t.id, t.symbol, t.side, t.mode, t.entry_price, t.stop_price, t.target_price,
                   t.units, t.strategy, t.confluence_score, t.status, t.exit_price,
                   t.exit_reason, t.net_pnl, t.pnl_percent, t.entry_time, t.exit_time
            FROM rdt_trades t
            LEFT JOIN rdt_strategy_lifecycle l
              ON l.strategy_id = t.strategy AND l.mode = t.mode
            WHERE t.status='CLOSED'
              AND COALESCE(l.report_enabled, TRUE) = TRUE
            ORDER BY t.exit_time DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal):
                    r[k] = float(v)
            for dt_key in ("entry_time", "exit_time"):
                if r.get(dt_key) is not None:
                    r[dt_key] = str(r[dt_key])
        return rows


def get_retailbot2_outbox_counts():
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute("SELECT status, COUNT(*) FROM rdt_telegram_outbox GROUP BY status")
        return {status: int(count) for status, count in cur.fetchall()}


def get_retailbot2_stats():
    """Per-strategy/mode win-rate stats plus overall equity summary
    (initial_capital isn't stored in this DB -- the dashboard adds it from
    its own config if needed; realized_pnl sums are enough on their own to
    show relative performance)."""
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute("""
            SELECT t.strategy, t.mode,
                   COUNT(*) AS total_trades,
                   SUM(CASE WHEN t.net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN t.net_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(t.net_pnl), 0) AS total_pnl,
                   MAX(t.exit_time) AS last_updated
            FROM rdt_trades t
            LEFT JOIN rdt_strategy_lifecycle l
              ON l.strategy_id = t.strategy AND l.mode = t.mode
            WHERE t.status='CLOSED'
              AND COALESCE(l.report_enabled, TRUE) = TRUE
            GROUP BY t.strategy, t.mode
            ORDER BY t.mode, t.strategy
        """)
        stats_rows = cur.fetchall()
        for r in stats_rows:
            if isinstance(r.get("total_pnl"), Decimal):
                r["total_pnl"] = float(r["total_pnl"])
            r["last_updated"] = str(r["last_updated"]) if r.get("last_updated") else None

        cur.execute("""
            SELECT t.mode, COUNT(*) AS open_count FROM rdt_trades t
            LEFT JOIN rdt_strategy_lifecycle l ON l.strategy_id = t.strategy AND l.mode = t.mode
            WHERE t.status='OPEN' AND COALESCE(l.report_enabled, TRUE) = TRUE GROUP BY t.mode
        """)
        open_counts = {r["mode"]: r["open_count"] for r in cur.fetchall()}

        cur.execute("""
            SELECT t.mode, COALESCE(SUM(t.net_pnl), 0) AS realized_pnl,
                   COUNT(*) AS closed_count,
                   SUM(CASE WHEN t.net_pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM rdt_trades t
            LEFT JOIN rdt_strategy_lifecycle l
              ON l.strategy_id = t.strategy AND l.mode = t.mode
            WHERE t.status='CLOSED'
              AND COALESCE(l.report_enabled, TRUE) = TRUE
            GROUP BY t.mode
        """)
        pnl_rows = cur.fetchall()
        by_mode = {}
        for r in pnl_rows:
            closed = r["closed_count"] or 0
            wins = r["wins"] or 0
            by_mode[r["mode"]] = {
                "realized_pnl": float(r["realized_pnl"]),
                "closed_count": closed,
                "wins": wins,
                "win_rate": round(wins / closed * 100, 1) if closed > 0 else None,
                "open_count": open_counts.get(r["mode"], 0),
            }
        for mode in ("shadow", "inverse"):
            by_mode.setdefault(mode, {
                "realized_pnl": 0.0, "closed_count": 0, "wins": 0,
                "win_rate": None, "open_count": open_counts.get(mode, 0),
            })
        return {"by_strategy": stats_rows, "by_mode": by_mode}


# ----------------------------------------------------------------------------
# Screen/screening.py (smart-money volume screener) -- read helpers.
# Uses the main crypto_signals pool since this table lives there.
# ----------------------------------------------------------------------------

def get_recent_smart_money_signals(limit=50):
    limit = max(1, min(int(limit), 200))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, symbol, signal_type, rvol, volume, quote_volume, price,
                   price_change_pct, volume_velocity, quality_score, candle_time,
                   telegram_sent, detected_at
            FROM smart_money_signals WHERE parent_watch_id IS NULL
            ORDER BY detected_at DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for k in ("rvol", "volume", "quote_volume", "price", "price_change_pct", "volume_velocity", "quality_score"):
                if isinstance(r.get(k), Decimal):
                    r[k] = float(r[k])
            r["telegram_sent"] = bool(r["telegram_sent"])
            r["detected_at"] = str(r["detected_at"])
        return rows
    finally:
        conn.close()


def get_smart_money_stats():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS n FROM smart_money_signals WHERE parent_watch_id IS NULL")
        total = cur.fetchone()["n"]
        cur.execute("""
            SELECT COUNT(*) AS n FROM smart_money_signals
            WHERE parent_watch_id IS NULL AND detected_at >= NOW() - INTERVAL 24 HOUR
        """)
        last_24h = cur.fetchone()["n"]
        cur.execute("""
            SELECT signal_type, COUNT(*) AS n FROM smart_money_signals
            WHERE parent_watch_id IS NULL AND detected_at >= NOW() - INTERVAL 24 HOUR
            GROUP BY signal_type
        """)
        by_type = {r["signal_type"]: r["n"] for r in cur.fetchall()}
        cur.close()
        return {"total_signals": total, "signals_last_24h": last_24h, "by_type_last_24h": by_type}
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Bot control -- read/write for the "enable + tunable params" toggle panels.
# RetailBot2's bot_control/lifecycle tables and the Screener's
# screener_control/heartbeat/signal tables are migration-managed. The web API
# and their workers verify schema presence at startup; these route helpers
# assume migrations have already been applied and never create schema.
# ----------------------------------------------------------------------------

# Explicit safe-list mirrored from paper/retailbot2.py's own
# _tunable_fields -- kept here too so a malformed/forged request can be
# rejected at the API layer before it ever reaches the bot process, not
# just relying on the bot's own filter as the only line of defense.
RETAILBOT2_TUNABLE_FIELDS = {
    "rsi_oversold", "rsi_overbought", "rsi_zone_width",
    "sr_touch_threshold", "sr_min_touches",
    "bb_std", "bb_proximity_pct",
    "pattern_tolerance", "pattern_min_bars_apart",
    "spike_atr_multiplier",
    "atr_sl_multiplier", "rr_ratio", "risk_per_trade",
    "max_open_trades_per_mode", "max_trades_per_symbol",
    "min_trade_interval_hours",
}

SCREENER_TUNABLE_FIELDS = {
    "rvol_multiplier", "divergence_max_price_change", "velocity_threshold",
    "enable_divergence_detection", "enable_velocity_detection",
    "max_alerts_per_hour", "max_alerts_per_symbol_per_hour", "alert_cooldown_sec",
    "require_confluence", "min_signal_rvol", "min_signal_velocity",
    "min_quote_volume_24h", "min_market_cap_usd", "min_quality_score",
    "min_alert_quality_score", "min_alert_quote_volume", "max_signal_age_sec",
    "expired_signal_retention_days", "expired_signal_cleanup_interval_days",
    "telegram_enabled",
}


def verify_retailbot2_schema():
    """Verify migration-managed RetailBot2 schema without issuing DDL/DML."""
    conn = get_retailbot2_pool().get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        required_tables = ("rdt_trades", "rdt_strategy_stats", "bot_control", "rdt_strategy_lifecycle")
        placeholders = ", ".join(["%s"] * len(required_tables))
        cur.execute(
            f"""SELECT TABLE_NAME AS table_name
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ({placeholders})""",
            required_tables,
        )
        existing = {str(row["table_name"]) for row in cur.fetchall()}
        missing = sorted(set(required_tables) - existing)
        if missing:
            raise RuntimeError(
                "RetailBot2 schema is missing tables "
                f"({', '.join(missing)}); run 'python -m db.migrate' before startup."
            )
        cur.execute(
            """SELECT COLUMN_NAME AS column_name
               FROM information_schema.columns
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
            ("rdt_trades", "telegram_enabled"),
        )
        if not cur.fetchone():
            raise RuntimeError(
                "RetailBot2 schema is missing rdt_trades.telegram_enabled; "
                "run 'python -m db.migrate' before startup."
            )
    finally:
        try:
            if cur is not None:
                cur.close()
        finally:
            conn.close()


def _retailbot2_lifecycle_flag(value):
    """Parse persisted lifecycle flags without treating non-empty strings as true.

    MariaDB BOOLEAN columns normally return 0/1, but fail closed if a legacy
    adapter or imported row supplies textual or malformed values.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    return False


def ensure_retailbot2_lifecycle_table():
    """Backward-compatible alias for read-only RetailBot2 schema verification.

    The schema is managed by db.migrate; this legacy entry point must never
    issue DDL or DML during runtime startup.
    """
    return verify_retailbot2_schema()


def get_retailbot2_strategy_lifecycle_settings():
    """Read UI-managed strategy settings.

    An empty table is valid and means worker defaults apply; no promotion is
    implicitly enabled. Query/connection errors propagate so callers cannot
    mistake an unavailable persistence layer for an empty configuration.
    """
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT strategy_id, mode, evaluation_enabled, paper_enabled,
                       telegram_enabled, report_enabled, auto_promote_enabled,
                       updated_at
                FROM rdt_strategy_lifecycle
                ORDER BY strategy_id, mode
            """)
            rows = cur.fetchall()
        finally:
            cur.close()
        for row in rows:
            for key in ("evaluation_enabled", "paper_enabled", "telegram_enabled",
                        "report_enabled", "auto_promote_enabled"):
                row[key] = _retailbot2_lifecycle_flag(row[key])
            if row.get("updated_at") is not None:
                row["updated_at"] = str(row["updated_at"])
        return rows
    finally:
        conn.close()


def set_retailbot2_strategy_lifecycle_setting(strategy_id, mode, settings):
    """Atomically replace one strategy/mode lifecycle setting row."""
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn) as cur:
        # The lifecycle table is provisioned and verified by the migration/startup
        # path. Runtime writes must not call a table-creation helper here.
        cur.execute("""
            INSERT INTO rdt_strategy_lifecycle
                (strategy_id, mode, evaluation_enabled, paper_enabled,
                 telegram_enabled, report_enabled, auto_promote_enabled)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                evaluation_enabled=VALUES(evaluation_enabled),
                paper_enabled=VALUES(paper_enabled),
                telegram_enabled=VALUES(telegram_enabled),
                report_enabled=VALUES(report_enabled),
                auto_promote_enabled=VALUES(auto_promote_enabled)
        """, (
            strategy_id, mode, settings["evaluation_enabled"],
            settings["paper_enabled"], settings["telegram_enabled"],
            settings["report_enabled"], settings["auto_promote_enabled"]
        ))
        conn.commit()


def get_retailbot2_control():
    conn = get_retailbot2_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute("SELECT enabled, overrides_json, updated_at FROM bot_control WHERE id=1")
        row = cur.fetchone()
        if not row:
            return {"enabled": False, "overrides": {}, "updated_at": None}
        raw_overrides = row.get("overrides_json")
        raw_enabled = bool(row["enabled"])
        if raw_enabled and raw_overrides is None:
            raw_enabled = False
        overrides = {}
        if raw_overrides:
            try:
                parsed = json.loads(raw_overrides)
                if isinstance(parsed, dict):
                    overrides = parsed
                else:
                    raw_enabled = False
                    overrides = {}
            except (json.JSONDecodeError, TypeError):
                raw_enabled = False
                overrides = {}
        return {
            "enabled": raw_enabled,
            "overrides": overrides,
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }


def set_retailbot2_control(enabled, overrides):
    """Persist the full dashboard control state, creating its singleton row if absent."""
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO bot_control (id, enabled, overrides_json)
               VALUES (1, %s, %s)
               ON DUPLICATE KEY UPDATE
                   enabled=VALUES(enabled),
                   overrides_json=VALUES(overrides_json)""",
            (enabled, json.dumps(overrides)),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def update_screener_heartbeat(*, pid=None, timeframe=None, symbols_monitored=None, last_signal_at=None, last_universe_refresh_at=None):
    """Persist lightweight process telemetry so the dashboard can distinguish
    a healthy-but-quiet screener from a process that is not running.

    Screener candle timestamps arrive as ISO-8601 strings (for example,
    2026-10-06T22:37:00Z), while MariaDB DATETIME expects a datetime
    value. Normalize ISO strings to UTC-naive datetimes before binding them.
    """
    def _normalize_datetime(value):
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except ValueError:
                logger.warning("Invalid screener heartbeat datetime: %r", value)
                return None
        return value

    last_signal_at = _normalize_datetime(last_signal_at)
    last_universe_refresh_at = _normalize_datetime(last_universe_refresh_at)

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO screener_heartbeat
                (id, pid, last_beat_at, timeframe, symbols_monitored, last_signal_at, last_universe_refresh_at)
            VALUES (1, %s, UTC_TIMESTAMP(), %s, COALESCE(%s, 0), %s, %s)
            ON DUPLICATE KEY UPDATE
                pid=COALESCE(VALUES(pid), pid),
                last_beat_at=UTC_TIMESTAMP(),
                timeframe=COALESCE(VALUES(timeframe), timeframe),
                symbols_monitored=COALESCE(%s, symbols_monitored),
                last_signal_at=COALESCE(VALUES(last_signal_at), last_signal_at),
                last_universe_refresh_at=COALESCE(VALUES(last_universe_refresh_at), last_universe_refresh_at)
            """,
            (pid, timeframe, symbols_monitored, last_signal_at, last_universe_refresh_at, symbols_monitored),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def get_screener_status():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT pid, last_beat_at, timeframe, symbols_monitored, last_signal_at,
                       last_universe_refresh_at
                FROM screener_heartbeat
                WHERE id=1
                """
            )
            row = cur.fetchone()
        except mysql.connector.Error as exc:
            cur.close()
            if exc.errno == 1146:
                return {
                    "running": False,
                    "database_ok": True,
                    "timeframe": None,
                    "symbols_monitored": 0,
                    "last_beat_at": None,
                    "last_signal_at": None,
                    "last_universe_refresh": None,
                }
            raise
        cur.close()
        if not row:
            return {
                "running": False,
                "database_ok": True,
                "timeframe": None,
                "symbols_monitored": 0,
                "last_beat_at": None,
                "last_signal_at": None,
                "last_universe_refresh": None,
            }
        last_beat_at = row.get("last_beat_at")
        running = bool(last_beat_at and (datetime.utcnow() - last_beat_at).total_seconds() <= 150)
        for key in ("last_beat_at", "last_signal_at", "last_universe_refresh_at"):
            if row.get(key) is not None:
                row[key] = str(row[key])
        return {
            "running": running,
            "database_ok": True,
            "pid": row.get("pid"),
            "timeframe": row.get("timeframe"),
            "symbols_monitored": int(row.get("symbols_monitored") or 0),
            "last_beat_at": row.get("last_beat_at"),
            "last_signal_at": row.get("last_signal_at"),
            "last_universe_refresh": row.get("last_universe_refresh_at"),
        }
    finally:
        conn.close()


def get_screener_control():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT enabled, overrides_json, updated_at FROM screener_control WHERE id=1")
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"enabled": True, "overrides": {}, "updated_at": None}
        return {
            "enabled": bool(row["enabled"]),
            "overrides": json.loads(row["overrides_json"]) if row["overrides_json"] else {},
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        }
    finally:
        conn.close()


def set_screener_control(enabled, overrides):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO screener_control (id, enabled, overrides_json)
               VALUES (1, %s, %s)
               ON DUPLICATE KEY UPDATE
                   enabled=VALUES(enabled),
                   overrides_json=VALUES(overrides_json)""",
            (enabled, json.dumps(overrides)),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# User-configurable backtest winner promotion policy and audit history
# ---------------------------------------------------------------------------

_STRATEGY_AUTO_PROMOTION_DEFAULTS = {
    "enabled": False,
    "min_test_trades": 30,
    "min_profit_factor": 1.2,
    "min_expectancy_r": 0.05,
    "max_drawdown_r": 8.0,
    "min_rank_score": 0.1,
    "require_holdout": True,
    "send_telegram": True,
}


def verify_strategy_auto_promotion_schema():
    """Verify migration-managed auto-promotion tables without issuing DDL/DML."""
    conn = get_pool().get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        required_tables = ("strategy_auto_promotion_policy", "strategy_promotion_audit")
        placeholders = ", ".join(["%s"] * len(required_tables))
        cur.execute(
            f"""SELECT TABLE_NAME AS table_name
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ({placeholders})""",
            required_tables,
        )
        existing = {str(row["table_name"]) for row in cur.fetchall()}
        missing_tables = sorted(set(required_tables) - existing)
        if missing_tables:
            raise RuntimeError(
                "Auto-promotion schema is missing tables "
                f"({', '.join(missing_tables)}); run 'python -m db.migrate' before startup."
            )

        required_columns = {
            "strategy_auto_promotion_policy": {
                "id", "enabled", "min_test_trades", "min_profit_factor",
                "min_expectancy_r", "max_drawdown_r", "min_rank_score",
                "require_holdout", "send_telegram", "updated_at",
            },
            "strategy_promotion_audit": {
                "decision_hash", "symbol", "market", "timeframe", "data_segment",
                "strategy_name", "status", "reason", "metrics_json", "criteria_json",
                "paper_config_id", "telegram_result", "created_at", "updated_at",
            },
        }
        conditions = []
        params = []
        for table, columns in required_columns.items():
            for column in sorted(columns):
                conditions.append("(TABLE_NAME = %s AND COLUMN_NAME = %s)")
                params.extend((table, column))
        cur.execute(
            """SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name
               FROM information_schema.columns
               WHERE TABLE_SCHEMA = DATABASE() AND ("""
            + " OR ".join(conditions)
            + ")",
            tuple(params),
        )
        actual = {(str(row["table_name"]), str(row["column_name"])) for row in cur.fetchall()}
        missing_columns = [
            f"{table}.{column}"
            for table, columns in required_columns.items()
            for column in sorted(columns)
            if (table, column) not in actual
        ]
        if missing_columns:
            raise RuntimeError(
                "Auto-promotion schema is outdated "
                f"({', '.join(missing_columns)}); run 'python -m db.migrate' before startup."
            )
    finally:
        try:
            if cur is not None:
                cur.close()
        finally:
            conn.close()


def get_strategy_auto_promotion_policy():
    conn = get_pool().get_connection()
    with _owned_cursor(conn, dictionary=True) as cur:
        cur.execute("SELECT * FROM strategy_auto_promotion_policy WHERE id=1")
        row = cur.fetchone() or {}
    policy = dict(_STRATEGY_AUTO_PROMOTION_DEFAULTS)
    policy.update(row)
    for key in ("enabled", "require_holdout", "send_telegram"):
        value = policy[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value in (0, 1):
            policy[key] = bool(value)
            continue
        raise ValueError(f"Invalid persisted auto-promotion policy: {key} must be boolean/0/1")
    min_trades = policy["min_test_trades"]
    if type(min_trades) is not int or not 30 <= min_trades <= 10000:
        raise ValueError(
            "Invalid persisted auto-promotion policy: min_test_trades must be an integer from 30 to 10000"
        )

    numeric_bounds = {
        "min_profit_factor": (1.0, 10.0, True),
        "min_expectancy_r": (0.0, 10.0, True),
        "max_drawdown_r": (0.0, 100.0, False),
        "min_rank_score": (0.01, 1.0, True),
    }
    for key, (minimum, maximum, minimum_inclusive) in numeric_bounds.items():
        raw_value = policy[key]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, Decimal)):
            raise ValueError(
                f"Invalid persisted auto-promotion policy: {key} must be a finite number"
            )
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(
                f"Invalid persisted auto-promotion policy: {key} must be a finite number"
            )
        if (value < minimum if minimum_inclusive else value <= minimum) or value > maximum:
            raise ValueError(
                f"Invalid persisted auto-promotion policy: {key} is outside the supported range"
            )
        policy[key] = value
    policy["updated_at"] = str(policy.get("updated_at")) if policy.get("updated_at") else None
    return policy


def set_strategy_auto_promotion_policy(policy):
    # Validate at the persistence boundary as well as the API boundary.
    # In Python, bool("false") is True, so coercing arbitrary values here
    # could persist an unintended enabled/Telegram/holdout flag.
    normalized_flags = {}
    for key in ("enabled", "require_holdout", "send_telegram"):
        value = policy[key]
        if isinstance(value, bool):
            normalized_flags[key] = value
        elif type(value) is int and value in (0, 1):
            normalized_flags[key] = bool(value)
        else:
            raise ValueError(f"Invalid auto-promotion policy: {key} must be boolean/0/1")

    # Validate the full policy at the persistence boundary. The HTTP model is
    # not the only possible caller, and int()/float() alone silently coerce
    # values such as 30.5, "30", or NaN into unsafe persisted policy.
    min_trades = policy["min_test_trades"]
    if type(min_trades) is not int or not 30 <= min_trades <= 10000:
        raise ValueError("Invalid auto-promotion policy: min_test_trades must be an integer from 30 to 10000")

    numeric_bounds = {
        "min_profit_factor": (1.0, 10.0, True),
        "min_expectancy_r": (0.0, 10.0, True),
        "max_drawdown_r": (0.0, 100.0, False),
        "min_rank_score": (0.01, 1.0, True),
    }
    normalized_numeric = {}
    for key, (minimum, maximum, minimum_inclusive) in numeric_bounds.items():
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Invalid auto-promotion policy: {key} must be a finite number")
        if (value < minimum if minimum_inclusive else value <= minimum) or value > maximum:
            lower = "[" if minimum_inclusive else "("
            raise ValueError(f"Invalid auto-promotion policy: {key} must be in {lower}{minimum}, {maximum}]")
        normalized_numeric[key] = float(value)

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO strategy_auto_promotion_policy
                (id, enabled, min_test_trades, min_profit_factor, min_expectancy_r,
                 max_drawdown_r, min_rank_score, require_holdout, send_telegram)
            VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                enabled=VALUES(enabled),
                min_test_trades=VALUES(min_test_trades),
                min_profit_factor=VALUES(min_profit_factor),
                min_expectancy_r=VALUES(min_expectancy_r),
                max_drawdown_r=VALUES(max_drawdown_r),
                min_rank_score=VALUES(min_rank_score),
                require_holdout=VALUES(require_holdout),
                send_telegram=VALUES(send_telegram)
        """, (
            normalized_flags["enabled"], min_trades,
            normalized_numeric["min_profit_factor"], normalized_numeric["min_expectancy_r"],
            normalized_numeric["max_drawdown_r"], normalized_numeric["min_rank_score"],
            normalized_flags["require_holdout"], normalized_flags["send_telegram"],
        ))
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_strategy_promotion_decision(decision_hash):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT decision_hash, status, reason, paper_config_id, telegram_result "
            "FROM strategy_promotion_audit WHERE decision_hash=%s",
            (decision_hash,),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


def record_strategy_promotion_decision(decision):
    """Claim one metrics+policy decision exactly once to prevent duplicate
    deployments/Telegram announcements from concurrent or repeated runs."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT IGNORE INTO strategy_promotion_audit
                (decision_hash, symbol, market, timeframe, data_segment,
                 strategy_name, status, reason, metrics_json, criteria_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            decision["decision_hash"], decision["symbol"], decision["market"],
            decision["timeframe"], decision["data_segment"], decision["strategy_name"],
            decision["status"], decision["reason"][:500], decision["metrics_json"],
            decision["criteria_json"],
        ))
        inserted = cur.rowcount == 1
        conn.commit()
        cur.close()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_strategy_promotion_decision(decision_hash, *, status, reason,
                                       paper_config_id=None, telegram_result=None):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE strategy_promotion_audit
            SET status=%s, reason=%s, paper_config_id=%s, telegram_result=%s
            WHERE decision_hash=%s
        """, (status, str(reason)[:500], paper_config_id, telegram_result, decision_hash))
        updated = cur.rowcount == 1
        conn.commit()
        cur.close()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_strategy_promotion_audit(limit=25):
    limit = max(1, min(int(limit), 100))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT decision_hash, symbol, market, timeframe, data_segment,
                   strategy_name, status, reason, paper_config_id, telegram_result,
                   created_at, updated_at
            FROM strategy_promotion_audit
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for row in rows:
            row["created_at"] = str(row["created_at"]) if row.get("created_at") else None
            row["updated_at"] = str(row["updated_at"]) if row.get("updated_at") else None
        return rows
    finally:
        conn.close()
