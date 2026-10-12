"""Regression tests for Smart Money EMA startup warmup."""

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
