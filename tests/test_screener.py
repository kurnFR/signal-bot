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
