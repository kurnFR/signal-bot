"""Regression coverage for the accounting, ingestion and reporting boundaries."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pandas as pd
import pytest

from backtest.equity import simulate_equity_curve
from backtest.holdout import compute_holdout_cutoff, split_by_cutoff
from backtest.metrics import compute_metrics
from backtest.simulate import simulate
from collectors import funding_poller as funding
from db import db


def test_equity_ruin_records_only_available_capital():
    result = simulate_equity_curve([{"r_multiple": r} for r in (-150, -2, 20)], 100, 1)
    assert [p["equity"] for p in result["curve"]] == [100, 0, 0, 0]
    assert [p["pnl_dollars"] for p in result["curve"]] == [0, -100, 0, 0]
    assert result["max_drawdown_pct"] == 100


@pytest.mark.parametrize("capital,risk", [(0, 1), (100, -1), (float("nan"), 1)])
def test_equity_rejects_invalid_sizing(capital, risk):
    with pytest.raises(ValueError):
        simulate_equity_curve([], capital, risk)


@pytest.mark.parametrize("frame", [None, pd.DataFrame(), pd.DataFrame(columns=["open_time"])])
def test_empty_simulation(frame):
    assert simulate(frame, {"market": "futures"}, None, None, None) == []


@pytest.mark.parametrize("max_hold,opens,reason,cost", [
    (1, [100, 100, 100], "TIMEOUT", 0.30),
    (5, [100, 100, 100], "END_OF_DATA", 0.30),
    (5, [100, 100, 100, 85], "SL", 0.30),
])
def test_exit_boundary_and_executed_prices(max_hold, opens, reason, cost):
    frame = pd.DataFrame({"open_time": [i * 1000 for i in range(len(opens))],
                          "open": opens, "close": opens, "high": opens, "low": opens, "atr": 2})
    params = {"market": "futures", "BACKTEST_MAX_HOLD_BARS": max_hold,
              "BACKTEST_SLIPPAGE_PCT": 0.01, "BACKTEST_FEE_PCT": 0,
              "funding_events": [(t, 100, 0.003) for t in (2000, 2999, 3000)]}
    trade, = simulate(frame, params, lambda *args: True, lambda *args: False,
                      lambda *args: (90, 120))
    assert trade["exit_reason"] == reason
    assert trade["funding_cost"] == pytest.approx(cost)
    assert trade["entry_price"] == 101
    assert trade["exit_price"] == pytest.approx(opens[-1] * 0.99)
    assert trade["gross_pnl"] == pytest.approx(trade["exit_price"] - trade["entry_price"])


def test_metrics_handle_flat_and_tiny_outcomes():
    metrics = compute_metrics([{"r_multiple": r} for r in (-0.00001, 0, -0.00001, 0.00003)])
    assert metrics["payoff_ratio"] == 3
    assert metrics["max_consecutive_losses"] == 1
    assert metrics["recovery_factor"] == 0.5


def test_zero_holdout_keeps_last_row():
    frame = pd.DataFrame({"open_time": [1, 3, 8]})
    train, holdout = split_by_cutoff(frame, compute_holdout_cutoff(frame, 0))
    assert train.equals(frame) and holdout.empty


def test_wfa_stitches_by_entry_across_folds(monkeypatch):
    from backtest import walk_forward as wfa
    frame = pd.DataFrame({"open_time": range(12)})
    def strategy(window, params):
        return [{"entry_time": t, "exit_time": window.open_time.max(), "direction": "LONG",
                 "r_multiple": -1} for t in window.open_time]
    monkeypatch.setitem(wfa.STRATEGIES, "test", strategy)
    monkeypatch.setattr(wfa, "fetch_ohlcv_with_features_df", lambda *a, **k: frame)
    monkeypatch.setattr(wfa, "HOLDOUT_FRACTION", 0)
    result = wfa.run_rolling_walk_forward("TEST", "spot", "1h", "test", 4, 4, 2)
    assert result["total_windows"] == 3
    assert result["out_of_sample_metrics"]["total_trades"] == 8
    assert result["out_of_sample_metrics"]["max_drawdown_r"] == 8


@pytest.mark.parametrize("lock_error", [False, True])
def test_lock_failure_allocates_no_session(monkeypatch, lock_error):
    session = Mock()
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr(funding.requests, "Session", session)
    monkeypatch.setattr(funding, "heartbeat", Mock())
    if lock_error:
        lock.acquire.side_effect = funding.FundingPollerLockError("offline")
        with pytest.raises(funding.FundingPollerLockError):
            funding.poll_all_symbols(lock=lock)
    else:
        assert funding.poll_all_symbols(lock=lock)["skipped"]
    session.assert_not_called()


def test_invalid_page_preserves_watermark(monkeypatch):
    persist = Mock()
    monkeypatch.setattr(funding, "upsert_funding", persist)
    monkeypatch.setattr(funding, "get_latest_funding_time", lambda *a, **k: 100)
    items = [{"symbol": "BTCUSDT", "fundingTime": t, "fundingRate": "0.01"} for t in [110, "bad", 150]]
    monkeypatch.setattr(funding, "fetch_funding_page", lambda **kwargs: items)
    with pytest.raises(ValueError, match="fundingTime"):
        funding.poll_symbol_incremental(Mock(), "BTCUSDT", end_ms=200)
    persist.assert_not_called()


def test_ban_stops_cycle(monkeypatch):
    poll = Mock(side_effect=funding.BinanceBanCooldownError("HTTP 418"))
    monkeypatch.setattr(funding, "poll_symbol_incremental", poll)
    monkeypatch.setattr(funding, "heartbeat", Mock())
    result = funding.poll_all_symbols(["BTCUSDT", "ETHUSDT"], session=Mock(), acquire_lock=False)
    assert result["symbols_skipped"] == result["symbols_failed"] == 1
    assert poll.call_count == 1


def test_failed_funding_write_releases_resources():
    pool = Mock()
    conn = pool.get_connection.return_value
    cursor = conn.cursor.return_value
    cursor.executemany.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        db.upsert_funding([("BTCUSDT", 123, "100", None, "0.01", None)], pool=pool)
    conn.rollback.assert_called_once()
    cursor.close.assert_called_once()
    conn.close.assert_called_once()


def test_pool_created_once(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db.pooling, "MySQLConnectionPool", factory)
    with ThreadPoolExecutor(8) as workers:
        pools = list(workers.map(lambda _: db.get_pool(), range(20)))
    factory.assert_called_once()
    assert all(pool is pools[0] for pool in pools)


def test_tournament_response_with_unbounded_metrics(tmp_path, monkeypatch):
    import json
    from web.routes import ml_routes

    result = tmp_path / "experiment.json"
    result.write_text('{"experiment_id":"test","ranked":[{"pf":Infinity}]}')
    monkeypatch.setattr(ml_routes, "run_real_experiment", lambda **kw: result)
    response = ml_routes.trigger_experiment(ml_routes.RunExperimentRequest())
    assert response["result"]["ranked"][0]["pf"] is None
    json.dumps(response, allow_nan=False)
