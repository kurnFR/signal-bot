import numpy as np
import pandas as pd

from ml.generator_experiment import _evaluate_partition


def test_generator_policy_evaluation_uses_canonical_non_overlapping_simulator():
    frame = pd.DataFrame({
        "open_time": list(range(9)),
        "open": [100.0] * 9,
        "high": [101.0] * 9,
        "low": [99.0] * 9,
        "close": [100.0] * 9,
        "atr": [1.0] * 9,
        "feature": [0.0] * 9,
    })
    partition = pd.DataFrame({
        "open_time": [2, 4, 6],
        "exit_time": [4, 6, 8],
    })
    probabilities = np.asarray([
        [0.05, 0.05, 0.90],
        [0.05, 0.05, 0.90],
        [0.05, 0.05, 0.90],
    ])

    metrics = _evaluate_partition(
        frame,
        partition,
        probabilities,
        0.60,
        0.10,
        evaluation_mode="canonical_simulator_oos",
        strategy_params={"BACKTEST_MAX_HOLD_BARS": 1},
        horizon_bars=2,
    )

    assert metrics["evaluation_mode"] == "canonical_simulator_oos"
    assert metrics["execution_engine"] == "backtest.simulate"
    assert metrics["non_overlapping_execution"] is True
    assert metrics["opportunities"] == 3
    assert metrics["trades"] == 2
    assert metrics["long_trades"] == 2
    assert metrics["short_trades"] == 0



def test_generator_experiment_rejects_cost_assumptions_that_differ_from_paper():
    import pytest
    from config import BACKTEST_FEE_PCT
    from ml.generator_experiment import run_generator_experiment

    frame = pd.DataFrame({"open_time": [1]})
    with pytest.raises(ValueError, match="fee assumptions must match server paper accounting"):
        run_generator_experiment(
            frame,
            model_type="hist_gradient_boosting",
            feature_columns=("feature",),
            label_config={"fee_rate": BACKTEST_FEE_PCT + 0.001},
        )


def test_generator_simulation_boundary_does_not_depend_on_label_exit_times(monkeypatch):
    from ml import generator_experiment

    frame = pd.DataFrame({
        "open_time": list(range(12)),
        "open": [100.0] * 12,
        "high": [101.0] * 12,
        "low": [99.0] * 12,
        "close": [100.0] * 12,
        "atr": [1.0] * 12,
        "feature": [0.0] * 12,
    })
    probabilities = np.asarray([
        [0.05, 0.05, 0.90],
        [0.05, 0.05, 0.90],
        [0.05, 0.05, 0.90],
    ])
    boundaries = []

    def capture_simulation_boundary(simulation_frame, params, *args):
        boundaries.append(params["_simulation_end_open_time"])
        return []

    monkeypatch.setattr(generator_experiment, "simulate", capture_simulation_boundary)

    for exit_times in ([3, 5, 7], [4, 6, 8]):
        metrics = _evaluate_partition(
            frame,
            pd.DataFrame({"open_time": [2, 4, 6], "exit_time": exit_times}),
            probabilities,
            0.60,
            0.10,
            evaluation_mode="canonical_simulator_oos",
            strategy_params={},
            horizon_bars=2,
        )
        assert metrics["simulation_end_open_time"] == 8
        assert metrics["evaluation_horizon_bars"] == 2

    assert boundaries == [8, 8]


def test_generator_policy_failure_reports_validation_search_diagnostics():
    import pytest
    from ml.generator_trainer import (
        GeneratorTrainingConfig,
        _select_policy,
    )

    validation = pd.DataFrame({"open_time": [1, 2, 3]})
    probabilities = np.asarray([
        [0.1, 0.1, 0.8],
        [0.1, 0.1, 0.8],
        [0.1, 0.1, 0.8],
    ])
    config = GeneratorTrainingConfig(
        model_type="hist_gradient_boosting",
        feature_columns=("feature",),
        min_validation_trades=10,
        confidence_candidates=(0.5, 0.7),
        margin_candidates=(0.1, 0.2),
    )

    def evaluator(_name, _frame, _probabilities, confidence, margin):
        trades = 4 if confidence == 0.5 else 2
        return {
            "trades": trades,
            "total_net_return": 0.01 if margin == 0.1 else 0.005,
            "expectancy": -0.002,
        }

    with pytest.raises(ValueError) as error:
        _select_policy(validation, probabilities, config, evaluator)

    message = str(error.value)
    assert "evaluated=4" in message
    assert "trade_floor_candidates=0" in message
    assert "required_trades=10" in message
    assert "max_observed_trades=4" in message
    assert "best_validation_candidate=" in message
    assert "Untouched test data was not used" in message
    assert "0.010000 net return / 4 trades" in message
