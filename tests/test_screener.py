import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENING = ROOT / "Screen" / "screening.py"
spec = importlib.util.spec_from_file_location("screening_under_test", SCREENING)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_smart_money_delivery_defaults_keep_detection_separate_from_alert_quality():
    assert module.CONFIG["min_quality_score"] == 70
    assert module.CONFIG["min_alert_quality_score"] == 80
    assert module.CONFIG["max_signal_age_sec"] == 900
    assert module.CONFIG["expired_signal_retention_days"] == 30
    assert module.CONFIG["expired_signal_cleanup_interval_days"] == 7

def test_delivery_and_cleanup_settings_are_dashboard_tunable():
    assert {
        "min_alert_quality_score",
        "max_signal_age_sec",
        "expired_signal_retention_days",
        "expired_signal_cleanup_interval_days",
    }.issubset(module._TUNABLE_CONFIG_KEYS)


def test_expired_cleanup_loop_runs_immediately_and_can_stop(monkeypatch):
    calls = []
    module.state.shutdown_flag = False
    monkeypatch.setattr(module, "cleanup_expired_signal_history", lambda: calls.append(True))

    def stop_after_first_sleep(_seconds):
        module.state.shutdown_flag = True

    monkeypatch.setattr(module.time, "sleep", stop_after_first_sleep)
    module.expired_signal_cleanup_loop()

    assert calls == [True]
    module.state.shutdown_flag = False

def make_signal(symbol, quality, candle="2026-09-30T00:00:00Z", timestamp=None):
    if timestamp is None:
        timestamp = module.time.time()
    return module.SmartMoneySignal(-quality * 1000, symbol, "RVOL_SPIKE", quality / 10, 1000, 100000, 100, 0.5, 5, timestamp, candle, quality)


def reset_limits(max_per_hour=5, max_per_symbol=5, cooldown=0):
    module.CONFIG["alert_cooldown_sec"] = cooldown
    module.CONFIG["max_alerts_per_hour"] = max_per_hour
    module.CONFIG["max_alerts_per_symbol_per_hour"] = max_per_symbol
    module.CONFIG["max_pending_signals"] = 200
    module.CONFIG["max_signal_age_sec"] = 180


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




def test_pending_queue_is_bounded_and_keeps_higher_ranked_signals():
    reset_limits()
    module.CONFIG["max_pending_signals"] = 2
    state = module.SmartMoneyState(10, 7, 5)

    assert state.register_signal(make_signal("LOWUSDT", 70))
    assert state.register_signal(make_signal("MIDUSDT", 80))
    assert state.register_signal(make_signal("HIGHUSDT", 95))

    assert len(state.pending_signals) == 2
    assert {item.symbol for item in state.pending_signals} == {"MIDUSDT", "HIGHUSDT"}
    assert ("LOWUSDT", "RVOL_SPIKE", "2026-09-30T00:00:00Z") not in state.pending_keys
    assert len(state.pending_keys) == 2


def test_full_queue_rejects_lower_ranked_candidate_without_growing():
    reset_limits()
    module.CONFIG["max_pending_signals"] = 1
    state = module.SmartMoneyState(10, 7, 5)

    assert state.register_signal(make_signal("HIGHUSDT", 95))
    assert not state.register_signal(make_signal("LOWUSDT", 70))

    assert len(state.pending_signals) == 1
    assert state.pending_signals[0].symbol == "HIGHUSDT"


def test_stale_candidate_is_removed_without_delivery():
    reset_limits()
    module.CONFIG["max_signal_age_sec"] = 60
    state = module.SmartMoneyState(10, 7, 5)
    assert state.register_signal(make_signal("OLDUSDT", 95, timestamp=module.time.time() - 120))

    assert state.pop_best_eligible() is None
    assert state.pending_signals == []
    assert state.pending_keys == set()



def test_persistent_rate_limit_blocks_when_hourly_cap_reached(monkeypatch):
    reset_limits(max_per_hour=5)
    class FakeCursor:
        def execute(self, query, params=None):
            self.query = query
        def fetchone(self):
            return (5, 0, None)
        def close(self):
            pass
    class FakeConnection:
        def cursor(self):
            return FakeCursor()
        def close(self):
            pass
    class FakePool:
        def get_connection(self):
            return FakeConnection()
    monkeypatch.setattr(module, "get_pool", lambda: FakePool())

    assert not module.persistent_alert_limit_allows("BTCUSDT")


def test_persistent_rate_limit_fails_closed_when_database_is_unavailable(monkeypatch):
    reset_limits()
    def unavailable_pool():
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(module, "get_pool", unavailable_pool)

    assert not module.persistent_alert_limit_allows("BTCUSDT")


def test_delivery_failure_is_scheduled_for_bounded_retry(monkeypatch):
    reset_limits()
    captured = {"updates": []}
    class FakeCursor:
        def execute(self, query, params=None):
            captured["updates"].append((query, params))
        def fetchone(self):
            return (2,)
        def close(self):
            pass
    class FakeConnection:
        def cursor(self):
            return FakeCursor()
        def commit(self):
            pass
        def close(self):
            pass
    class FakePool:
        def get_connection(self):
            return FakeConnection()
    monkeypatch.setattr(module, "get_pool", lambda: FakePool())
    module.CONFIG["max_delivery_attempts"] = 5

    module.complete_signal_delivery(make_signal("BTCUSDT", 90), False, "temporary Telegram error")

    update_query, update_params = captured["updates"][-1]
    assert "delivery_status=%s" in update_query
    assert update_params[0] == "PENDING"
    assert update_params[2] == 60
    assert update_params[3] == "temporary Telegram error"

def test_dispatcher_survives_telegram_exception_and_schedules_retry(monkeypatch):
    reset_limits()
    candidate = make_signal("BTCUSDT", 90)
    completed = []
    released = []

    monkeypatch.setattr(module.state, "shutdown_flag", False)
    monkeypatch.setattr(module, "persistent_alert_limit_allows", lambda symbol: True)
    monkeypatch.setattr(module.state, "_can_alert", lambda symbol: True)
    monkeypatch.setattr(module.state, "release_alert_slot", lambda symbol: released.append(symbol))
    monkeypatch.setattr(module, "complete_signal_delivery", lambda signal, sent, error=None: completed.append((signal, sent, error)))

    def claim_once():
        # Return one candidate, then stop the loop after it has been processed.
        module.state.shutdown_flag = True
        return candidate

    def raise_telegram_error(signal):
        raise RuntimeError("simulated Telegram timeout")

    monkeypatch.setattr(module, "claim_next_pending_signal", claim_once)
    monkeypatch.setattr(module.telegram, "send_early_alert", raise_telegram_error)

    module.dispatch_alerts()

    assert released == ["BTCUSDT"]
    assert len(completed) == 1
    assert completed[0][0] is candidate
    assert completed[0][1] is False
    assert "RuntimeError" in completed[0][2]
    assert "simulated Telegram timeout" in completed[0][2]




def test_neutral_direction_never_counts_neutral_htfs_as_aligned(monkeypatch):
    reset_limits()
    state = module.SmartMoneyState(7200, 7, 5)
    module.CONFIG["htf_timeframes"] = ("5m", "15m")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [[0, "100", "100", "100", "100"] for _ in range(30)]

    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: Response())
    alignment, context = state._fetch_htf_context("TESTUSDT", 1791462600000, "NEUTRAL")

    assert alignment == 0
    assert context == "5m:NEUTRAL,15m:NEUTRAL"
    assert module.SmartMoneyState._score_htf(alignment, context, "NEUTRAL") == 0


def test_neutral_direction_gets_no_orderflow_score():
    assert module.SmartMoneyState._score_orderflow("NEUTRAL", 0.99) == 0
    assert module.SmartMoneyState._score_orderflow("NEUTRAL", 0.01) == 0


def test_extreme_velocity_does_not_dominate_candidate_priority():
    reset_limits()
    state = module.SmartMoneyState(7200, 7, 5)
    state.ema_volume["TESTUSDT"] = 100.0
    state.ema_quote_volume["TESTUSDT"] = 100.0
    state.ema_initialized["TESTUSDT"] = state.warmup_candles
    state.quote_volume_history["TESTUSDT"].extend([1.0] * 20)
    state._fetch_htf_context = lambda *args: (1, "5m:BULLISH,15m:NEUTRAL")

    signal = state.detect_smart_money_signal(
        "TESTUSDT", 10.0, 1000.0, 101.0, 100.0, 101.2, 99.8,
        "2026-10-08T12:30:00Z", taker_buy_quote=800.0,
    )

    assert signal is not None
    assert signal.volume_velocity > 100
    assert abs(signal.priority_score) < 101000


def test_missing_websocket_taker_buy_quote_remains_unavailable(monkeypatch):
    captured = {}

    def detect(*args, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(module.state, "detect_smart_money_signal", detect)
    event = {
        "e": "kline",
        "s": "BTCUSDT",
        "k": {
            "x": True, "v": "10", "q": "1000", "o": "100",
            "c": "101", "h": "102", "l": "99", "t": 1791462600000,
        },
    }

    module.process_kline_message(__import__("json").dumps(event))

    assert captured["taker_buy_quote"] is None


def test_expired_signal_cleanup_only_deletes_old_expired_rows(monkeypatch):
    class FakeCursor:
        rowcount = 7

        def __init__(self):
            self.query = None
            self.params = None

        def execute(self, query, params=None):
            self.query = " ".join(query.split())
            self.params = params

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self):
            return self.fake_cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    connection = FakeConnection()

    class FakePool:
        def get_connection(self):
            return connection

    module.CONFIG["expired_signal_retention_days"] = 30
    monkeypatch.setattr(module, "get_pool", lambda: FakePool())

    assert module.cleanup_expired_signal_history() == 7
    assert "DELETE FROM smart_money_signals" in connection.fake_cursor.query
    assert "delivery_status='EXPIRED'" in connection.fake_cursor.query
    assert "delivery_updated_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)" in connection.fake_cursor.query
    assert connection.fake_cursor.params == (30,)
    assert connection.committed
    assert not connection.rolled_back


def test_expired_signal_cleanup_rolls_back_on_database_error(monkeypatch):
    class FakeCursor:
        def execute(self, query, params=None):
            raise RuntimeError("database unavailable")

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    connection = FakeConnection()

    class FakePool:
        def get_connection(self):
            return connection

    monkeypatch.setattr(module, "get_pool", lambda: FakePool())

    assert module.cleanup_expired_signal_history() == 0
    assert not connection.committed
    assert connection.rolled_back

def test_durable_queue_claims_highest_quality_fresh_candidate(monkeypatch):
    from datetime import datetime

    candidate = {
        "id": 41, "symbol": "STRONGUSDT", "signal_type": "RVOL_SPIKE",
        "rvol": 8.5, "volume": 1000, "quote_volume": 100000,
        "price": 100, "price_change_pct": 0.2, "volume_velocity": 6.0,
        "quality_score": 91, "direction": "BULLISH", "taker_buy_ratio": 0.7,
        "htf_alignment": 2, "htf_context": "5m:BULLISH,15m:BULLISH",
        "candle_time": "2026-10-12T06:30:00Z", "detected_at": datetime.utcnow(),
    }

    class FakeCursor:
        def __init__(self):
            self.queries = []
            self.last_query = ""

        def execute(self, query, params=None):
            normalized = " ".join(query.split())
            self.queries.append((normalized, params))
            self.last_query = normalized

        def fetchone(self):
            if "SELECT COUNT(*)" in self.last_query:
                return (0,)
            if "ORDER BY quality_score DESC, rvol DESC, detected_at ASC, id ASC" in self.last_query:
                return candidate
            return None

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.commits = 0
            self.rolled_back = False

        def cursor(self, dictionary=False):
            assert dictionary is True
            return self.fake_cursor

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    connection = FakeConnection()

    class FakePool:
        def get_connection(self):
            return connection

    reset_limits()
    module.CONFIG["min_alert_quality_score"] = 80
    module.CONFIG["max_signal_age_sec"] = 900
    monkeypatch.setattr(module, "get_pool", lambda: FakePool())

    selected = module.claim_next_pending_signal()

    assert selected is not None
    assert selected.symbol == "STRONGUSDT"
    assert selected.quality_score == 91
    select_queries = [q for q, _ in connection.fake_cursor.queries if q.startswith("SELECT id,symbol")]
    assert len(select_queries) == 1
    assert "quality_score >= %s" in select_queries[0]
    assert "detected_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s SECOND)" in select_queries[0]
    assert connection.commits >= 2
    assert not connection.rolled_back


def test_durable_queue_does_not_claim_when_hourly_cap_is_full(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.queries = []
            self.last_query = ""

        def execute(self, query, params=None):
            self.last_query = " ".join(query.split())
            self.queries.append(self.last_query)

        def fetchone(self):
            if "SELECT COUNT(*)" in self.last_query:
                return (5,)
            return None

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = FakeCursor()
            self.committed = False

        def cursor(self, dictionary=False):
            return self.fake_cursor

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

        def close(self):
            pass

    connection = FakeConnection()

    class FakePool:
        def get_connection(self):
            return connection

    reset_limits(max_per_hour=5)
    monkeypatch.setattr(module, "get_pool", lambda: FakePool())

    assert module.claim_next_pending_signal() is None
    assert not any(q.startswith("SELECT id,symbol") for q in connection.fake_cursor.queries)
    assert connection.committed

