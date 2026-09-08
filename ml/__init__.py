"""Machine-learning research and strategy components.

The package intentionally starts with dataset/split contracts before model
training so time-series leakage rules are encoded in reusable code.
"""

from ml.experiment import ExperimentConfig
from ml.trainer import TrainingResult, ThresholdResult, train_experiment, select_validation_threshold

__all__ = [
    "ExperimentConfig",
    "TrainingResult",
    "ThresholdResult",
    "train_experiment",
    "select_validation_threshold",
]
