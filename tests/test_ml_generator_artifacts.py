import json

import numpy as np
import pytest

from ml.generator_artifacts import (
    GENERATOR_CLASS_MAPPING,
    GENERATOR_TASK_TYPE,
    GeneratorArtifact,
    generator_paper_eligibility_report,
    load_generator_artifact,
    list_generator_artifacts,
    save_generator_artifact,
)


def _artifact(**overrides):
    values = {
        "model_id": "gen-btcusdt-1h-test-0123456789ab",
        "experiment_id": "gen-exp-0123456789ab",
        "model_type": "logistic_regression",
        "feature_columns": ("feature",),
        "class_mapping": dict(GENERATOR_CLASS_MAPPING),
        "decision_policy": {"min_confidence": 0.6, "min_directional_margin": 0.1},
        "label_config": {
            "horizon_bars": 12,
            "barrier_atr_multiple": 1.5,
            "fee_rate": 0.001,
            "slippage_rate": 0.0005,
            "min_net_return": 0.0,
        },
        "data_boundaries": {
            "train_start": 100,
            "train_end": 200,
            "validation_start": 300,
            "validation_end": 400,
            "test_start": 500,
            "test_end": 600,
        },
        "train_metrics": {"rows": 100},
        "validation_metrics": {"trades": 20, "total_net_return": 0.1},
        "test_metrics": {
            "trades": 40,
            "profit_factor": 1.2,
            "max_drawdown_pct": 10.0,
            "total_net_return": 0.2,
        },
    }
    values.update(overrides)
    return GeneratorArtifact(**values)


def _model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    x = np.asarray([[-2.0], [-1.5], [0.0], [1.5], [2.0], [-1.0], [0.5], [1.0]])
    y = np.asarray([0, 0, 1, 2, 2, 0, 1, 2])
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    transformed = scaler.fit_transform(imputer.fit_transform(x))
    model = LogisticRegression(random_state=7).fit(transformed, y)
    model._signal_bot_generator_preprocessor = (imputer, scaler)
    model._signal_bot_generator_feature_columns = ("feature",)
    return model


def test_generator_artifact_round_trip_uses_hardened_model_store(tmp_path):
    artifact = _artifact()
    model = _model()

    weights_path, manifest_path = save_generator_artifact(artifact, model, tmp_path)
    loaded_artifact, loaded_model = load_generator_artifact(artifact.model_id, tmp_path)

    assert weights_path.is_file()
    assert manifest_path.is_file()
    assert loaded_artifact.task_type == GENERATOR_TASK_TYPE
    assert loaded_artifact.weights_sha256
    assert set(loaded_model.classes_) == {0, 1, 2}
    assert loaded_model.predict_proba(np.asarray([[0.0]] )).shape == (1, 3)



def test_generator_model_listing_checks_type_eligibility_and_weight_digest(tmp_path):
    metrics = {
        "trades": 35,
        "profit_factor": 1.1,
        "max_drawdown_pct": 20.0,
        "total_net_return": 0.05,
        "evaluation_mode": "canonical_simulator_oos",
        "execution_engine": "backtest.simulate",
        "non_overlapping_execution": True,
    }
    artifact = _artifact(test_metrics=metrics, eligible_for_paper=True)
    weights_path, _ = save_generator_artifact(artifact, _model(), tmp_path)

    listed = list_generator_artifacts(tmp_path)
    assert len(listed) == 1
    assert listed[0]["model_id"] == artifact.model_id
    assert listed[0]["eligibility"]["eligible"] is True

    weights_path.write_bytes(weights_path.read_bytes() + b"tampered")
    listed_after_tamper = list_generator_artifacts(tmp_path)
    assert listed_after_tamper[0]["eligibility"]["eligible"] is False
    assert "SHA-256 verification" in " ".join(listed_after_tamper[0]["eligibility"]["reasons"])

def test_filter_artifact_cannot_be_loaded_as_generator(tmp_path):
    model_id = "gen-wrong-type-0123456789ab"
    (tmp_path / f"{model_id}.json").write_text(json.dumps({
        "model_id": model_id,
        "task_type": "signal_filter_v1",
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="not an independent ML signal generator"):
        load_generator_artifact(model_id, tmp_path)


def test_generator_artifact_rejects_future_metadata_as_features():
    with pytest.raises(ValueError, match="target/future metadata"):
        _artifact(feature_columns=("feature", "long_net_return"))


def test_generator_artifact_rejects_wrong_class_mapping():
    with pytest.raises(ValueError, match="class mapping"):
        _artifact(class_mapping={"LONG": 0, "HOLD": 1, "SHORT": 2})


def test_paper_eligibility_requires_untouched_test_metrics():
    good = generator_paper_eligibility_report({
        "trades": 35,
        "profit_factor": 1.1,
        "max_drawdown_pct": 20.0,
        "total_net_return": 0.05,
        "evaluation_mode": "canonical_simulator_oos",
        "execution_engine": "backtest.simulate",
        "non_overlapping_execution": True,
    })
    bad = generator_paper_eligibility_report({
        "trades": 10,
        "profit_factor": 0.8,
        "max_drawdown_pct": 30.0,
        "total_net_return": -0.1,
    })

    assert good["eligible"] is True
    assert bad["eligible"] is False
    assert len(bad["reasons"]) == 7



def test_paper_eligibility_rejects_label_opportunity_metrics_without_simulator_evidence():
    report = generator_paper_eligibility_report({
        "trades": 100,
        "profit_factor": 2.0,
        "max_drawdown_pct": 5.0,
        "total_net_return": 0.5,
    })

    assert report["eligible"] is False
    assert any("canonical out-of-sample simulator" in reason for reason in report["reasons"])
    assert any("non-overlapping" in reason for reason in report["reasons"])

def test_paper_eligible_artifact_cannot_bypass_eligibility_gate():
    with pytest.raises(ValueError, match="failed its test gate"):
        _artifact(
            eligible_for_paper=True,
            test_metrics={
                "trades": 10,
                "profit_factor": 0.8,
                "max_drawdown_pct": 30.0,
                "total_net_return": -0.1,
            },
        )


def test_tampered_generator_weights_fail_digest_verification(tmp_path):
    artifact = _artifact()
    weights_path, _ = save_generator_artifact(artifact, _model(), tmp_path)
    weights_path.write_bytes(weights_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="SHA-256"):
        load_generator_artifact(artifact.model_id, tmp_path)



def test_generator_artifact_uses_execution_aligned_label_and_purge_protocols():
    from ml.generator_artifacts import (
        GENERATOR_EVALUATION_PROTOCOL,
        GENERATOR_LABEL_PROTOCOL,
    )

    artifact = _artifact()

    assert artifact.label_protocol_version == "execution_aligned_stop_target_timeout_v2"
    assert artifact.label_protocol_version == GENERATOR_LABEL_PROTOCOL
    assert artifact.evaluation_protocol_version == GENERATOR_EVALUATION_PROTOCOL
    assert artifact.evaluation_protocol_version == "purged_generator_max_execution_end_v2"
