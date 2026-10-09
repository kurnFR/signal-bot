import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENING = ROOT / "Screen" / "screening.py"
spec = importlib.util.spec_from_file_location("screening_under_test", SCREENING)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def make_signal(symbol, quality, candle="2026-09-30T00:00:00Z"):
    return module.SmartMoneySignal(-quality * 1000, symbol, "RVOL_SPIKE", quality / 10, 1000, 100000, 100, 0.5, 5, 1.0, candle, quality)


def reset_limits(max_per_hour=5, max_per_symbol=5, cooldown=0):
    module.CONFIG["alert_cooldown_sec"] = cooldown
    module.CONFIG["max_alerts_per_hour"] = max_per_hour
    module.CONFIG["max_alerts_per_symbol_per_hour"] = max_per_symbol


def test_pending_candidates_are_ranked_by_quality():
    reset_limits()
    state = module.SmartMoneyState(10, 7, 5)
    assert state.register_signal(make_signal("LOWUSDT", 72))
    assert state.register_signal(make_signal("HIGHUSDT", 94))
    selected = state.pop_best_eligible()
    assert selected.symbol == "HIGHUSDT"
    assert selected.quality_score == 94


def test_duplicate_candidate_is_not_queued_twice():
    reset_limits()
    state = module.SmartMoneyState(10, 7, 5)
    signal = make_signal("BTCUSDT", 90)
    assert state.register_signal(signal)
    assert not state.register_signal(signal)
    assert len(state.pending_signals) == 1


def test_failed_delivery_releases_alert_slot():
    reset_limits(max_per_symbol=2, cooldown=900)
    state = module.SmartMoneyState(10, 7, 5)
    assert state._can_alert("BTCUSDT")
    state.release_alert_slot("BTCUSDT")
    assert state.hourly_alert_count["global"] == 0
    assert state.hourly_alert_count["BTCUSDT"] == 0
    assert state.last_alert_time.get("BTCUSDT") is None


def test_global_limit_preserves_unselected_candidate():
    reset_limits(max_per_hour=1)
    state = module.SmartMoneyState(10, 7, 1)
    assert state.register_signal(make_signal("BTCUSDT", 95))
    assert state.register_signal(make_signal("ETHUSDT", 90))
    assert state.pop_best_eligible().symbol == "BTCUSDT"
    assert state.pop_best_eligible() is None
    assert len(state.pending_signals) == 1
    assert state.pending_signals[0].symbol == "ETHUSDT"


def test_cooldown_blocked_candidate_is_not_lost():
    reset_limits(cooldown=900)
    state = module.SmartMoneyState(10, 7, 5)
    state.last_alert_time["BTCUSDT"] = module.time.time()
    assert state.register_signal(make_signal("BTCUSDT", 95))
    assert state.pop_best_eligible() is None
    assert len(state.pending_signals) == 1
    assert state.pending_signals[0].symbol == "BTCUSDT"


def test_quality_is_capped_without_htf_confirmation():
    reset_limits()
    state = module.SmartMoneyState(7200, 7, 5)
    state.ema_volume["XAUTUSDT"] = 100.0
    state.ema_quote_volume["XAUTUSDT"] = 100.0
    state.ema_initialized["XAUTUSDT"] = state.warmup_candles
    state.quote_volume_history["XAUTUSDT"].extend([100.0] * 20)
    state._fetch_htf_context = lambda *args: (0, "UNAVAILABLE")

    signal = state.detect_smart_money_signal(
        "XAUTUSDT", 10.0, 1100.0, 99.8, 100.0, 100.2, 99.2,
        "2026-10-08T12:30:00Z", taker_buy_quote=550.0,
    )

    assert signal is not None
    assert signal.quality_score <= 79
    assert signal.htf_context == "UNAVAILABLE"


def test_htf_alignment_can_unlock_high_quality_score():
    reset_limits()
    state = module.SmartMoneyState(7200, 7, 5)
    state.ema_volume["XAUTUSDT"] = 100.0
    state.ema_quote_volume["XAUTUSDT"] = 100.0
    state.ema_initialized["XAUTUSDT"] = state.warmup_candles
    state.quote_volume_history["XAUTUSDT"].extend([100.0] * 20)
    state._fetch_htf_context = lambda *args: (2, "5m:BEARISH,15m:BEARISH")

    signal = state.detect_smart_money_signal(
        "XAUTUSDT", 10.0, 1100.0, 99.0, 100.0, 100.2, 98.8,
        "2026-10-08T12:30:00Z", taker_buy_quote=220.0,
    )

    assert signal is not None
    assert signal.direction == "BEARISH"
    assert signal.htf_alignment == 2
    assert signal.htf_context == "5m:BEARISH,15m:BEARISH"
    assert signal.quality_score >= 80


def test_websocket_taker_buy_quote_is_used_for_orderflow():
    reset_limits()
    state = module.SmartMoneyState(7200, 7, 5)
    state.ema_volume["BTCUSDT"] = 100.0
    state.ema_quote_volume["BTCUSDT"] = 100.0
    state.ema_initialized["BTCUSDT"] = state.warmup_candles
    state.quote_volume_history["BTCUSDT"].extend([100.0] * 20)
    state._fetch_htf_context = lambda *args: (2, "5m:BULLISH,15m:BULLISH")

    signal = state.detect_smart_money_signal(
        "BTCUSDT", 10.0, 1000.0, 101.0, 100.0, 101.1, 99.9,
        "2026-10-08T12:30:00Z", taker_buy_quote=800.0,
    )

    assert signal is not None
    assert signal.taker_buy_ratio == 0.8
    assert signal.direction == "BULLISH"

def test_database_upsert_replaces_score_with_matching_confirmation_context(monkeypatch):
    reset_limits()
    captured = {}

    class FakeCursor:
        def execute(self, query, params=None):
            captured["query"] = query
            captured["params"] = params

        def close(self):
            pass

    class FakeConnection:
        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def commit(self):
            pass

        def close(self):
            pass

    class FakePool:
        def get_connection(self):
            return FakeConnection()

    monkeypatch.setattr(module, "get_pool", lambda: FakePool())
    monkeypatch.setattr(module, "update_screener_heartbeat", lambda **kwargs: None)

    signal = make_signal("SPKUSDT", 79)
    signal.direction = "BULLISH"
    signal.taker_buy_ratio = 0.72
    signal.htf_alignment = 0
    signal.htf_context = "UNAVAILABLE"

    module.save_signal_to_db(signal, telegram_sent=False)

    query = captured["query"]
    assert "quality_score=VALUES(quality_score)" in query
    assert "quality_score=GREATEST(" not in query
    params = captured["params"]
    assert params[8] == 79
    assert params[9] == "BULLISH"
    assert params[10] == 0.72
    assert params[12] == "UNAVAILABLE"

