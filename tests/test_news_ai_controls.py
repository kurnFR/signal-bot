import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from db import news_ai_control as nac
from db.db import get_pool
from web.app import app
from web.auth import create_access_token, require_admin


def _db_ok():
    try:
        get_pool().get_connection().close()
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_ok(), reason="MariaDB not available")


def _exec(sql, params=()):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.with_rows else None
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean():
    nac._invalidate()
    yield
    nac._invalidate()


@pytest.mark.parametrize("mc,mt", [(49, 3), (101, 3), (75, -1), (75, 21), (True, 3), ("75", 3)])
def test_validation_rejects_out_of_range(mc, mt):
    with pytest.raises(ValueError):
        nac.validate_settings(mc, mt)


def test_validation_accepts_boundaries_including_zero_cap():
    nac.validate_settings(50, 0)
    nac.validate_settings(100, 20)


def test_fail_open_to_config_defaults_on_db_error():
    with patch.object(nac, "get_settings_detail", side_effect=RuntimeError("db down")):
        got = nac.get_effective_settings(use_cache=False)
    assert got == nac._defaults()


@db_required
def test_override_roundtrip_and_reset():
    _exec("DELETE FROM news_ai_settings")
    base = nac.get_settings_detail()
    assert base["source"] == "default"
    nac.set_settings(88, 1, "tester")
    d = nac.get_settings_detail()
    assert (d["min_confidence"], d["max_trades_per_day"], d["source"], d["updated_by"]) == (88, 1, "override", "tester")
    assert nac.get_effective_settings(use_cache=False) == {**nac._defaults(), "min_confidence": 88, "max_trades_per_day": 1}
    assert nac.reset_settings() is True
    assert nac.get_settings_detail()["source"] == "default"


@pytest.mark.parametrize("changes", [
    {"stop_atr_mult": 0.49}, {"tp_r_scalp": 10.01}, {"tp_r_intraday": float("nan")},
    {"tp_r_swing": True}, {"poll_interval_seconds": 29}, {"execution_poll_interval_seconds": 3601},
    {"poll_interval_seconds": 31.5},
])
def test_runtime_override_validation(changes):
    with pytest.raises(ValueError):
        nac.validate_settings(**changes)


@db_required
def test_partial_update_and_per_field_reset():
    nac.reset_settings()
    try:
        nac.set_settings(stop_atr_mult=2.25, tp_r_swing=4, updated_by="tester")
        nac.set_settings(poll_interval_seconds=45, updated_by="tester")
        detail = nac.get_settings_detail()
        assert detail["overrides"] == {"stop_atr_mult": 2.25, "tp_r_swing": 4.0, "poll_interval_seconds": 45}
        assert detail["min_confidence"] == detail["defaults"]["min_confidence"]
        nac.set_settings(stop_atr_mult=None, updated_by="tester")
        assert nac.get_effective_settings()["stop_atr_mult"] == nac._defaults()["stop_atr_mult"]
        assert nac.get_effective_settings()["tp_r_swing"] == 4
    finally:
        nac.reset_settings()


def test_trade_uses_effective_stop_and_target(monkeypatch):
    import pandas as pd
    from ai import news_execution

    frame = pd.DataFrame([{"atr": 2, "close": 100, "open_time": 1000}] * 5)
    monkeypatch.setattr(news_execution, "fetch_ohlcv_with_features_df", lambda *args, **kwargs: frame)
    monkeypatch.setattr(news_execution, "get_effective_settings", lambda: {"stop_atr_mult": 2.5, "tp_r_scalp": 4})
    trade = news_execution._compute_trade("BTCUSDT", "spot", "1h", "long", "scalp")
    assert trade["stop_loss"] == 95
    assert trade["take_profit"] == 120


@db_required
def test_runtime_controls_migration_can_reapply():
    from db.migrate import HERE, MIGRATIONS, run_statements, split_statements

    migration = next(m for m in MIGRATIONS if m.version == "039_news_ai_runtime_controls")
    sql = (HERE / migration.filename).read_text()
    conn = get_pool().get_connection()
    try:
        with conn.cursor() as cur:
            for _ in range(2):
                assert run_statements(cur, split_statements(sql), label=migration.version, log=lambda _: None) == 0
    finally:
        conn.close()


@db_required
def test_db_check_constraint_blocks_out_of_range_even_if_validation_bypassed():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        with pytest.raises(Exception):
            cur.execute("INSERT INTO news_ai_settings (id,min_confidence,max_trades_per_day) VALUES (1,10,3)")
    finally:
        conn.rollback()
        conn.close()


def _seed_signal(acted=False, processing_minutes_ago=None):
    _exec("DELETE FROM news_ai_signals WHERE symbol='ZZNEWSUSDT'")
    _exec("DELETE FROM news_events WHERE external_id='zz-news-test'")
    _exec("INSERT INTO news_events (source, category, external_id, headline, published_at) "
          "VALUES ('test','crypto_news','zz-news-test','h', UTC_TIMESTAMP())")
    eid = _exec("SELECT id FROM news_events WHERE external_id='zz-news-test'")[0][0]
    proc = "NULL" if processing_minutes_ago is None else f"UTC_TIMESTAMP() - INTERVAL {int(processing_minutes_ago)} MINUTE"
    _exec(f"INSERT INTO news_ai_signals (news_event_id,symbol,market,timeframe,bias,confidence,acted_on,processing_at) "
          f"VALUES (%s,'ZZNEWSUSDT','spot','1h','long',80,%s,{proc})", (eid, 1 if acted else 0))
    return _exec("SELECT id FROM news_ai_signals WHERE symbol='ZZNEWSUSDT'")[0][0]


@db_required
def test_dismiss_outcomes_and_execution_never_picks_dismissed():
    sid = _seed_signal()
    assert nac.dismiss_news_signal(sid) == "dismissed"
    assert nac.dismiss_news_signal(sid) == "already_final"
    assert nac.dismiss_news_signal(10**9) == "not_found"
    row = _exec("SELECT acted_on, skip_reason, paper_position_id FROM news_ai_signals WHERE id=%s", (sid,))[0]
    assert row[0] == 1 and row[1] == "dismissed_by_operator" and row[2] is None
    from db.db import get_unacted_news_signals, claim_news_signal
    assert sid not in [s["id"] for s in get_unacted_news_signals(limit=500)]
    assert claim_news_signal(sid) is False
    _exec("DELETE FROM news_ai_signals WHERE symbol='ZZNEWSUSDT'")
    _exec("DELETE FROM news_events WHERE external_id='zz-news-test'")


@db_required
def test_cannot_dismiss_signal_the_executor_is_processing():
    sid = _seed_signal(processing_minutes_ago=1)
    assert nac.dismiss_news_signal(sid) == "in_progress"
    assert _exec("SELECT acted_on FROM news_ai_signals WHERE id=%s", (sid,))[0][0] == 0
    stale = _seed_signal(processing_minutes_ago=30)   # crashed worker's stale claim
    assert nac.dismiss_news_signal(stale) == "dismissed"
    _exec("DELETE FROM news_ai_signals WHERE symbol='ZZNEWSUSDT'")
    _exec("DELETE FROM news_events WHERE external_id='zz-news-test'")


def _client(role):
    token = create_access_token(user_id=1, username="u", role=role)
    h = {"Authorization": f"Bearer {token}"}
    p = patch("web.app.authenticate_request", return_value={"id": 1, "username": "u", "role": role, "is_active": 1})
    p.start()
    return TestClient(app), h, p


def test_routes_roles_and_outcomes():
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "username": "u", "role": "admin"}
    try:
        c, h, p = _client("admin")
        with patch("web.routes.news_routes.get_settings_detail", return_value={"min_confidence": 75}), \
             patch("web.routes.news_routes.set_settings") as setter, patch("web.routes.news_routes.log_audit") as audit:
            assert c.get("/api/news/settings", headers=h).status_code == 200
            assert c.put("/api/news/settings", json={"min_confidence": 80, "max_trades_per_day": 2}, headers=h).status_code == 200
            setter.assert_called_once_with(min_confidence=80, max_trades_per_day=2, updated_by="u")
            assert audit.call_args.args[0] == "NEWS_AI_SETTINGS_UPDATED"
        with patch("web.routes.news_routes.set_settings", side_effect=ValueError("min_confidence must be")):
            assert c.put("/api/news/settings", json={"min_confidence": 10, "max_trades_per_day": 2}, headers=h).status_code == 400
        for outcome, code in (("dismissed", 200), ("not_found", 404), ("already_final", 409), ("in_progress", 409)):
            with patch("web.routes.news_routes.dismiss_news_signal", return_value=outcome), patch("web.routes.news_routes.log_audit"):
                assert c.post("/api/news/signals/5/dismiss", headers=h).status_code == code
        p.stop()
    finally:
        app.dependency_overrides.pop(require_admin, None)
    # trader: may dismiss, may not touch settings
    c, h, p = _client("trader")
    with patch("web.routes.news_routes.dismiss_news_signal", return_value="dismissed"), patch("web.routes.news_routes.log_audit"):
        assert c.post("/api/news/signals/5/dismiss", headers=h).status_code == 200
    assert c.put("/api/news/settings", json={"min_confidence": 80, "max_trades_per_day": 2}, headers=h).status_code == 403
    assert c.get("/api/news/settings", headers=h).status_code == 403
    p.stop()
    # viewer: neither
    c, h, p = _client("viewer")
    assert c.post("/api/news/signals/5/dismiss", headers=h).status_code == 403
    p.stop()
