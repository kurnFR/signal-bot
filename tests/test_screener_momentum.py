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
