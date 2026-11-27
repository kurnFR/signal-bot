"""Seed a freshly migrated, disposable loopback database for real browser tests."""

import os

from db.db import _owned_cursor, get_pool
from web.auth import hash_password


def seed() -> None:
    if os.environ.get("MYSQL_HOST") != "127.0.0.1" or os.environ.get("MYSQL_DATABASE") != "signal_bot_test":
        raise SystemExit("Browser fixtures require an isolated loopback signal_bot_test database.")
    if not os.environ.get("SIGNAL_BOT_TEST_RUN_ID"):
        raise SystemExit("Set a dedicated browser-test run ID.")
    password = os.environ["SIGNAL_BOT_REAL_E2E_PASSWORD"]
    username = os.getenv("SIGNAL_BOT_REAL_E2E_USER", "browser_e2e")

    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute("SELECT DATABASE()")
        if cur.fetchone() != ("signal_bot_test",):
            raise SystemExit("Unexpected fixture database identity.")
        cur.execute(
            """INSERT INTO users (username, email, password_hash, role, is_active, must_change_password, terms_accepted_at)
               VALUES (%s, 'browser-e2e@example.test', %s, 'admin', 1, 0, UTC_TIMESTAMP())""",
            (username, hash_password(password)),
        )
        cur.executemany(
            """INSERT INTO ohlcv (symbol,market,timeframe,open_time,close_time,open,high,low,close,
                                  volume,quote_volume,num_trades,taker_buy_base_vol,taker_buy_quote_vol)
               VALUES (%s,%s,'1h',%s,%s,100,101,99,100,10,1000,5,1,100)""",
            [
                ("BTCUSDT", "spot", 1795003200000, 1795006799999),
                ("BTCUSDT", "spot", 1795006800000, 1795010399999),
                ("ETHUSDT", "futures", 1795003200000, 1795006799999),
            ],
        )
        cur.execute(
            """INSERT INTO collector_heartbeat (collector_name,status,detail,last_beat_time)
               VALUES ('oi_poller','ok','browser fixture',UTC_TIMESTAMP() - INTERVAL 1 DAY)"""
        )
        cur.execute(
            """INSERT INTO backtest_runs
               (id, symbol, market, timeframe, strategy_name, data_segment,
                params_json, data_start_time, data_end_time)
               VALUES (42,'E2EPAPERUSDT','spot','1h','trend_ema_v1','holdout','{}',1795003200000,1795006800000)"""
        )
        cur.execute(
            """INSERT INTO paper_configs
               (symbol, market, timeframe, strategy_name, is_active, allocated_capital, risk_per_trade_pct, archived_at)
               VALUES ('HISTORYUSDT','spot','1h','trend_ema_v1',0,5000,1,UTC_TIMESTAMP())"""
        )
        cur.execute(
            """INSERT INTO paper_positions (
                    config_id,symbol,market,timeframe,strategy_name,direction,entry_time,
                    entry_price,current_price,stop_loss,take_profit,current_stop,atr_at_signal,
                    initial_risk,last_update_time,status
               ) VALUES (%s,'HISTORYUSDT','spot','1h','trend_ema_v1','LONG',1795003200000,
                         100,105,95,105,95,1,5,1795006800000,'CLOSED')""",
            (cur.lastrowid,),
        )
        conn.commit()


if __name__ == "__main__":
    seed()
