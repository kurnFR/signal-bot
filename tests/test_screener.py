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
    return module.SmartMoneySignal(
        priority_score=-quality * 1000,
        symbol=symbol,
        signal_type="RVOL_SPIKE",
        rvol=quality / 10,
        volume=1000,
        quote_volume=100000,
        price=100,
        price_change_pct=0.5,
        volume_velocity=5,
        timestamp=1.0,
        candle_time=candle,
        quality_score=quality,
    )


def test_pending_candidates_are_ranked_by_quality():
    state = module.SmartMoneyState(ema_length=10, rvol_threshold=7, max_alerts_per_hour=5)
    module.CONFIG["alert_cooldown_sec"] = 0
    module.CONFIG["max_alerts_per_hour"] = 5
    module.CONFIG["max_alerts_per_symbol_per_hour"] = 5
    assert state.register_signal(make_signal("LOWUSDT", 72))
    assert state.register_signal(make_signal("HIGHUSDT", 94))
    selected = state.pop_best_eligible()
    assert selected is not None
    assert selected.symbol == "HIGHUSDT"
    assert selected.quality_score == 94


def test_duplicate_candidate_is_not_queued_twice():
    state = module.SmartMoneyState(10, 7, 5)
    signal = make_signal("BTCUSDT", 90)
    assert state.register_signal(signal) is True
    assert state.register_signal(signal) is False
    assert len(state.pending_signals) == 1


def test_failed_delivery_releases_alert_slot():
    state = module.SmartMoneyState(10, 7, 5)
    module.CONFIG["alert_cooldown_sec"] = 900
    module.CONFIG["max_alerts_per_hour"] = 5
    module.CONFIG["max_alerts_per_symbol_per_hour"] = 2
    assert state._can_alert("BTCUSDT") is True
    assert state.hourly_alert_count["global"] == 1
    assert state.hourly_alert_count["BTCUSDT"] == 1
    state.release_alert_slot("BTCUSDT")
    assert state.hourly_alert_count["global"] == 0
    assert state.hourly_alert_count["BTCUSDT"] == 0
    assert state.last_alert_time.get("BTCUSDT") is None


def test_global_alert_limit_blocks_lower_priority_candidates():
    state = module.SmartMoneyState(10, 7, 1)
    module.CONFIG["alert_cooldown_sec"] = 0
    module.CONFIG["max_alerts_per_hour"] = 1
    module.CONFIG["max_alerts_per_symbol_per_hour"] = 5
    assert state.register_signal(make_signal("BTCUSDT", 95))
    assert state.register_signal(make_signal("ETHUSDT", 90))
    first = state.pop_best_eligible()
    second = state.pop_best_eligible()
    assert first.symbol == "BTCUSDT"
    assert second is None
