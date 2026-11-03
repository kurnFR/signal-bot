import numpy as np
import pandas as pd
import pytest

from ml.generator_dataset import GENERATOR_CLASSES
from ml.generator_trainer import GeneratorTrainingConfig, train_generator


def _partitions():
    rng = np.random.default_rng(17)
    targets = np.tile([0, 1, 2], 80)
    features = np.select(
        [targets == 0, targets == 1, targets == 2],
        [-2.0, 0.0, 2.0],
    ) + rng.normal(0.0, 0.08, len(targets))
    frame = pd.DataFrame({
        "open_time": np.arange(len(targets), dtype=np.int64) * 10,
        "exit_time": np.arange(len(targets), dtype=np.int64) * 10 + 1,
        "target": targets,
        "feature": features,
        "long_net_return": np.where(targets == GENERATOR_CLASSES["LONG"], 0.02, -0.02),
        "short_net_return": np.where(targets == GENERATOR_CLASSES["SHORT"], 0.02, -0.02),
    })
    return frame.iloc[:120].copy(), frame.iloc[120:180].copy(), frame.iloc[180:].copy()


def _config(**kwargs):
    values = {
        "model_type": "logistic_regression",
        "feature_columns": ("feature",),
        "seed": 11,
        "min_validation_trades": 5,
        "confidence_candidates": (0.4, 0.5, 0.6),
        "margin_candidates": (0.05, 0.1),
    }
    values.update(kwargs)
    return GeneratorTrainingConfig(**values)


def test_generator_trains_three_classes_and_locks_policy_on_validation():
    train, validation, test = _partitions()

    result = train_generator(train, validation, test, _config())

    assert set(result.class_mapping.values()) == {0, 1, 2}
    assert result.policy.validation_trades >= 5
    assert result.policy.validation_total_net_return > 0
    assert result.test_metrics["trades"] > 0
    assert result.test_metrics["total_net_return"] > 0
    assert result.model._signal_bot_generator_feature_columns == ("feature",)


def test_test_outcomes_do_not_change_validation_selected_policy():
    train, validation, test = _partitions()
    first = train_generator(train, validation, test, _config())

    changed_test = test.copy()
    changed_test["long_net_return"] = -0.5
    changed_test["short_net_return"] = -0.5
    second = train_generator(train, validation, changed_test, _config())

    assert second.policy == first.policy
    assert second.test_metrics["total_net_return"] < first.test_metrics["total_net_return"]


def test_training_requires_all_three_classes_in_training_partition():
    train, validation, test = _partitions()
    train = train[train["target"] != GENERATOR_CLASSES["HOLD"]]

    with pytest.raises(ValueError, match="all three generator classes"):
        train_generator(train, validation, test, _config())


def test_training_rejects_unpurged_label_horizons():
    train, validation, test = _partitions()
    train.loc[train.index[-1], "exit_time"] = int(validation["open_time"].min())

    with pytest.raises(ValueError, match="purge overlapping outcomes"):
        train_generator(train, validation, test, _config())


def test_training_rejects_non_finite_features():
    train, validation, test = _partitions()
    train.loc[train.index[0], "feature"] = np.inf

    with pytest.raises(ValueError, match="infinite values"):
        train_generator(train, validation, test, _config())


def test_training_rejects_missing_directional_return_columns():
    train, validation, test = _partitions()
    train = train.drop(columns=["short_net_return"])

    with pytest.raises(ValueError, match="short_net_return"):
        train_generator(train, validation, test, _config())


def test_training_rejects_target_columns_in_feature_schema():
    train, validation, test = _partitions()

    with pytest.raises(ValueError, match="target/future metadata"):
        train_generator(
            train, validation, test,
            _config(feature_columns=("feature", "long_net_return")),
        )
