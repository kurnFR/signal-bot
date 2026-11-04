import pandas as pd
import pytest

from backtest.accounting import resolve_stop_target_exit
from backtest.simulate import simulate
from ml.generator_dataset import build_generator_dataset


@pytest.mark.parametrize(
    ("direction", "bar_open", "bar_high", "bar_low", "stop", "target", "expected"),
    [
        ("LONG", 94.0, 97.0, 93.0, 95.0, 110.0, (94.0, "SL")),
        ("LONG", 112.0, 114.0, 108.0, 95.0, 110.0, (112.0, "TP")),
        ("SHORT", 106.0, 108.0, 103.0, 105.0, 90.0, (106.0, "SL")),
        ("SHORT", 88.0, 92.0, 86.0, 105.0, 90.0, (88.0, "TP")),
    ],
)
def test_gap_through_stop_or_target_fills_at_bar_open(
    direction, bar_open, bar_high, bar_low, stop, target, expected
):
    assert resolve_stop_target_exit(
        direction, bar_open, bar_high, bar_low, stop, target
    ) == expected


def test_intrabar_both_levels_touched_uses_stop_first():
    assert resolve_stop_target_exit(
        "LONG", 100.0, 111.0, 94.0, 95.0, 110.0
    ) == (95.0, "SL")


@pytest.mark.parametrize(
    ("direction", "gap_open", "expected_reason"),
    [("LONG", 94.0, "SL"), ("LONG", 112.0, "TP"),
     ("SHORT", 106.0, "SL"), ("SHORT", 88.0, "TP")],
)
def test_canonical_simulator_uses_gap_aware_fill(direction, gap_open, expected_reason):
    frame = pd.DataFrame({
        "open_time": [0, 1, 2, 3],
        "open": [100.0, 100.0, 100.0, gap_open],
        "high": [101.0, 101.0, 101.0, max(gap_open, 100.0) + 1.0],
        "low": [99.0, 99.0, 99.0, min(gap_open, 100.0) - 1.0],
        "close": [100.0, 100.0, 100.0, gap_open],
        "atr": [1.0] * 4,
        "signal": [False, True, False, False],
    })
    trades = simulate(
        frame,
        {
            "BACKTEST_FEE_PCT": 0.0,
            "BACKTEST_SLIPPAGE_PCT": 0.0,
            "BACKTEST_MAX_HOLD_BARS": 3,
        },
        lambda row, prev, params: direction == "LONG" and bool(row["signal"]),
        lambda row, prev, params: direction == "SHORT" and bool(row["signal"]),
        lambda side, entry, atr, row, params: (
            (entry - 5.0, entry + 10.0)
            if side == "LONG"
            else (entry + 5.0, entry - 10.0)
        ),
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == expected_reason
    assert trades[0]["exit_price"] == pytest.approx(gap_open)


def test_generator_label_uses_same_gap_aware_stop_fill_as_simulator():
    frame = pd.DataFrame({
        "open_time": [0, 1, 2, 3, 4],
        "open": [100.0, 100.0, 94.0, 94.0, 94.0],
        "high": [101.0, 101.0, 97.0, 97.0, 97.0],
        "low": [99.0, 99.0, 93.0, 93.0, 93.0],
        "close": [100.0, 100.0, 94.0, 94.0, 94.0],
        "atr": [1.0] * 5,
        "feature": [0.0] * 5,
    })
    labels = build_generator_dataset(
        frame,
        feature_columns=("feature",),
        horizon_bars=2,
        fee_rate=0.0,
        slippage_rate=0.0,
        stop_atr_multiple=1.0,
        risk_reward_ratio=2.0,
        max_hold_bars=1,
    )
    row = labels.loc[labels["open_time"] == 0].iloc[0]

    # The LONG stop was 95, but the next candle opened at 94. The label must
    # account for the adverse gap rather than assuming a fill at 95.
    assert row["long_net_return"] == pytest.approx(-0.06)
    assert row["label_reason"] == "take_profit" or row["target_name"] == "SHORT"
