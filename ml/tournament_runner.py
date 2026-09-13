"""Execute a deterministic ML candidate tournament without TEST leakage.

Candidate selection is performed entirely with TRAIN and VALIDATION. TEST is
not passed to candidate fitting, preprocessing, threshold selection, or
ranking. Only the locked winner is evaluated on TEST once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from ml.experiment import ExperimentConfig
from ml.result import ExperimentResult
from ml.split import split_by_fractions
from ml.tournament import Candidate, CandidateScore, rank_candidates
from ml.trainer import evaluate_locked_model, fit_experiment


@dataclass(frozen=True)
class TournamentResult:
    """Complete research result for one locked tournament winner."""

    experiment: ExperimentResult
    winner: Candidate
    ranked_validation: tuple[CandidateScore, ...]
    model: Any


def _validation_score(validation: pd.DataFrame, fitted: dict[str, Any]) -> CandidateScore:
    trading = fitted["validation_trading"]
    return CandidateScore(
        candidate=fitted["candidate"],
        validation_net_pnl_r=float(trading["total_outcome_r"]),
        validation_profit_factor=_profit_factor(validation, fitted),
        validation_max_drawdown_r=_max_drawdown(validation, fitted),
        validation_trade_count=int(trading["selected_trades"]),
    )


def _selected_outcomes(validation: pd.DataFrame, fitted: dict[str, Any]) -> pd.Series:
    model = fitted["model"]
    imputer, scaler = model._signal_bot_preprocessor
    features = fitted["feature_columns"]
    x_validation = scaler.transform(imputer.transform(validation.loc[:, features]))
    probabilities = model.predict_proba(x_validation)[:, 1]
    return validation.loc[probabilities >= fitted["threshold"].threshold, "outcome_r"].astype(float)


def _profit_factor(validation: pd.DataFrame, fitted: dict[str, Any]) -> float:
    outcomes = _selected_outcomes(validation, fitted)
    gains = float(outcomes[outcomes > 0].sum())
    losses = float(-outcomes[outcomes < 0].sum())
    return gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)


def _max_drawdown(validation: pd.DataFrame, fitted: dict[str, Any]) -> float:
    outcomes = _selected_outcomes(validation, fitted)
    if outcomes.empty:
        return 0.0
    equity = outcomes.cumsum()
    return float((equity.cummax() - equity).max())


def _test_metrics(test: pd.DataFrame, probabilities, threshold: float) -> dict[str, Any]:
    probabilities = pd.Series(probabilities, index=test.index, dtype=float)
    selected = test.loc[probabilities >= threshold]
    outcomes = selected["outcome_r"].astype(float)
    total_r = float(outcomes.sum()) if len(outcomes) else 0.0
    gains = float(outcomes[outcomes > 0].sum())
    losses = float(-outcomes[outcomes < 0].sum())
    equity = outcomes.cumsum()
    return {
        "candidate_signals": int(len(test)),
        "selected_trades": int(len(selected)),
        "selection_rate_pct": round(len(selected) / len(test) * 100.0, 4) if len(test) else None,
        "total_outcome_r": round(total_r, 8),
        "expectancy_r": round(total_r / len(selected), 8) if len(selected) else None,
        "win_rate_pct": round(float((outcomes > 0).mean() * 100.0), 4) if len(selected) else None,
        "profit_factor": round(gains / losses, 8) if losses > 0 else (float("inf") if gains > 0 else 0.0),
        "max_drawdown_r": round(float((equity.cummax() - equity).max()), 8) if len(outcomes) else 0.0,
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
    """Run TRAIN/VALIDATION tournament and test the locked winner exactly once."""
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

    split = split_by_fractions(
        dataset,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
    )
    validation_scores: list[CandidateScore] = []
    fitted: list[dict[str, Any]] = []

    # TEST is intentionally absent from this loop.
    for index, candidate in enumerate(candidate_list):
        config = ExperimentConfig(
            experiment_id=f"{experiment_id}-c{index:03d}",
            symbol=symbol,
            timeframe=timeframe,
            base_strategy=base_strategy,
            model_type=candidate.model_type,
            feature_columns=candidate.feature_columns,
            model_params=dict(candidate.model_params),
            probability_threshold=0.60,
            train_fraction=train_fraction,
            validation_fraction=validation_fraction,
            test_fraction=1.0 - train_fraction - validation_fraction,
            seed=seed,
        )
        locked_candidate = fit_experiment(
            split.train,
            split.validation,
            config,
            min_validation_trades=min_validation_trades,
            threshold_candidates=candidate.threshold_candidates,
        )
        locked_candidate = {**locked_candidate, "candidate": candidate}
        fitted.append(locked_candidate)
        validation_scores.append(_validation_score(split.validation, locked_candidate))

    ranked = rank_candidates(validation_scores)
    winner = ranked[0].candidate
    winner_index = candidate_list.index(winner)
    winner_fit = fitted[winner_index]

    # LOCK POINT. TEST is first accessed here, by exactly one winner.
    test_classification, test_trading = evaluate_locked_model(split.test, winner_fit)
    test_metrics = {**dict(test_classification), "trading": dict(test_trading)}
    validation_metrics = {
        **dict(winner_fit["validation_metrics"]),
        "trading": dict(winner_fit["validation_trading"]),
        "candidate_count": len(candidate_list),
    }
    baseline_metrics = {
        "total_trades": int(len(split.test)),
        "total_outcome_r": round(float(split.test["outcome_r"].sum()), 8),
    }
    result = ExperimentResult(
        experiment_id=experiment_id,
        symbol=symbol,
        timeframe=timeframe,
        base_strategy=base_strategy,
        model_type=winner.model_type,
        baseline_metrics=baseline_metrics,
        ml_validation_metrics=validation_metrics,
        ml_test_metrics=test_metrics,
        threshold=float(winner_fit["threshold"].threshold),
        status="research",
    )
    return TournamentResult(result, winner, tuple(ranked), winner_fit["model"])
