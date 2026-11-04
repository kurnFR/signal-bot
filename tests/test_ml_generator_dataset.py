import numpy as np
import pandas as pd
import pytest

from features.indicators import compute_all_features
from ml.generator_dataset import (
    GENERATOR_CLASSES,
    build_generator_dataset,
)


def _frame():
    return pd.DataFrame({
        "open_time": [100, 200, 300, 400, 500, 600],
        "open": [100.0] * 6,
        "high": [101.0] * 6,
        "low": [99.0] * 6,
        "close": [100.0] * 6,
        "atr": [1.0] * 6,
        "feature": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    })


def _build(frame, **kwargs):
    params = {
        "feature_columns": ("feature",),
        "horizon_bars": 2,
        "barrier_atr_multiple": 1.5,
        "fee_rate": 0.0,
        "slippage_rate": 0.0,
    }
    params.update(kwargs)
    return build_generator_dataset(frame, **params)


def test_generator_labels_long_without_any_base_strategy_trades():
    frame = _frame()
    frame.loc[1, "high"] = 104.0

    result = _build(frame)

    row = result.loc[result["open_time"] == 100].iloc[0]
    assert row["target"] == GENERATOR_CLASSES["LONG"]
    assert row["target_name"] == "LONG"
    assert row["entry_time"] == 200
    assert row["exit_time"] == 200
    assert row["feature"] == 10.0
    assert row["long_net_return"] > 0
    assert row["short_net_return"] < 0


def test_generator_labels_short_when_lower_barrier_is_first():
    frame = _frame()
    frame.loc[1, "low"] = 96.0

    result = _build(frame)
    row = result.loc[result["open_time"] == 100].iloc[0]

    assert row["target"] == GENERATOR_CLASSES["SHORT"]
    assert row["target_name"] == "SHORT"
    assert row["label_reason"] == "take_profit"


def test_same_bar_touching_both_barriers_is_conservatively_hold():
    frame = _frame()
    frame.loc[1, "high"] = 102.0
    frame.loc[1, "low"] = 98.0

    result = _build(frame)
    row = result.loc[result["open_time"] == 100].iloc[0]

    assert row["target"] == GENERATOR_CLASSES["HOLD"]
    assert row["label_reason"] == "directional_returns_below_hurdle"
    assert row["long_net_return"] < 0
    assert row["short_net_return"] < 0


def test_no_barrier_touch_before_horizon_is_hold_and_exit_time_is_recorded():
    result = _build(_frame())
    row = result.loc[result["open_time"] == 100].iloc[0]

    assert row["target"] == GENERATOR_CLASSES["HOLD"]
    assert row["label_reason"] == "max_hold_timeout"
    assert row["exit_time"] == 300
    assert row["label_end_time"] == 300


def test_directional_label_is_downgraded_when_round_trip_cost_exceeds_edge():
    frame = _frame()
    frame.loc[1, "high"] = 102.0

    result = _build(frame, fee_rate=0.02, slippage_rate=0.02)
    row = result.loc[result["open_time"] == 100].iloc[0]

    assert row["target"] == GENERATOR_CLASSES["HOLD"]
    assert row["label_reason"] == "directional_returns_below_hurdle"
    assert row["gross_return"] > 0
    assert row["net_return"] == 0


def test_features_are_read_from_signal_candle_not_future_candles():
    original = _frame()
    changed_future = original.copy()
    changed_future.loc[1:, "feature"] = -999.0
    changed_future.loc[1, "high"] = 104.0

    first = _build(original).loc[lambda x: x["open_time"] == 100].iloc[0]
    second = _build(changed_future).loc[lambda x: x["open_time"] == 100].iloc[0]

    assert first["feature"] == second["feature"] == 10.0
    assert first["target"] != second["target"]


def test_unclosed_signal_candles_are_not_labeled():
    frame = _frame()
    frame["is_closed"] = 1
    frame.loc[0, "is_closed"] = 0

    result = _build(frame)

    assert 100 not in set(result["open_time"])


def test_invalid_time_order_and_ohlc_are_rejected():
    frame = _frame()
    frame.loc[2, "open_time"] = 100
    with pytest.raises(ValueError, match="strictly increasing"):
        _build(frame)

    frame = _frame()
    frame.loc[0, "high"] = 90.0
    with pytest.raises(ValueError, match="high must be"):
        _build(frame)


def test_horizon_and_cost_parameters_are_validated():
    with pytest.raises(ValueError, match="positive integer"):
        _build(_frame(), horizon_bars=0)
    with pytest.raises(ValueError, match="non-negative"):
        _build(_frame(), fee_rate=-0.1)


def test_empty_input_returns_stable_generator_schema():
    result = _build(_frame().iloc[0:0])

    assert result.empty
    assert {"open_time", "exit_time", "target", "target_name", "feature"} <= set(result.columns)


def test_builder_keeps_last_signal_with_a_complete_forward_horizon():
    result = _build(_frame())

    assert list(result["open_time"]) == [100, 200, 300, 400]
    assert result.iloc[-1]["exit_time"] == 600


def test_open_future_candles_are_not_used_to_resolve_labels():
    frame = _frame()
    frame["is_closed"] = 1
    frame.loc[2, "is_closed"] = 0

    result = _build(frame)

    assert 100 not in set(result["open_time"])
    assert 200 not in set(result["open_time"])


def test_shared_ml_features_do_not_change_when_future_ohlcv_is_mutated():
    count = 96
    steps = np.arange(count, dtype=float)
    mid = 100.0 + steps * 0.15 + np.sin(steps / 3.0)
    open_price = mid
    close_price = mid + 0.2 * np.sin(steps / 2.0)
    frame = pd.DataFrame({
        "open": open_price,
        "high": np.maximum(open_price, close_price) + 0.8,
        "low": np.minimum(open_price, close_price) - 0.8,
        "close": close_price,
        "volume": 1000.0 + steps * 3.0 + 100.0 * np.cos(steps / 4.0),
    })
    params = {
        "RSI_WINDOW": 14,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "ATR_PERIOD": 14,
        "VOLUME_SMA_PERIOD": 20,
        "HTF_S_R_LOOKBACK_BARS": 24,
    }
    cutoff = 60
    changed_future = frame.copy()
    changed_future.loc[cutoff + 1:, ["open", "high", "low", "close"]] *= 1.4
    changed_future.loc[cutoff + 1:, "volume"] *= 5.0

    original_features = compute_all_features(frame, params)
    changed_features = compute_all_features(changed_future, params)

    pd.testing.assert_frame_equal(
        original_features.iloc[:cutoff + 1].reset_index(drop=True),
        changed_features.iloc[:cutoff + 1].reset_index(drop=True),
        check_exact=True,
    )



def test_generator_labels_use_configured_asymmetric_stop_target_and_full_hold_window():
    frame = pd.DataFrame({
        "open_time": list(range(7)),
        "open": [100.0] * 7,
        "high": [100.5, 100.5, 100.5, 100.5, 102.5, 100.5, 100.5],
        "low": [99.5, 99.5, 99.5, 99.5, 99.5, 99.5, 99.5],
        "close": [100.0] * 7,
        "atr": [1.0] * 7,
        "feature": [1.0] * 7,
    })

    result = _build(
        frame,
        horizon_bars=2,
        max_hold_bars=3,
        stop_atr_multiple=1.0,
        risk_reward_ratio=2.0,
    )
    row = result.loc[result["open_time"] == 0].iloc[0]

    assert row["target_name"] == "LONG"
    assert row["exit_time"] == 4
    assert row["long_net_return"] == pytest.approx(0.02)
    assert row["short_net_return"] == pytest.approx(-0.01)
    assert row["label_reason"] == "take_profit"


def test_generator_dataset_requires_complete_max_hold_future_window():
    frame = _frame()
    result = _build(frame, horizon_bars=2, max_hold_bars=5)

    assert result.empty
