"""Execution-aware ML tournament selection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from backtest.metrics import compute_metrics
from backtest.params import BASE_PARAMS
from ml.experiment import ExperimentConfig
from ml.metrics import probability_diagnostics
from ml.result import ExperimentResult
from ml.split import split_by_fractions
from ml.strategy import MLSignalFilter
from ml.tournament import Candidate, CandidateScore, rank_candidates
from ml.trainer import fit_candidate_model


@dataclass(frozen=True)
class ExecutionTournamentResult:
    experiment: ExperimentResult
    winner: Candidate
    ranked_validation: tuple[CandidateScore, ...]
    model: Any
    validation_by_candidate: tuple[Mapping[str, Any], ...]


def _trade_metrics(trades: list[dict]) -> dict[str, Any]:
    metrics = compute_metrics(trades)
    total_r = float(sum(float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None))
    return {**metrics, "total_outcome_r": round(total_r, 8),
            "net_pnl": round(float(sum(float(t.get("net_pnl", 0.0)) for t in trades)), 8)}


def _locked_filter(fitted: Mapping[str, Any], threshold: float) -> MLSignalFilter:
    filt = MLSignalFilter(base_strategy=lambda *_args, **_kwargs: None,
                          model_type=fitted["experiment"].model_type,
                          model_params=fitted["experiment"].model_params,
                          threshold=threshold)
    filt.model = fitted["model"]
    filt.feature_columns = tuple(fitted["feature_columns"])
    return filt


def _candidate_config(experiment_id: str, symbol: str, timeframe: str, base_strategy: str,
                      candidate: Candidate, train_fraction: float, validation_fraction: float,
                      seed: int, target_type: str) -> ExperimentConfig:
    return ExperimentConfig(experiment_id=experiment_id, symbol=symbol, timeframe=timeframe,
                            base_strategy=base_strategy, model_type=candidate.model_type,
                            feature_columns=candidate.feature_columns,
                            model_params=dict(candidate.model_params), probability_threshold=0.60,
                            target_type=target_type, train_fraction=train_fraction,
                            validation_fraction=validation_fraction,
                            test_fraction=1.0 - train_fraction - validation_fraction, seed=seed)


def _score_validation(trades: list[dict], candidate: Candidate, threshold: float, *,
                      min_validation_trades: int) -> CandidateScore | None:
    metrics = _trade_metrics(trades)
    count = int(metrics["total_trades"])
    if count < min_validation_trades:
        return None
    return CandidateScore(candidate=candidate, validation_net_pnl_r=float(metrics["total_outcome_r"]),
                          validation_profit_factor=float(metrics["profit_factor"] or 0.0),
                          validation_max_drawdown_r=float(metrics["max_drawdown_r"] or 0.0),
                          validation_trade_count=count)


def run_execution_aware_tournament(
    market_df: pd.DataFrame, dataset: pd.DataFrame, candidates: Iterable[Candidate], *,
    strategy_fn: Callable[[pd.DataFrame, dict], list[dict]],
    base_params: Mapping[str, Any] | None = None, experiment_id: str, symbol: str,
    timeframe: str, base_strategy: str, train_fraction: float = 0.60,
    validation_fraction: float = 0.20, min_validation_trades: int = 10,
    max_candidates: int = 50, seed: int = 42, threshold_candidates: Iterable[float] | None = None,
    target_type: str = "binary_positive_r",
) -> ExecutionTournamentResult:
    """Select a model/threshold using only execution-aware VALIDATION results.

    TEST is deliberately untouched until the winner and threshold are locked.
    """
    candidate_list = list(candidates)
    if not candidate_list or len(candidate_list) > max_candidates:
        raise ValueError("invalid candidate grid")
    if dataset.empty or market_df.empty:
        raise ValueError("market_df and dataset must not be empty")
    if "open_time" not in dataset.columns or "open_time" not in market_df.columns:
        raise ValueError("market_df and dataset must contain open_time")
    if train_fraction <= 0 or validation_fraction <= 0 or train_fraction + validation_fraction >= 1:
        raise ValueError("invalid train/validation fractions")
    if min_validation_trades < 1:
        raise ValueError("min_validation_trades must be >= 1")

    split = split_by_fractions(dataset, train_fraction=train_fraction, validation_fraction=validation_fraction)
    validation_start, validation_end = int(split.validation["open_time"].min()), int(split.validation["open_time"].max())
    test_start, test_end = int(split.test["open_time"].min()), int(split.test["open_time"].max())
    params_base = dict(BASE_PARAMS if base_params is None else base_params)

    baseline_validation_params = dict(params_base)
    baseline_validation_params["_simulation_start_open_time"] = validation_start
    baseline_validation_params["_simulation_end_open_time"] = validation_end
    baseline_validation_metrics = _trade_metrics(strategy_fn(market_df, baseline_validation_params))

    validation_scores, details, fitted_by_index = [], [], {}
    for index, candidate in enumerate(candidate_list):
        config = _candidate_config(f"{experiment_id}-c{index:03d}", symbol, timeframe,
                                   base_strategy, candidate, train_fraction, validation_fraction,
                                   seed, target_type)
        fitted = fit_candidate_model(split.train, split.validation, config)
        fitted_by_index[index] = fitted
        thresholds = tuple(candidate.threshold_candidates) or tuple(threshold_candidates or ())
        if not thresholds:
            raise ValueError("candidate has no threshold candidates")
        probability_info = probability_diagnostics(split.validation["target"],
                                                   fitted["validation_probabilities"],
                                                   thresholds=thresholds)
        threshold_results, best_score, best_threshold, best_trades = [], None, None, None
        for threshold in thresholds:
            threshold = float(threshold)
            if not 0 < threshold < 1:
                raise ValueError("threshold candidates must be strictly between 0 and 1")
            params = dict(params_base)
            params["_ml_signal_filter"] = _locked_filter(fitted, threshold)
            params["_simulation_start_open_time"] = validation_start
            params["_simulation_end_open_time"] = validation_end
            trades = strategy_fn(market_df, params)
            metrics = _trade_metrics(trades)
            score = _score_validation(trades, candidate, threshold, min_validation_trades=min_validation_trades)
            threshold_results.append({"threshold": threshold, "usable": score is not None, "metrics": metrics})
            if score is not None and (best_score is None or score.rank_key > best_score.rank_key):
                best_score, best_threshold, best_trades = score, threshold, trades
        detail = {"candidate_index": index, "candidate": candidate,
                  "selected_threshold": best_threshold,
                  "selected_metrics": _trade_metrics(best_trades) if best_trades is not None else None,
                  "probability_diagnostics": probability_info,
                  "threshold_results": tuple(threshold_results)}
        details.append(detail)
        if best_score is not None:
            validation_scores.append(best_score)

    if not validation_scores:
        raise ValueError(f"no candidate/threshold produced at least {min_validation_trades} validation trades")

    ranked = rank_candidates(validation_scores)
    winner = ranked[0].candidate
    winner_detail = next(item for item in details if item["candidate"] == winner)
    winner_fit = fitted_by_index[int(winner_detail["candidate_index"])]
    winner_threshold = float(winner_detail["selected_threshold"])

    test_baseline_params = dict(params_base)
    test_baseline_params["_simulation_start_open_time"] = test_start
    test_baseline_params["_simulation_end_open_time"] = test_end
    baseline_test_metrics = _trade_metrics(strategy_fn(market_df, test_baseline_params))

    test_params = dict(params_base)
    test_params["_ml_signal_filter"] = _locked_filter(winner_fit, winner_threshold)
    test_params["_simulation_start_open_time"] = test_start
    test_params["_simulation_end_open_time"] = test_end
    test_metrics = _trade_metrics(strategy_fn(market_df, test_params))

    experiment = ExperimentResult(
        experiment_id=experiment_id, symbol=symbol, timeframe=timeframe,
        base_strategy=base_strategy, model_type=winner.model_type,
        baseline_metrics={"validation": baseline_validation_metrics, "test": baseline_test_metrics,
                          "test_market_rows": int(len(split.test))},
        ml_validation_metrics={**dict(winner_detail["selected_metrics"]),
                                "candidate_count": len(candidate_list),
                                "eligible_candidate_count": len(validation_scores),
                                "threshold_selection": "execution_aware_validation",
                                "probability_diagnostics": dict(winner_detail["probability_diagnostics"]),
                                "target_type": target_type},
        ml_test_metrics=test_metrics, threshold=winner_threshold, status="research")
    return ExecutionTournamentResult(experiment=experiment, winner=winner,
                                     ranked_validation=tuple(ranked), model=winner_fit["model"],
                                     validation_by_candidate=tuple(details))
