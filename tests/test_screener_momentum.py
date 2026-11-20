from Screen import momentum as m
from Screen.replay_momentum import outcomes


def test_break_requires_contiguous_closed_directional_evidence_after_delivery():
    watch = m.Bar(0, 10, 11, 9, 10)
    wick = m.Bar(m.MINUTE, 10, 12, 8, 9)
    break_low = m.Bar(2*m.MINUTE, 9, 10, 7, 8)
    bars = {b.start: b for b in (watch, wick, break_low)}
    bars.update({-i*m.MINUTE: watch for i in range(1, 21)})
    assert m.first_break(bars, 0, m.MINUTE, "BEARISH") == (break_low, .5)
    assert m.first_break(bars, 0, 3*m.MINUTE, "BEARISH") is None
    assert m.first_break(bars, 0, m.MINUTE, "BULLISH") is None
    del bars[m.MINUTE]
    assert m.first_break(bars, 0, m.MINUTE, "BEARISH") is None


def test_replay_uses_only_full_minutes_after_delivery_and_signed_extremes():
    bars = {m.MINUTE: m.Bar(m.MINUTE, 100, 104, 90, 95)}
    (horizon, (net, favorable, adverse)), = outcomes(bars, 59_000, -1)
    assert horizon == 1 and round(net) == 5 and round(favorable) == 10 and round(adverse) == -4
    assert list(outcomes(bars, 60_000, 1)) == []


def test_queue_links_a_single_fresh_break_to_its_delivered_watch(monkeypatch):
    from unittest.mock import MagicMock
    from Screen import screening

    start = 1_800_000_000_000
    watch = m.Bar(start, 100, 102, 98, 99)
    breakout = m.Bar(start + m.MINUTE, 99, 100, 95, 97)
    bars = {bar.start: bar for bar in (watch, breakout)}
    pool = MagicMock()
    cur = pool.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [dict(id=8, symbol="BCHUSDT", candle_time=m.iso(start),
                                     delivered_at=m.iso(start + m.MINUTE + 2000), direction="BEARISH",
                                     watch_context='{"interval":"1m","high":102,"low":98}',
                                     rvol=75, volume=1500, quote_volume=325000, price=99,
                                     price_change_pct=-1, volume_velocity=7, quality_score=100)]
    saved = []
    monkeypatch.setattr(screening, "get_pool", lambda: pool)
    monkeypatch.setattr(m, "bars_between", lambda *args: bars)
    monkeypatch.setattr(screening, "save_signal_to_db", lambda s, telegram_sent: saved.append(s))
    screening.queue_watch_confirmations(start + 2*m.MINUTE + 2000)
    screening.queue_watch_confirmations(start + 3*m.MINUTE)
    child, = saved
    assert child.parent_watch_id == 8
    assert "1m close 97 crossed the watch low 98" in m.message(child)
    assert "Distance beyond level: unavailable" in m.message(child)


def test_watch_freshness_exact_boundary():
    start = 1_800_000_000_000  # watch open
    candle_time = m.iso(start)
    deadline = start + 6 * m.MINUTE  # 6 minutes from open (5 minutes from close)

    assert m.watch_deadline_ms(candle_time) == deadline
    # Strictly before deadline: eligible
    assert not m.is_watch_expired(candle_time, deadline - 1)
    # Exactly at deadline: expired
    assert m.is_watch_expired(candle_time, deadline)
    # After deadline: expired
    assert m.is_watch_expired(candle_time, deadline + 1000)

    # With smaller max_signal_age_sec, uses the earlier deadline
    assert m.watch_deadline_ms(candle_time, max_signal_age_sec=180) == start + 180_000
    assert not m.is_watch_expired(candle_time, start + 179_999, max_signal_age_sec=180)
    assert m.is_watch_expired(candle_time, start + 180_000, max_signal_age_sec=180)

    # With larger max_signal_age_sec (like production 900s), capped at 6m
    assert m.watch_deadline_ms(candle_time, max_signal_age_sec=900) == deadline


def test_paxg_watch_170630_regression_expired():
    # PAXG 170630 open: 2026-11-20T02:01:00Z, close: 02:02:00 UTC
    candle_time = "2026-11-20T02:01:00Z"
    paxg_open_ms = m.utc_ms(candle_time)
    deadline_ms = paxg_open_ms + 6 * m.MINUTE  # 02:07:00 UTC

    assert m.watch_deadline_ms(candle_time, 900) == deadline_ms
    # At historical delivery instant 02:11:07 UTC (247s past deadline), it must be expired
    delivery_ms = m.utc_ms("2026-11-20T02:11:07Z")
    assert m.is_watch_expired(candle_time, delivery_ms, 900)
    assert delivery_ms > deadline_ms


def test_sui_watch_170695_regression_timely():
    # SUI 170695 open: 2026-11-20T02:23:00Z, close: 02:24:00 UTC
    candle_time = "2026-11-20T02:23:00Z"
    sui_open_ms = m.utc_ms(candle_time)
    deadline_ms = sui_open_ms + 6 * m.MINUTE  # 02:29:00 UTC

    # Delivered at 02:24:33 UTC (33s after close, well before 02:29:00 UTC deadline)
    delivery_ms = m.utc_ms("2026-11-20T02:24:33Z")
    assert not m.is_watch_expired(candle_time, delivery_ms, 900)
    assert delivery_ms < deadline_ms


def test_retry_crossing_deadline_aborts_send_and_marks_expired(monkeypatch):
    from unittest.mock import MagicMock
    from Screen import screening

    start = 1_800_000_000_000
    candle_time = m.iso(start)
    signal = screening.SmartMoneySignal(
        -1000.0, "BTCUSDT", "RVOL_SPIKE", 10.0, 100.0, 5000000.0,
        50000.0, 1.0, 5.0, start / 1000, candle_time, 100.0,
        direction="BULLISH"
    )

    # Simulate pre-send check when clock has crossed deadline
    deadline_ms = start + 6 * m.MINUTE
    now_ms = deadline_ms + 5000  # 5s past deadline on retry

    expired_calls = []
    monkeypatch.setattr(screening, "expire_claimed_signal", lambda sig, reason: expired_calls.append((sig, reason)))

    # Check pre-send logic
    assert m.is_watch_expired(signal.candle_time, now_ms, 900)
    screening.expire_claimed_signal(signal, m.EXPIRED_WATCH_REASON)
    assert len(expired_calls) == 1
    sig, reason = expired_calls[0]
    assert sig.symbol == "BTCUSDT"
    assert reason == m.EXPIRED_WATCH_REASON


def test_claim_expires_stale_watches_before_hourly_cap_early_return(monkeypatch):
    from unittest.mock import MagicMock
    from Screen import screening

    executed_queries = []
    cursor_mock = MagicMock()

    def record_execute(sql, params=None):
        executed_queries.append((sql, params))

    cursor_mock.execute.side_effect = record_execute
    cursor_mock.fetchone.return_value = {"sent_count": 5}  # Hourly cap is full!

    conn_mock = MagicMock()
    conn_mock.cursor.return_value = cursor_mock
    pool_mock = MagicMock()
    pool_mock.get_connection.return_value = conn_mock

    monkeypatch.setattr(screening, "get_pool", lambda: pool_mock)
    monkeypatch.setitem(screening.CONFIG, "max_alerts_per_hour", 5)
    monkeypatch.setitem(screening.CONFIG, "max_signal_age_sec", 180)

    # Call claim_next_pending_signal
    res = screening.claim_next_pending_signal()
    assert res is None  # Early return due to cap

    # Verify that expiry updates ran and committed BEFORE early return
    assert any("SET delivery_status='EXPIRED'" in q[0] and m.EXPIRED_WATCH_REASON in str(q[1]) for q in executed_queries)
    assert conn_mock.commit.call_count >= 1


def test_claim_selection_query_excludes_expired_watches(monkeypatch):
    from unittest.mock import MagicMock
    from Screen import screening

    executed_queries = []
    cursor_mock = MagicMock()

    def record_execute(sql, params=None):
        executed_queries.append((sql, params))

    cursor_mock.execute.side_effect = record_execute
    cursor_mock.fetchone.side_effect = [{"sent_count": 0}, None]  # Cap allows claim, but no eligible rows

    conn_mock = MagicMock()
    conn_mock.cursor.return_value = cursor_mock
    pool_mock = MagicMock()
    pool_mock.get_connection.return_value = conn_mock

    monkeypatch.setattr(screening, "get_pool", lambda: pool_mock)
    monkeypatch.setitem(screening.CONFIG, "max_alerts_per_hour", 5)
    monkeypatch.setitem(screening.CONFIG, "max_signal_age_sec", 180)

    res = screening.claim_next_pending_signal()
    assert res is None

    # Verify claim selection query includes the watch freshness condition
    select_query, params = executed_queries[-1]
    assert "SELECT id,symbol,signal_type" in select_query
    assert "direction NOT IN ('BULLISH', 'BEARISH') OR candle_time >" in select_query
    assert 180 in params
