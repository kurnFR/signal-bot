import numpy as np
import pandas as pd

from backtest.params import BASE_PARAMS
from backtest.strategies.ml_signal_generator import NAME, run
from ml.generator_artifacts import GENERATOR_CLASS_MAPPING, GeneratorArtifact, save_generator_artifact
from ml.generator_runtime import predict_generator_signals


class _Identity:
    def transform(self, values):
        return np.asarray(values, dtype=float)


class _FixedGeneratorModel:
    classes_ = np.asarray([0, 1, 2])

    def predict_proba(self, values):
        result = []
        for row in np.asarray(values, dtype=float):
            if row[0] < -0.5:
                result.append([0.90, 0.05, 0.05])
            elif row[0] > 0.5:
                result.append([0.05, 0.05, 0.90])
            else:
                result.append([0.05, 0.90, 0.05])
        return np.asarray(result)

    def __init__(self):
        self._signal_bot_generator_preprocessor = (_Identity(), _Identity())
        self._signal_bot_generator_feature_columns = ("feature",)


def _artifact(model_id="gen-runtime-test-0123456789ab"):
    return GeneratorArtifact(
        model_id=model_id,
        experiment_id="gen-runtime-exp-0123456789ab",
        model_type="logistic_regression",
        feature_columns=("feature",),
        class_mapping=dict(GENERATOR_CLASS_MAPPING),
        decision_policy={"min_confidence": 0.6, "min_directional_margin": 0.1},
        label_config={"horizon_bars": 12, "barrier_atr_multiple": 1.5},
        data_boundaries={"train_start": 1, "validation_start": 2, "test_start": 3},
        train_metrics={"rows": 100},
        validation_metrics={"trades": 20, "total_net_return": 0.1},
        test_metrics={"trades": 10, "total_net_return": 0.05},
    )


def _save(tmp_path):
    artifact = _artifact()
    save_generator_artifact(artifact, _FixedGeneratorModel(), tmp_path)
    return artifact


def test_runtime_generates_short_hold_long_without_base_strategy(tmp_path):
    artifact = _save(tmp_path)
    _, model = __import__("ml.generator_artifacts", fromlist=["load_generator_artifact"]).load_generator_artifact(
        artifact.model_id, tmp_path
    )
    frame = pd.DataFrame({"feature": [-1.0, 0.0, 1.0]})

    signals = predict_generator_signals(frame, artifact, model)

    assert signals["decision"].tolist() == [0, 1, 2]
    assert signals["p_short"].iloc[0] == 0.90
    assert signals["p_long"].iloc[2] == 0.90


def test_runtime_fails_closed_for_infinite_or_all_missing_rows(tmp_path):
    artifact = _save(tmp_path)
    _, model = __import__("ml.generator_artifacts", fromlist=["load_generator_artifact"]).load_generator_artifact(
        artifact.model_id, tmp_path
    )
    frame = pd.DataFrame({"feature": [-1.0, np.inf, np.nan]})

    signals = predict_generator_signals(frame, artifact, model)

    assert signals["decision"].tolist() == [0, 1, 1]


def test_runtime_rejects_missing_feature_schema(tmp_path):
    artifact = _save(tmp_path)
    _, model = __import__("ml.generator_artifacts", fromlist=["load_generator_artifact"]).load_generator_artifact(
        artifact.model_id, tmp_path
    )

    try:
        predict_generator_signals(pd.DataFrame({"wrong": [1.0]}), artifact, model)
    except ValueError as exc:
        assert "missing features" in str(exc)
    else:
        raise AssertionError("missing feature schema should fail closed")


def test_strategy_adapter_generates_trades_through_canonical_simulator(tmp_path):
    artifact = _save(tmp_path)
    frame = pd.DataFrame({
        "open_time": [100, 200, 300, 400, 500, 600],
        "open": [100.0] * 6,
        "high": [100.5] * 6,
        "low": [99.5] * 6,
        "close": [100.0] * 6,
        "atr": [1.0] * 6,
        "feature": [0.0, -1.0, 0.0, 0.0, 0.0, 0.0],
    })
    params = dict(
        BASE_PARAMS,
        symbol="BTCUSDT",
        market="spot",
        timeframe="1h",
        ml_generator_model_id=artifact.model_id,
        ML_GENERATOR_MODELS_DIR=str(tmp_path),
        ML_GENERATOR_STOP_ATR_MULT=1.5,
        ML_GENERATOR_RISK_REWARD_RATIO=2.0,
    )

    trades = run(frame, params)

    assert trades
    assert trades[0]["direction"] == "SHORT"
    assert trades[0]["signal_open_time"] == 200
    assert trades[0]["entry_time"] == 300
    assert trades[0]["ml_generator_model_id"] == artifact.model_id
    assert trades[0]["strategy_name"] == NAME
    assert trades[0]["ml_probability"] == 0.90


def test_strategy_fails_closed_without_model_id():
    assert run(pd.DataFrame({"open_time": [1, 2]}), {}) == []


def test_paper_mode_rejects_research_only_generator_artifact(tmp_path):
    artifact = _save(tmp_path)
    frame = pd.DataFrame({
        "open_time": [100, 200, 300],
        "open": [100.0] * 3,
        "high": [100.5] * 3,
        "low": [99.5] * 3,
        "close": [100.0] * 3,
        "atr": [1.0] * 3,
        "feature": [0.0, -1.0, 0.0],
    })
    params = dict(
        BASE_PARAMS,
        ml_generator_model_id=artifact.model_id,
        ML_GENERATOR_MODELS_DIR=str(tmp_path),
        _require_generator_eligible=True,
    )

    assert run(frame, params) == []
