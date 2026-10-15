"""Regression tests for Smart Money EMA startup warmup."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from Screen import screening


def _kline(open_time, close_time, volume, quote_volume):
    row = [None] * 12
    row[0] = open_time
    row[5] = str(volume)
    row[6] = close_time
    row[7] = str(quote_volume)
    return row


def test_fetch_closed_kline_history_pages_back_and_excludes_open_candles():
    responses = [
        [_kline(100, 150, 1, 10), _kline(200, 250, 2, 20), _kline(900, 2000, 9, 90)],
        [_kline(50, 99, 0.5, 5)],
    ]
    request = Mock()
    request.raise_for_status.return_value = None
    request.json.side_effect = responses

    with patch.object(screening.requests, "get", return_value=request) as get, \
         patch.object(screening.time, "time", return_value=1.0), \
         patch.object(screening.time, "sleep"):
        rows = screening._fetch_closed_kline_history("TESTUSDT", 3)

    assert [row[0] for row in rows] == [50, 100, 200]
    assert len(get.call_args_list) == 2
    assert get.call_args_list[0].kwargs["params"]["interval"] == screening.CONFIG["timeframe"]


def test_seed_ema_history_restores_ready_state_from_closed_candles():
    state = screening.state
    symbol = "EMA_WARMUP_TEST"
    old_warmup = state.warmup_candles
    try:
        state.warmup_candles = 3
        with state.lock:
            state.ema_volume.pop(symbol, None)
            state.ema_quote_volume.pop(symbol, None)
            state.ema_initialized.pop(symbol, None)
            state.volume_history.pop(symbol, None)
            state.quote_volume_history.pop(symbol, None)

        rows = [
            _kline(100, 150, 10, 100),
            _kline(200, 250, 20, 200),
            _kline(300, 350, 30, 300),
        ]

        assert screening._seed_ema_history(symbol, rows) is True
        assert state.ema_initialized[symbol] == 3
        ready, _, _ = state.update_ema(symbol, 40, 400)
        assert ready is True
    finally:
        with state.lock:
            state.ema_volume.pop(symbol, None)
            state.ema_quote_volume.pop(symbol, None)
            state.ema_initialized.pop(symbol, None)
            state.volume_history.pop(symbol, None)
            state.quote_volume_history.pop(symbol, None)
        state.warmup_candles = old_warmup



def _signal():
    return SimpleNamespace(
        symbol="TESTUSDT",
        signal_type="RVOL_SPIKE",
        rvol=8.5,
        volume=100.0,
        quote_volume=10000.0,
        price=1.25,
        price_change_pct=0.2,
        volume_velocity=4.0,
        quality_score=75.0,
        direction="BULLISH",
        taker_buy_ratio=0.7,
        htf_alignment=1,
        htf_context="5m:BULLISH",
        candle_time="2026-10-12T05:00:00Z",
    )


def test_save_signal_to_db_retries_connection_pool_exhaustion_and_commits():
    conn = Mock()
    pool = Mock()
    pool.get_connection.side_effect = [RuntimeError("pool exhausted"), conn]

    with patch.object(screening, "get_pool", return_value=pool), \
         patch.object(screening, "update_screener_heartbeat"), \
         patch.object(screening.time, "sleep"):
        assert screening.save_signal_to_db(_signal(), telegram_sent=False) is True

    assert pool.get_connection.call_count == 2
    conn.cursor.return_value.execute.assert_called_once()
    insert_sql = conn.cursor.return_value.execute.call_args.args[0]
    assert "detected_at" in insert_sql
    assert "UTC_TIMESTAMP()" in insert_sql
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_save_signal_to_db_returns_false_when_all_retries_fail():
    pool = Mock()
    pool.get_connection.side_effect = RuntimeError("pool exhausted")

    with patch.object(screening, "get_pool", return_value=pool), \
         patch.object(screening.time, "sleep"):
        assert screening.save_signal_to_db(_signal(), telegram_sent=False) is False

    assert pool.get_connection.call_count == 3
