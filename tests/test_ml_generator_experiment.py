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
