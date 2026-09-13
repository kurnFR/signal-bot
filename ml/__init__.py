"""Machine-learning research and strategy components."""

from ml.experiment import ExperimentConfig
from ml.trainer import (
    TrainingResult,
    ThresholdResult,
    train_experiment,
    select_validation_threshold,
    fit_candidate_model,
    fit_experiment,
    evaluate_locked_model,
)
from ml.runner import MLRunResult, run_experiment
from ml.models import create_model, supported_models
from ml.metrics import classification_metrics
from ml.tournament_runner import TournamentResult, run_tournament
from ml.execution_tournament import ExecutionTournamentResult, run_execution_aware_tournament

__all__ = [
    "ExperimentConfig",
    "TrainingResult",
    "ThresholdResult",
    "train_experiment",
    "select_validation_threshold",
    "fit_candidate_model",
    "fit_experiment",
    "evaluate_locked_model",
    "run_experiment",
    "MLRunResult",
    "create_model",
    "supported_models",
    "classification_metrics",
    "TournamentResult",
    "run_tournament",
    "ExecutionTournamentResult",
    "run_execution_aware_tournament",
]
