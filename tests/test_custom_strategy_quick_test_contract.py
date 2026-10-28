"""Contract tests for the custom-strategy quick-test API; no live market data is used."""
import math

import pandas as pd
import pytest
from fastapi import HTTPException

from web.routes import custom_strategy


def _request():
    strategy = custom_strategy.CustomStrategyRequest(
        name="contract_test",
        displayName="Contract Test",
        longConditions=[],
        shortConditions=[],
    )
    return custom_strategy.QuickTestRequest(
        strategy=strategy,
        symbol="BTCUSDT",
        market="spot",
        timeframe="1h",
    )


@pytest.mark.parametrize("candles", [None, pd.DataFrame({"close": range(49)})])
def test_quick_test_rejects_fewer_than_50_candles(monkeypatch, candles):
    monkeypatch.setattr(
        custom_strategy,
        "fetch_ohlcv_with_features_df",
        lambda *args, **kwargs: candles,
    )

    with pytest.raises(HTTPException) as exc:
        custom_strategy.quick_test_strategy(_request())

    assert exc.value.status_code == 400
    assert "Insufficient candles" in exc.value.detail


def test_quick_test_uses_latest_1000_candles(monkeypatch):
    frame = pd.DataFrame({"marker": range(1205)})
    observed = {}

    monkeypatch.setattr(
        custom_strategy,
        "fetch_ohlcv_with_features_df",
        lambda *args, **kwargs: frame,
    )

    def create_runner(_strategy):
        def run(test_frame, _params):
            observed["length"] = len(test_frame)
            observed["first_marker"] = int(test_frame.iloc[0]["marker"])
            observed["last_marker"] = int(test_frame.iloc[-1]["marker"])
            return []

        return run, lambda *_args, **_kwargs: []

    monkeypatch.setattr(custom_strategy, "_create_strategy_runner", create_runner)
    monkeypatch.setattr(
        custom_strategy,
        "compute_metrics",
        lambda _trades: {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "expectancy_r": 0.0,
        },
    )

    result = custom_strategy.quick_test_strategy(_request())

    assert observed == {"length": 1000, "first_marker": 205, "last_marker": 1204}
    assert result["totalCandles"] == 1000


def test_quick_test_sanitizes_non_finite_metrics(monkeypatch):
    frame = pd.DataFrame({"close": range(60)})
    monkeypatch.setattr(
        custom_strategy,
        "fetch_ohlcv_with_features_df",
        lambda *args, **kwargs: frame,
    )
    monkeypatch.setattr(
        custom_strategy,
        "_create_strategy_runner",
        lambda _strategy: (lambda *_args, **_kwargs: [], lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        custom_strategy,
        "compute_metrics",
        lambda _trades: {
            "total_trades": 0,
            "win_rate_pct": float("nan"),
            "profit_factor": float("inf"),
            "max_drawdown_r": float("-inf"),
            "expectancy_r": float("nan"),
        },
    )

    result = custom_strategy.quick_test_strategy(_request())

    assert result["winRate"] is None
    assert result["winRatePct"] is None
    assert result["profitFactor"] is None
    assert result["maxDrawdownR"] is None
    assert result["expectancyR"] is None
    assert all(
        value is None or (isinstance(value, (int, float)) and math.isfinite(value))
        for value in result.values()
        if isinstance(value, (int, float))
    )
