import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from db.db import get_pool, upsert_ohlcv
from web.app import app
from web.auth import create_access_token, require_admin
from web.routes import master_data

SYM, MKT, TF = "ZZTESTUSDT", "spot", "1h"


def _db_ok():
    try:
        get_pool().get_connection().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="MariaDB not available")


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


@pytest.fixture
def seeded():
    _exec("DELETE FROM features WHERE symbol=%s", (SYM,))
    _exec("DELETE FROM ohlcv WHERE symbol=%s", (SYM,))
    _exec("DELETE FROM paper_configs WHERE symbol=%s", (SYM,))
    for t in (1, 2, 3):
        upsert_ohlcv([(SYM, MKT, TF, t * 1000, t * 1000 + 999, 1, 2, 1, 2, 10, 20, 5, 1, 2, 1)])
        _exec("INSERT INTO features (symbol,market,timeframe,open_time) VALUES (%s,%s,%s,%s)", (SYM, MKT, TF, t * 1000))
    yield
    _exec("DELETE FROM features WHERE symbol=%s", (SYM,))
    _exec("DELETE FROM ohlcv WHERE symbol=%s", (SYM,))
    _exec("DELETE FROM paper_configs WHERE symbol=%s", (SYM,))


@pytest.fixture
def admin_client():
    token = create_access_token(user_id=1, username="u", role="admin")
    h = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "username": "u", "role": "admin"}
    with patch("web.app.authenticate_request", return_value={"id": 1, "username": "u", "role": "admin", "is_active": 1}):
        yield TestClient(app), h
    app.dependency_overrides.pop(require_admin, None)


def _count(table):
    return _exec(f"SELECT COUNT(*) FROM {table} WHERE symbol=%s", (SYM,))[0][0]


def test_features_scope_keeps_candles(seeded, admin_client):
    c, h = admin_client
    r = c.delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}&scope=features", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["features_deleted"] == 3 and r.json()["candles_deleted"] == 0
    assert _count("features") == 0 and _count("ohlcv") == 3


def test_all_scope_removes_candles_and_is_audited(seeded, admin_client):
    c, h = admin_client
    with patch("web.routes.master_data.log_audit") as audit:
        r = c.delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}&scope=all", headers=h)
    assert r.status_code == 200 and r.json()["candles_deleted"] == 3
    assert _count("ohlcv") == 0 and _count("features") == 0
    assert audit.call_args.args[0] == "MASTER_DATA_DELETED"


def test_other_timeframes_untouched(seeded, admin_client):
    upsert_ohlcv([(SYM, MKT, "4h", 1, 2, 1, 2, 1, 2, 1, 2, 1, 1, 1, 1)])
    c, h = admin_client
    c.delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}&scope=all", headers=h)
    assert _count("ohlcv") == 1


def test_refused_while_job_running(seeded, admin_client):
    c, h = admin_client
    with patch.dict(master_data.JOBS, {"j1": {"symbol": SYM, "market": MKT, "timeframe": TF, "status": "running"}}, clear=False):
        r = c.delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}&scope=all", headers=h)
    assert r.status_code == 409 and _count("ohlcv") == 3


def test_refused_when_active_deployment_uses_dataset(seeded, admin_client):
    from paper.engine import set_paper_config
    cid = set_paper_config(SYM, MKT, TF, "trend_ema_v1", 1000.0, 1.0, True)
    c, h = admin_client
    r = c.delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}&scope=all", headers=h)
    assert r.status_code == 409 and "active paper deployment" in r.json()["detail"]
    assert _count("ohlcv") == 3
    _exec("DELETE FROM paper_configs WHERE id=%s", (cid if isinstance(cid, int) else 0,))


@pytest.mark.parametrize("q", ["symbol=bad!&market=spot&timeframe=1h", "symbol=BTCUSDT&market=xx&timeframe=1h",
                               "symbol=BTCUSDT&market=spot&timeframe=7h"])
def test_rejects_invalid_input(q, admin_client):
    c, h = admin_client
    assert c.delete(f"/api/master-data/dataset?{q}", headers=h).status_code == 400


def test_non_admin_cannot_delete():
    token = create_access_token(user_id=2, username="t", role="trader")
    with patch("web.app.authenticate_request", return_value={"id": 2, "username": "t", "role": "trader", "is_active": 1}):
        r = TestClient(app).delete(f"/api/master-data/dataset?symbol={SYM}&market={MKT}&timeframe={TF}",
                                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403)
