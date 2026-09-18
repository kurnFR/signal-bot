"""
MySQL connection pool + upsert helpers shared by every collector.
Using INSERT ... ON DUPLICATE KEY UPDATE everywhere so re-running the
backfill or restarting a WS collector is always safe (idempotent).
"""
import mysql.connector
from mysql.connector import pooling
import logging

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MYSQL_CONFIG

logger = logging.getLogger("db")

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="crypto_bot_pool",
            pool_size=10,
            init_command="SET time_zone = '+00:00'",  # force UTC session tz -- see note in schema.sql
            **MYSQL_CONFIG,
        )
    return _pool


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
    cur = conn.cursor()
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Schema initialized.")


def upsert_ohlcv(rows):
    """
    rows: list of tuples matching the ohlcv table column order
    (symbol, market, timeframe, open_time, close_time, open, high, low,
     close, volume, quote_volume, num_trades, taker_buy_base_vol,
     taker_buy_quote_vol, is_closed)
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
            close_time=VALUES(close_time),
            open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
            volume=VALUES(volume), quote_volume=VALUES(quote_volume),
            num_trades=VALUES(num_trades),
            taker_buy_base_vol=VALUES(taker_buy_base_vol),
            taker_buy_quote_vol=VALUES(taker_buy_quote_vol),
            is_closed=VALUES(is_closed)
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def upsert_funding(rows):
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
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
    finally:
        conn.close()


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
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
    finally:
        conn.close()


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
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def heartbeat(collector_name, status="ok", detail=None):
    sql = """
        INSERT INTO collector_heartbeat (collector_name, status, detail, last_beat_time)
        VALUES (%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
            status=VALUES(status),
            detail=VALUES(detail),
            last_beat_time=NOW()
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (collector_name, status, detail))
        conn.commit()
        cur.close()
    finally:
        conn.close()


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


def fetch_ohlcv_df(symbol, market, timeframe):
    """
    Returns the full OHLCV history for one symbol/market/timeframe as a
    pandas DataFrame, sorted ascending by open_time, numeric dtypes (not
    Decimal) so indicator math works correctly.
    """
    import pandas as pd

    sql = """
        SELECT open_time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol=%s AND market=%s AND timeframe=%s
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
            SELECT o.open_time, o.open, o.high, o.low, o.close, o.volume, o.is_closed,
                   f.rsi, f.macd, f.macd_signal, f.macd_hist, f.atr, f.atr_pct,
                   f.volume_sma, f.volume_ratio, f.support, f.resistance,
                   f.swing_high, f.swing_low, f.fib_0, f.fib_236, f.fib_382,
                   f.fib_5, f.fib_618, f.fib_786, f.fib_1
            FROM (
                SELECT open_time, open, high, low, close, volume, is_closed, symbol, market, timeframe
                FROM ohlcv
                WHERE symbol=%(symbol)s AND market=%(market)s AND timeframe=%(timeframe)s {closed_clause}
                ORDER BY open_time DESC
                LIMIT %(limit)s
            ) o
            JOIN features f
              ON f.symbol = o.symbol AND f.market = o.market AND f.timeframe = o.timeframe
             AND f.open_time = o.open_time
            ORDER BY o.open_time ASC
        """
    else:
        sql = """
            SELECT o.open_time, o.open, o.high, o.low, o.close, o.volume, o.is_closed,
                   f.rsi, f.macd, f.macd_signal, f.macd_hist, f.atr, f.atr_pct,
                   f.volume_sma, f.volume_ratio, f.support, f.resistance,
                   f.swing_high, f.swing_low, f.fib_0, f.fib_236, f.fib_382,
                   f.fib_5, f.fib_618, f.fib_786, f.fib_1
            FROM ohlcv o
            JOIN features f
              ON f.symbol = o.symbol AND f.market = o.market AND f.timeframe = o.timeframe
             AND f.open_time = o.open_time
            WHERE o.symbol=%(symbol)s AND o.market=%(market)s AND o.timeframe=%(timeframe)s
            ORDER BY o.open_time ASC
        """
    engine = get_sqlalchemy_engine()
    df = pd.read_sql(sql, engine, params=params)

    if closed_only and (limit is None or int(limit) <= 0):
        df = df[df["is_closed"] == 1].reset_index(drop=True)

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
    try:
        cur = conn.cursor()
        cur.execute(sql, (
            symbol, market, timeframe, strategy_name, data_segment, json.dumps(params),
            data_start_time, data_end_time, total_trades, win_rate_pct,
            expectancy_r, profit_factor, max_drawdown_r,
        ))
        conn.commit()
        run_id = cur.lastrowid
        cur.close()
        return run_id
    finally:
        conn.close()



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
    paper.engine.get_paper_configs() -- that module still has the
    import-time DB side effect flagged in PROFESSIONAL_TRADER_REVIEW.md
    §2.6 (unresolved), so importing it from a lightweight poller like
    ai/news_strategy_engine.py would make this feature depend on that bug
    staying harmless. Revisit once §2.6 is fixed."""
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
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


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
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(event_ids))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def insert_news_ai_signal(signal):
    """
    signal: dict with keys news_event_id, symbol, bias, confidence, reasoning,
    invalidation_condition, time_horizon, trade_timing. paper_position_id is
    intentionally not accepted here -- Phase 2 is log-only; Phase 3 will add
    a separate update call once positions are actually opened from this.
    """
    sql = """
        INSERT INTO news_ai_signals (
            news_event_id, symbol, bias, confidence, reasoning,
            invalidation_condition, time_horizon, trade_timing
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, (
            signal["news_event_id"], signal["symbol"], signal["bias"],
            signal["confidence"], signal.get("reasoning"),
            signal.get("invalidation_condition"), signal.get("time_horizon"),
            signal.get("trade_timing"),
        ))
        conn.commit()
        new_id = cur.lastrowid
        cur.close()
        return new_id
    finally:
        conn.close()
