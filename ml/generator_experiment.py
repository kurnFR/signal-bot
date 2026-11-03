"""End-to-end research orchestration for independent ML signal generators.

This runner is deliberately separate from the legacy ML-filter experiment path.
It trains on candle-level labels, selects its decision policy through the
canonical non-overlapping simulator, evaluates the untouched test segment once,
and persists a typed generator artifact.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from config import BACKTEST_FEE_PCT, BACKTEST_SLIPPAGE_PCT
from backtest.metrics import compute_metrics
from backtest.params import BASE_PARAMS
from backtest.simulate import simulate
from backtest.strategies.ml_signal_generator import (
    _long_condition,
    _short_condition,
    _stop_target,
)
from ml.generator_artifacts import (
    GENERATOR_CLASS_MAPPING,
    GeneratorArtifact,
    generator_paper_eligibility_report,
    load_generator_artifact,
    save_generator_artifact,
)
from ml.generator_dataset import (
    GENERATOR_LABEL_PROTOCOL,
    build_generator_dataset,
)
from ml.generator_trainer import (
    GeneratorTrainingConfig,
    GeneratorTrainingResult,
    _decisions,
    train_generator,
)
from ml.split import split_by_fractions


@dataclass(frozen=True)
class GeneratorExperimentResult:
    artifact: GeneratorArtifact
    model_path: Path
    manifest_path: Path
    dataset_rows: int
    train_rows: int
    validation_rows: int
    test_rows: int
    validation_metrics: Mapping[str, Any]
    test_metrics: Mapping[str, Any]


def _canonical_metrics(
    trades: Sequence[Mapping[str, Any]],
    opportunities: int,
    evaluation_mode: str,
) -> dict[str, Any]:
    """Summarize actual serialized simulator trades, not overlapping labels."""
    normalized = list(trades)
    base = compute_metrics(normalized)
    returns = np.asarray(
        [float(trade.get("net_return_pct", 0.0)) / 100.0 for trade in normalized],
        dtype=float,
    )
    if not np.isfinite(returns).all() or (returns <= -1.0).any():
        raise ValueError("canonical simulator returned invalid net trade returns")

    equity = np.cumprod(1.0 + returns) if len(returns) else np.asarray([], dtype=float)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:] if len(equity) else equity
    drawdowns = (peaks - equity) / np.maximum(peaks, 1e-12) if len(equity) else equity
    profit_factor = base.get("profit_factor")
    if not isinstance(profit_factor, (int, float)) or not np.isfinite(float(profit_factor)):
        profit_factor = None

    previous_exit = None
    non_overlapping = True
    for trade in normalized:
        entry_time = int(trade["entry_time"])
        exit_time = int(trade["exit_time"])
        if exit_time < entry_time or (previous_exit is not None and entry_time <= previous_exit):
            non_overlapping = False
            break
        previous_exit = exit_time

    return {
        "evaluation_mode": evaluation_mode,
        "execution_engine": "backtest.simulate",
        "non_overlapping_execution": non_overlapping,
        "opportunities": int(opportunities),
        "trades": len(normalized),
        "long_trades": sum(trade.get("direction") == "LONG" for trade in normalized),
        "short_trades": sum(trade.get("direction") == "SHORT" for trade in normalized),
        "total_net_return": round(float(equity[-1] - 1.0), 10) if len(equity) else 0.0,
        "expectancy": base.get("expectancy_r"),
        "profit_factor": profit_factor,
        "max_drawdown_pct": round(float(drawdowns.max()) * 100.0, 8) if len(drawdowns) else 0.0,
        "win_rate_pct": base.get("win_rate_pct"),
        "total_pnl_r": base.get("total_pnl_r"),
    }


def _evaluate_partition(
    feature_frame: pd.DataFrame,
    partition: pd.DataFrame,
    probabilities: np.ndarray,
    confidence: float,
    margin: float,
    *,
    evaluation_mode: str,
    strategy_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run only this partition's signals through the shared serial simulator."""
    if len(partition) != len(probabilities):
        raise ValueError("partition and generator probabilities must align")
    decisions = _decisions(probabilities, confidence, margin)
    decision_by_time = {
        int(open_time): int(decision)
        for open_time, decision in zip(partition["open_time"], decisions)
    }
    signal_start = int(partition["open_time"].min())
    simulation_end = int(partition["exit_time"].max())

    frame = feature_frame.loc[feature_frame["open_time"] <= simulation_end].copy()
    available_times = set(pd.to_numeric(frame["open_time"], errors="coerce").astype("int64"))
    if not set(decision_by_time).issubset(available_times):
        raise ValueError("canonical evaluation frame is missing partition signal candles")
    if simulation_end not in available_times:
        raise ValueError("canonical evaluation frame is missing label-resolution boundary")

    frame["_ml_generator_decision"] = frame["open_time"].map(decision_by_time).fillna(1).astype(int)
    params = dict(BASE_PARAMS)
    params.update(dict(strategy_params or {}))
    params["_simulation_start_open_time"] = signal_start
    params["_simulation_end_open_time"] = simulation_end

    trades = simulate(
        frame,
        params,
        _long_condition,
        _short_condition,
        _stop_target,
    )
    return _canonical_metrics(trades, len(partition), evaluation_mode)


def run_generator_experiment(
    feature_frame: pd.DataFrame,
    *,
    model_type: str,
    feature_columns: Sequence[str],
    label_config: Mapping[str, Any] | None = None,
    model_params: Mapping[str, Any] | None = None,
    strategy_params: Mapping[str, Any] | None = None,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    min_validation_trades: int = 10,
    confidence_candidates: tuple[float, ...] = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80),
    margin_candidates: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30),
    seed: int = 42,
    artifact_dir: str | Path = "ml_models",
) -> GeneratorExperimentResult:
    """Train, canonically evaluate, and persist one independent generator.

    The input frame must already contain closed OHLCV and the shared
    point-in-time feature columns. No registered technical strategy is called.
    """
    if not isinstance(feature_frame, pd.DataFrame) or feature_frame.empty:
        raise ValueError("feature_frame must be a non-empty DataFrame")
    if "open_time" not in feature_frame:
        raise ValueError("feature_frame must contain open_time")
    ordered = feature_frame.sort_values("open_time", kind="stable").reset_index(drop=True)
    if ordered["open_time"].duplicated().any():
        raise ValueError("feature_frame open_time values must be unique")
    labels = {
        "horizon_bars": 12,
        "barrier_atr_multiple": 1.5,
        "fee_rate": BACKTEST_FEE_PCT,
        "slippage_rate": BACKTEST_SLIPPAGE_PCT,
        "min_net_return": 0.0,
        **dict(label_config or {}),
    }
    import math
    if not math.isclose(float(labels["fee_rate"]), BACKTEST_FEE_PCT, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("generator fee assumptions must match server paper accounting")
    if not math.isclose(float(labels["slippage_rate"]), BACKTEST_SLIPPAGE_PCT, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("generator slippage assumptions must match server paper accounting")
    effective_strategy_params = dict(strategy_params or {})
    if "BACKTEST_FEE_PCT" in effective_strategy_params and not math.isclose(
        float(effective_strategy_params["BACKTEST_FEE_PCT"]), BACKTEST_FEE_PCT, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError("generator execution fee must match server paper accounting")
    if "BACKTEST_SLIPPAGE_PCT" in effective_strategy_params and not math.isclose(
        float(effective_strategy_params["BACKTEST_SLIPPAGE_PCT"]), BACKTEST_SLIPPAGE_PCT, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError("generator execution slippage must match server paper accounting")
    dataset = build_generator_dataset(
        ordered,
        feature_columns=feature_columns,
        **labels,
    )
    if len(dataset) < 30:
        raise ValueError("generator dataset must contain at least 30 labeled candle opportunities")

    split = split_by_fractions(
        dataset,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
    )
    config = GeneratorTrainingConfig(
        model_type=model_type,
        feature_columns=tuple(feature_columns),
        model_params=dict(model_params or {}),
        seed=seed,
        min_validation_trades=min_validation_trades,
        confidence_candidates=confidence_candidates,
        margin_candidates=margin_candidates,
    )

    def evaluator(name, partition, probabilities, confidence, margin):
        mode = "canonical_simulator_oos" if name == "test" else "canonical_simulator_validation"
        return _evaluate_partition(
            ordered,
            partition,
            probabilities,
            confidence,
            margin,
            evaluation_mode=mode,
            strategy_params=effective_strategy_params,
        )

    training: GeneratorTrainingResult = train_generator(
        split.train,
        split.validation,
        split.test,
        config,
        policy_evaluator=evaluator,
    )
    test_metrics = dict(training.test_metrics)
    validation_metrics = dict(training.validation_metrics)
    if test_metrics.get("evaluation_mode") != "canonical_simulator_oos":
        raise ValueError("generator test metrics did not come from canonical OOS simulation")
    if test_metrics.get("execution_engine") != "backtest.simulate":
        raise ValueError("generator test metrics did not use the canonical simulator")
    if test_metrics.get("non_overlapping_execution") is not True:
        raise ValueError("generator simulator produced overlapping or invalid trade intervals")

    model_id = f"generator-{uuid.uuid4().hex[:20]}"
    times = {
        "train_start": int(split.train["open_time"].min()),
        "train_end": int(split.train["open_time"].max()),
        "validation_start": int(split.validation["open_time"].min()),
        "validation_end": int(split.validation["open_time"].max()),
        "test_start": int(split.test["open_time"].min()),
        "test_end": int(split.test["open_time"].max()),
    }
    artifact_label_config = {
        "label_protocol": GENERATOR_LABEL_PROTOCOL,
        "horizon_bars": int(labels.get("horizon_bars", 12)),
        "barrier_atr_multiple": float(labels.get("barrier_atr_multiple", 1.5)),
        "fee_rate": float(labels["fee_rate"]),
        "slippage_rate": float(labels["slippage_rate"]),
        "min_net_return": float(labels.get("min_net_return", 0.0)),
        "stop_atr_multiple": float(effective_strategy_params.get("ML_GENERATOR_STOP_ATR_MULT", 1.5)),
        "risk_reward_ratio": float(effective_strategy_params.get("ML_GENERATOR_RISK_REWARD_RATIO", 2.0)),
        "max_hold_bars": int(effective_strategy_params.get("BACKTEST_MAX_HOLD_BARS", BASE_PARAMS["BACKTEST_MAX_HOLD_BARS"])),
    }
    artifact = GeneratorArtifact(
        model_id=model_id,
        experiment_id=model_id,
        model_type=model_type,
        feature_columns=tuple(feature_columns),
        class_mapping=dict(GENERATOR_CLASS_MAPPING),
        decision_policy={
            "min_confidence": training.policy.min_confidence,
            "min_directional_margin": training.policy.min_directional_margin,
        },
        label_config=artifact_label_config,
        data_boundaries={
            **times,
            "dataset_rows": len(dataset),
            "train_rows": len(split.train),
            "validation_rows": len(split.validation),
            "test_rows": len(split.test),
            "split_method": "chronological_fraction_with_label_end_purge",
        },
        train_metrics=dict(training.train_metrics),
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        eligible_for_paper=generator_paper_eligibility_report(test_metrics)["eligible"],
    )
    model_path, manifest_path = save_generator_artifact(artifact, training.model, artifact_dir)
    persisted_artifact, _ = load_generator_artifact(model_id, artifact_dir)
    return GeneratorExperimentResult(
        artifact=persisted_artifact,
        model_path=model_path,
        manifest_path=manifest_path,
        dataset_rows=len(dataset),
        train_rows=len(split.train),
        validation_rows=len(split.validation),
        test_rows=len(split.test),
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
    )
