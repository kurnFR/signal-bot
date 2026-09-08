"""Leakage-safe candidate tournament for ML strategy experiments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Candidate:
    model_type: str
    model_params: dict[str, Any]
    feature_columns: tuple[str, ...]
    threshold_candidates: tuple[float, ...]


@dataclass(frozen=True)
class CandidateScore:
    candidate: Candidate
    validation_net_pnl_r: float
    validation_profit_factor: float
    validation_max_drawdown_r: float
    validation_trade_count: int

    @property
    def rank_key(self) -> tuple[float, float, float, int]:
        # Profitability first, then risk, then trade-count as a deterministic tie-break.
        return (
            self.validation_net_pnl_r,
            self.validation_profit_factor,
            -self.validation_max_drawdown_r,
            self.validation_trade_count,
        )


def rank_candidates(scores: Iterable[CandidateScore]) -> list[CandidateScore]:
    """Rank candidates using validation-only metrics.

    TEST/OOS metrics are intentionally absent from CandidateScore, making it
    impossible for the tournament ranking function to optimize on TEST data.
    """
    return sorted(scores, key=lambda score: score.rank_key, reverse=True)


def build_candidates(
    model_grid: Iterable[tuple[str, dict[str, Any]]],
    feature_sets: Iterable[Iterable[str]],
    thresholds: Iterable[float],
) -> list[Candidate]:
    threshold_values = tuple(float(t) for t in thresholds)
    if not threshold_values or any(not 0 < t < 1 for t in threshold_values):
        raise ValueError("threshold candidates must be strictly between 0 and 1")
    result: list[Candidate] = []
    for model_type, params in model_grid:
        for features in feature_sets:
            feature_columns = tuple(features)
            if not feature_columns:
                raise ValueError("feature sets must not be empty")
            result.append(Candidate(model_type, dict(params), feature_columns, threshold_values))
    if not result:
        raise ValueError("candidate grid is empty")
    return result
