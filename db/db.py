"""
MySQL connection pool + upsert helpers shared by every collector.
Using INSERT ... ON DUPLICATE KEY UPDATE everywhere so re-running the
backfill or restarting a WS collector is always safe (idempotent).
"""
import mysql.connector
from mysql.connector import pooling
import logging
import json
from decimal import Decimal

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MYSQL_CONFIG, RETAILBOT2_DB_CONFIG

logger = logging.getLogger("db")

_pool = None
_retailbot2_pool = None


def get_retailbot2_pool():
    """Separate pool for paper/retailbot2.py's own database (see
    RETAILBOT2_DB_CONFIG in config.py) -- read-only usage from the web
    dashboard's visibility panel. Lazily initialized so importing db.db
    doesn't require this second database to be reachable if retailbot2
    isn't running / configured."""
    global _retailbot2_pool
    if _retailbot2_pool is None:
        _retailbot2_pool = pooling.MySQLConnectionPool(
            pool_name="retailbot2_pool",
            pool_size=3,
            init_command="SET time_zone = '+00:00'",
            **RETAILBOT2_DB_CONFIG,
        )
    return _retailbot2_pool


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
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, (strategy_name,))
        row = cur.fetchone()
        cur.close()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


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
    try:
        cur = conn.cursor()
        cur.execute(sql, (symbol, market, timeframe, strategy_name, allocated_capital, risk_per_trade_pct))
        conn.commit()
        cur.close()
    finally:
        conn.close()


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
        heartbeats = cur.fetchall()
        for h in heartbeats:
            h["last_beat_time"] = str(h["last_beat_time"])
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
# retailbot2 -- read helpers for the web dashboard visibility panel.
# Reads from RETAILBOT2_DB_CONFIG's database (default "Binance"), separate
# from the main crypto_signals pool used everywhere else in this file.
# ----------------------------------------------------------------------------

def get_retailbot2_open_trades(limit=100):
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, symbol, side, mode, entry_price, stop_price, target_price,
                   atr, rr, units, strategy, strategies_json, confluence_score,
                   reason, entry_time
            FROM rdt_trades WHERE status='OPEN'
            ORDER BY entry_time DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal):
                    r[k] = float(v)
            r["entry_time"] = str(r["entry_time"]) if r.get("entry_time") else None
        return rows
    finally:
        conn.close()


def get_retailbot2_recent_trades(limit=50):
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, symbol, side, mode, entry_price, stop_price, target_price,
                   units, strategy, confluence_score, status, exit_price,
                   exit_reason, net_pnl, pnl_percent, entry_time, exit_time
            FROM rdt_trades WHERE status='CLOSED'
            ORDER BY exit_time DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal):
                    r[k] = float(v)
            for dt_key in ("entry_time", "exit_time"):
                if r.get(dt_key) is not None:
                    r[dt_key] = str(r[dt_key])
        return rows
    finally:
        conn.close()


def get_retailbot2_stats():
    """Per-strategy/mode win-rate stats plus overall equity summary
    (initial_capital isn't stored in this DB -- the dashboard adds it from
    its own config if needed; realized_pnl sums are enough on their own to
    show relative performance)."""
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM rdt_strategy_stats ORDER BY mode, strategy")
        stats_rows = cur.fetchall()
        for r in stats_rows:
            if isinstance(r.get("total_pnl"), Decimal):
                r["total_pnl"] = float(r["total_pnl"])
            r["last_updated"] = str(r["last_updated"]) if r.get("last_updated") else None

        cur.execute("""
            SELECT mode, COUNT(*) AS open_count FROM rdt_trades
            WHERE status='OPEN' GROUP BY mode
        """)
        open_counts = {r["mode"]: r["open_count"] for r in cur.fetchall()}

        cur.execute("""
            SELECT mode, COALESCE(SUM(net_pnl), 0) AS realized_pnl,
                   COUNT(*) AS closed_count,
                   SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM rdt_trades WHERE status='CLOSED' GROUP BY mode
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
        cur.close()
        return {"by_strategy": stats_rows, "by_mode": by_mode}
    finally:
        conn.close()


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
                   price_change_pct, volume_velocity, candle_time,
                   telegram_sent, detected_at
            FROM smart_money_signals
            ORDER BY detected_at DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            for k in ("rvol", "volume", "quote_volume", "price", "price_change_pct", "volume_velocity"):
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
        cur.execute("SELECT COUNT(*) AS n FROM smart_money_signals")
        total = cur.fetchone()["n"]
        cur.execute("""
            SELECT COUNT(*) AS n FROM smart_money_signals
            WHERE detected_at >= NOW() - INTERVAL 24 HOUR
        """)
        last_24h = cur.fetchone()["n"]
        cur.execute("""
            SELECT signal_type, COUNT(*) AS n FROM smart_money_signals
            WHERE detected_at >= NOW() - INTERVAL 24 HOUR
            GROUP BY signal_type
        """)
        by_type = {r["signal_type"]: r["n"] for r in cur.fetchall()}
        cur.close()
        return {"total_signals": total, "signals_last_24h": last_24h, "by_type_last_24h": by_type}
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Bot control -- read/write for the "enable + tunable params" toggle panels.
# retailbot2's bot_control lives in ITS OWN database (RETAILBOT2_DB_CONFIG);
# the screener's screener_control lives in the shared crypto_signals DB.
# Both tables are self-created by their respective process on startup
# (paper/retailbot2.py's DatabaseManager._create_tables(),
# Screen/screening.py's ensure_signal_table()) -- these helpers assume the
# table already exists, which it will once that process has run at least
# once. If it hasn't, these raise, and the route layer turns that into a
# clear "control table not ready yet -- has retailbot2/screening been
# started at least once?" error rather than a silent empty response.
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
}


def get_retailbot2_control():
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT enabled, overrides_json, updated_at FROM bot_control WHERE id=1")
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


def set_retailbot2_control(enabled, overrides):
    """overrides is the FULL replacement dict (read-modify-write from the
    dashboard form), pre-filtered by the caller against
    RETAILBOT2_TUNABLE_FIELDS -- see web/routes/research_routes.py."""
    conn = get_retailbot2_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE bot_control SET enabled=%s, overrides_json=%s WHERE id=1",
            (enabled, json.dumps(overrides)),
        )
        conn.commit()
        cur.close()
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
            "UPDATE screener_control SET enabled=%s, overrides_json=%s WHERE id=1",
            (enabled, json.dumps(overrides)),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
