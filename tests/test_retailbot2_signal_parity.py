from pathlib import Path

from unittest.mock import patch

import pandas as pd
import pytest

from backtest.params import BASE_PARAMS, retailbot2_param_overrides
from backtest.strategies import retailbot2_strategies as library
from backtest.strategies.retailbot2_strategies import RETAILBOT2_STRATEGIES
from paper.retailbot2 import BotConfig, RetailDeathTrapBot, RetailStrategyEngine
from tools.retailbot2_parity import ORACLE_METHODS, candles, compare, compare_frames


def test_signal_and_risk_parity():
    report = compare([1, 2], count=240)
    for name, (bars, live, library, direction_mismatches, risk_mismatches) in report.items():
        assert bars == 476
        assert live == library > 0, name
        assert (direction_mismatches, risk_mismatches) == (0, 0), name


@pytest.mark.parametrize("name", ORACLE_METHODS)
def test_strategy_fires_on_the_last_closed_bar(name):
    oracle = RetailStrategyEngine(BotConfig())
    series = candles(1, 320)
    prepared = oracle.calculate_indicators(series)
    get_signal = getattr(oracle, ORACLE_METHODS[name])
    for end in range(50, len(series)):
        signal = get_signal(prepared.iloc[:end], "TEST")
        if signal:
            break
    assert signal is not None
    prefix = series.iloc[:end]
    signal_time, entry_time = prefix.open_time.iloc[-2:]
    params = dict(BASE_PARAMS, _simulation_start_open_time=int(signal_time))
    for runner_name, expected_side in (
        (name, signal["side"]),
        (name + "_INV", "LONG" if signal["side"] == "SHORT" else "SHORT"),
    ):
        [trade] = RETAILBOT2_STRATEGIES[runner_name](prefix, params)
        assert trade["signal_open_time"] == signal_time
        assert trade["entry_time"] == entry_time
        assert trade["direction"] == expected_side


def test_explicit_deployment_overrides_win_over_retail_defaults():
    assert BASE_PARAMS["RB2_RSI_OVERSOLD"] == 35
    assert BASE_PARAMS["RB2_RSI_OVERBOUGHT"] == 65
    assert retailbot2_param_overrides({"RSI_OVERSOLD": 32}) == {"RB2_RSI_OVERSOLD": 32}
    assert retailbot2_param_overrides({"RSI_OVERSOLD": 32, "RB2_RSI_OVERSOLD": 38}) == {
        "RB2_RSI_OVERSOLD": 38
    }


def test_real_btcusdt_1h_signal_and_risk_parity():
    csv_path = Path(__file__).resolve().parent / "fixtures" / "btcusdt_spot_1h_3000.csv"
    frame = pd.read_csv(csv_path)
    assert len(frame) >= 2000
    report = compare_frames([frame])
    for name, (bars, live, library, direction_mismatches, risk_mismatches) in report.items():
        assert bars == len(frame) - 2
        assert live == library > 0, name
        assert (direction_mismatches, risk_mismatches) == (0, 0), name


def test_composite_parity_with_live_build_composite():
    import math

    oracle = RetailStrategyEngine(BotConfig())
    series = candles(1, 240)
    oracle_frame = oracle.calculate_indicators(series)
    params = dict(BASE_PARAMS)
    for runner_name, invert in (("COMPOSITE", False), ("COMPOSITE_INV", True)):
        live_count = lib_count = 0

        def capture(prepared, effective_params, buy, sell, stop_target):
            nonlocal live_count, lib_count
            for i in range(1, len(prepared) - 1):
                live_signals = oracle.run_all(oracle_frame.iloc[: i + 2], "TEST")
                live = oracle.build_composite(live_signals)
                if live and invert:
                    live = RetailDeathTrapBot._invert(live)
                row, previous = prepared.iloc[i], prepared.iloc[i - 1]
                side = "LONG" if buy(row, previous, effective_params) else (
                    "SHORT" if sell(row, previous, effective_params) else None
                )
                live_count += bool(live)
                lib_count += bool(side)
                assert side == (live and live["side"]), (runner_name, i)
                if live:
                    stop, target = stop_target(side, row["close"], row["atr"], row, effective_params)
                    assert math.isclose(stop, live["stop"])
                    assert math.isclose(target, live["target"])
            return []

        with patch.object(library, "simulate", capture):
            RETAILBOT2_STRATEGIES[runner_name](series, params)
        assert live_count == lib_count > 0, runner_name
