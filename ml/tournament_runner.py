"""Leakage-safe execution engine for ML candidate tournaments.

The engine deliberately separates candidate selection from OOS evaluation:
1. split the prepared dataset chronologically;
2. train every candidate on TRAIN only;
3. select each candidate's threshold on VALIDATION only;
4. rank candidates using VALIDATION-only trading outcomes;
5. lock exactly one winner;
6. evaluate the locked winner on TEST exactly once.

No candidate receives TEST metrics before the winner is locked, and no paper/live
promotion is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from ml.experiment import ExperimentConfig
from ml.metrics import classification_metrics
from ml.models import create_model
from ml.result import ExperimentResult
from ml.split import split_by_fractions
from ml.tournament import Candidate, CandidateScore, rank_candidates
from ml.trainer import select_validation_threshold


@dataclass(frozen=True)
class TournamentResult:
    """Complete research result for one locked tournament winner."""

    experiment: ExperimentResult
    winner: Candidate
    ranked_validation: tuple[CandidateScore, ...]
    model: Any


def _prepare_features(train: pd.DataFrame, validation: pd.DataFrame, test: pd.DataFrame,
                      feature_columns: tuple[str, ...]):
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for ML tournament execution") from exc

    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train.loc[:, feature_columns])
    x_validation = imputer.transform(validation.loc[:, feature_columns])
    x_test = imputer.transform(test.loc[:, feature_columns])

    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_validation = scaler.transform(x_validation)
    x_test = scaler.transform(x_test)
    return x_train, x_validation, x_test, imputer, scaler


def _validate_candidate_frames(train: pd.DataFrame, validation: pd.DataFrame,
                               test: pd.DataFrame, features: tuple[str, ...]) -> None:
    required = set(features) | {"target", "outcome_r", "open_time"}
    for frame, name in ((train, "train"), (validation, "validation"), (test, "test")):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")
        if frame.empty:
            raise ValueError(f"{name} must not be empty")
        if frame["target"].nunique() < 2:
            raise ValueError(f"{name}.target must contain both classes")


def _selected_stats(frame: pd.DataFrame, probabilities, threshold: float) -> tuple[float, float, int]:
    probabilities = pd.Series(probabilities, index=frame.index, dtype=float)
    selected = frame.loc[probabilities >= threshold]
    outcomes = selected["outcome_r"].astype(float)
    if outcomes.empty:
        return 0.0, 0.0, 0
    gains = float(outcomes[outcomes > 0].sum())
    losses = float(-outcomes[outcomes < 0].sum())
    profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
    cumulative = outcomes.cumsum()
    drawdown = float((cumulative.cummax() - cumulative).max()) if len(cumulative) else 0.0
    return float(profit_factor), drawdown, len(selected)


def _metrics(frame: pd.DataFrame, probabilities, threshold: float) -> dict[str, Any]:
    probabilities = pd.Series(probabilities, index=frame.index, dtype=float)
    selected = frame.loc[probabilities >= threshold]
    outcomes = selected["outcome_r"].astype(float)
    total_r = float(outcomes.sum()) if len(outcomes) else 0.0
    wins = int((outcomes > 0).sum())
    losses = int((outcomes < 0).sum())
    gross_profit = float(outcomes[outcomes > 0].sum())
    gross_loss = float(-outcomes[outcomes < 0].sum())
    return {
        "candidate_signals": int(len(frame)),
        "selected_trades": int(len(selected)),
        "selection_rate_pct": round(len(selected) / len(frame) * 100.0, 4) if len(frame) else None,
        "total_outcome_r": round(total_r, 8),
        "expectancy_r": round(total_r / len(selected), 8) if len(selected) else None,
        "win_rate_pct": round(wins / len(selected) * 100.0, 4) if len(selected) else None,
        "profit_factor": round(gross_profit / gross_loss, 8) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "max_drawdown_r": round(float((outcomes.cumsum().cummax() - outcomes.cumsum()).max()), 8) if len(outcomes) else 0.0,
        "gross_profit_r": round(gross_profit, 8),
        "gross_loss_r": round(gross_loss, 8),
        "wins": wins,
        "losses": losses,
        "threshold": threshold,
    }


def run_tournament(
    dataset: pd.DataFrame,
    candidates: Iterable[Candidate],
    *,
    experiment_id: str,
    symbol: str,
    timeframe: str,
    base_strategy: str,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    min_validation_trades: int = 10,
    max_candidates: int = 50,
    seed: int = 42,
) -> TournamentResult:
    """Run a candidate tournament and test only the locked winner."""
    candidate_list = list(candidates)
    if not candidate_list:
        raise ValueError("candidate grid is empty")
    if max_candidates < 1:
        raise ValueError("max_candidates must be >= 1")
    if len(candidate_list) > max_candidates:
        raise ValueError(f"candidate grid exceeds max_candidates={max_candidates}")
    if dataset.empty:
        raise ValueError("dataset must not be empty")
    if "open_time" not in dataset.columns:
        raise ValueError("dataset must contain open_time")

    split = split_by_fractions(dataset, train_fraction=train_fraction,
                                validation_fraction=validation_fraction)
    validation_scores: list[CandidateScore] = []
    fitted: dict[Candidate, tuple[Any, Any, Any]] = {}

    # Candidate loop has no TEST access. Each model and preprocessor is fitted
    # exclusively on TRAIN, and threshold selection uses VALIDATION outcomes.
    for candidate in candidate_list:
        features = tuple(candidate.feature_columns)
        _validate_candidate_frames(split.train, split.validation, split.test, features)
        x_train, x_validation, _x_test, imputer, scaler = _prepare_features(
            split.train, split.validation, split.test, features
        )
        model = create_model(candidate.model_type, {**candidate.model_params, "random_state": seed})
        model.fit(x_train, split.train["target"].astype(int))
        validation_prob = model.predict_proba(x_validation)[:, 1]
        threshold = select_validation_threshold(
            split.validation,
            validation_prob,
            min_trades=min_validation_trades,
            candidates=candidate.threshold_candidates,
        )
        profit_factor, drawdown, trade_count = _selected_stats(
            split.validation, validation_prob, threshold.threshold
        )
        validation_scores.append(CandidateScore(
            candidate=candidate,
            validation_net_pnl_r=threshold.total_outcome_r,
            validation_profit_factor=profit_factor,
            validation_max_drawdown_r=drawdown,
            validation_trade_count=trade_count,
        ))
        model._signal_bot_preprocessor = (imputer, scaler)
        fitted[candidate] = (model, validation_prob, threshold)

    ranked = rank_candidates(validation_scores)
    winner = ranked[0].candidate
    winner_model, _winner_validation_prob, winner_threshold = fitted[winner]

    # LOCK POINT: from here onward exactly one candidate is evaluated on TEST.
    features = tuple(winner.feature_columns)
    x_test = winner_model._signal_bot_preprocessor[0].transform(split.test.loc[:, features])
    x_test = winner_model._signal_bot_preprocessor[1].transform(x_test)
    test_prob = winner_model.predict_proba(x_test)[:, 1]

    config = ExperimentConfig(
        experiment_id=experiment_id,
        symbol=symbol,
        timeframe=timeframe,
        base_strategy=base_strategy,
        model_type=winner.model_type,
        feature_columns=features,
        strategy_params={},
        model_params=dict(winner.model_params),
        probability_threshold=winner_threshold.threshold,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
        test_fraction=1.0 - train_fraction - validation_fraction,
        seed=seed,
    )
    baseline_metrics = {
        "total_trades": int(len(split.test)),
        "total_outcome_r": round(float(split.test["outcome_r"].sum()), 8),
    }
    test_metrics = _metrics(split.test, test_prob, winner_threshold.threshold)
    result = ExperimentResult(
        experiment_id=config.experiment_id,
        symbol=config.symbol,
        timeframe=config.timeframe,
        base_strategy=config.base_strategy,
        model_type=config.model_type,
        baseline_metrics=baseline_metrics,
        ml_validation_metrics=_metrics(split.validation, fitted[winner][1], winner_threshold.threshold),
        ml_test_metrics=test_metrics,
        threshold=winner_threshold.threshold,
        status="research",
    )
    return TournamentResult(result, winner, tuple(ranked), winner_model)
