import pytest

from web.routes.retailbot2_promotion_mapping import resolve_retailbot2_promotion_target


@pytest.fixture
def executable_identities():
    return [
        {"strategy_id": "BB_REV_INV", "mode": "inverse"},
        {"strategy_id": "DBL_PAT_INV", "mode": "inverse"},
        {"strategy_id": "COMPOSITE", "mode": "shadow", "historical_only": True},
    ]


def _candidate(**overrides):
    candidate = {
        "strategy": "backtest_reversal_v1",
        "retailbot2_mapping": {
            "strategy_id": "BB_REV_INV",
            "mode": "inverse",
            "compatible": True,
            "mapping_version": "reviewed-v1",
        },
    }
    candidate.update(overrides)
    return candidate


def test_resolves_explicit_reviewed_mapping_to_registered_executable_identity(executable_identities):
    result = resolve_retailbot2_promotion_target(_candidate(), executable_identities)
    assert result == {
        "source_strategy": "backtest_reversal_v1",
        "strategy_id": "BB_REV_INV",
        "mode": "inverse",
        "mapping_version": "reviewed-v1",
    }


@pytest.mark.parametrize("candidate", [
    {"strategy": "backtest_reversal_v1"},
    {"strategy": "backtest_reversal_v1", "retailbot2_mapping": None},
    {"strategy": "backtest_reversal_v1", "retailbot2_mapping": {"compatible": False}},
    {"strategy": "backtest_reversal_v1", "retailbot2_mapping": {
        "strategy_id": "BB_REV_INV", "mode": "inverse", "compatible": 1,
        "mapping_version": "reviewed-v1",
    }},
])
def test_rejects_missing_or_unapproved_mapping(candidate, executable_identities):
    assert resolve_retailbot2_promotion_target(candidate, executable_identities) is None


@pytest.mark.parametrize("mapping", [
    {"strategy_id": "NOT_REGISTERED", "mode": "inverse", "compatible": True, "mapping_version": "v1"},
    {"strategy_id": "BB_REV_INV", "mode": "both", "compatible": True, "mapping_version": "v1"},
    {"strategy_id": "BB_REV_INV", "mode": "inverse", "compatible": True, "mapping_version": ""},
    {"strategy_id": "COMPOSITE", "mode": "shadow", "compatible": True, "mapping_version": "v1"},
])
def test_rejects_unregistered_invalid_or_historical_only_targets(mapping, executable_identities):
    assert resolve_retailbot2_promotion_target(
        {"strategy": "backtest_reversal_v1", "retailbot2_mapping": mapping},
        executable_identities,
    ) is None


def test_rejects_blank_source_identity(executable_identities):
    assert resolve_retailbot2_promotion_target(_candidate(strategy="  "), executable_identities) is None


@pytest.mark.parametrize("mode", [None, [], {}, 1])
def test_rejects_non_string_modes_without_raising(mode, executable_identities):
    candidate = _candidate(retailbot2_mapping={
        "strategy_id": "BB_REV_INV",
        "mode": mode,
        "compatible": True,
        "mapping_version": "reviewed-v1",
    })
    assert resolve_retailbot2_promotion_target(candidate, executable_identities) is None


def test_rejects_unavailable_executable_registry_without_raising():
    assert resolve_retailbot2_promotion_target(_candidate(), None) is None
