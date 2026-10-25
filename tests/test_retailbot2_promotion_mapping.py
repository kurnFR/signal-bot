import pytest

from backtest.strategies import STRATEGIES
from web.routes.retailbot2_promotion_mapping import resolve_retailbot2_promotion_target


@pytest.fixture
def executable_identities():
    return [
        {"strategy_id": "BB_REV_INV", "mode": "inverse"},
        {"strategy_id": "DBL_PAT_INV", "mode": "inverse"},
        {"strategy_id": "COMPOSITE", "mode": "shadow", "historical_only": True},
    ]


@pytest.fixture
def mapping_registry():
    # Test-only reviewed registry. No production mapping is implied by this fixture.
    return {
        "backtest_reversal_v1": {
            "strategy_id": "BB_REV_INV",
            "mode": "inverse",
            "compatible": True,
            "mapping_version": "reviewed-v1",
        },
    }


def _candidate(**overrides):
    candidate = {"strategy": "backtest_reversal_v1"}
    candidate.update(overrides)
    return candidate


def test_resolves_server_reviewed_mapping_to_registered_executable_identity(
    executable_identities, mapping_registry
):
    result = resolve_retailbot2_promotion_target(
        _candidate(), executable_identities, mapping_registry
    )
    assert result == {
        "source_strategy": "backtest_reversal_v1",
        "strategy_id": "BB_REV_INV",
        "mode": "inverse",
        "mapping_version": "reviewed-v1",
    }


def test_candidate_cannot_self_attest_or_override_reviewed_mapping(
    executable_identities, mapping_registry
):
    candidate = _candidate(retailbot2_mapping={
        "strategy_id": "DBL_PAT_INV",
        "mode": "inverse",
        "compatible": True,
        "mapping_version": "attacker-supplied",
    })
    result = resolve_retailbot2_promotion_target(
        candidate, executable_identities, mapping_registry
    )
    assert result["strategy_id"] == "BB_REV_INV"
    assert result["mapping_version"] == "reviewed-v1"


@pytest.mark.parametrize("candidate", [
    {},
    {"strategy": "  "},
    {"strategy": "unknown_backtest_strategy", "retailbot2_mapping": {
        "strategy_id": "BB_REV_INV", "mode": "inverse", "compatible": True,
        "mapping_version": "attacker-supplied",
    }},
])
def test_rejects_missing_or_unregistered_source(candidate, executable_identities, mapping_registry):
    assert resolve_retailbot2_promotion_target(
        candidate, executable_identities, mapping_registry
    ) is None


@pytest.mark.parametrize("mapping", [
    {"strategy_id": "NOT_REGISTERED", "mode": "inverse", "compatible": True, "mapping_version": "v1"},
    {"strategy_id": "BB_REV_INV", "mode": "both", "compatible": True, "mapping_version": "v1"},
    {"strategy_id": "BB_REV_INV", "mode": "inverse", "compatible": True, "mapping_version": ""},
    {"strategy_id": "COMPOSITE", "mode": "shadow", "compatible": True, "mapping_version": "v1"},
    {"strategy_id": "BB_REV_INV", "mode": "inverse", "compatible": 1, "mapping_version": "v1"},
])
def test_rejects_invalid_reviewed_mapping(mapping, executable_identities):
    registry = {"backtest_reversal_v1": mapping}
    assert resolve_retailbot2_promotion_target(
        _candidate(), executable_identities, registry
    ) is None


@pytest.mark.parametrize("mode", [None, [], {}, 1])
def test_rejects_non_string_modes_without_raising(mode, executable_identities):
    registry = {"backtest_reversal_v1": {
        "strategy_id": "BB_REV_INV",
        "mode": mode,
        "compatible": True,
        "mapping_version": "reviewed-v1",
    }}
    assert resolve_retailbot2_promotion_target(
        _candidate(), executable_identities, registry
    ) is None


def test_rejects_unavailable_executable_registry_without_raising(mapping_registry):
    assert resolve_retailbot2_promotion_target(
        _candidate(), None, mapping_registry
    ) is None


@pytest.mark.parametrize("registry", [None, [], "not-a-registry"])
def test_rejects_unavailable_or_malformed_mapping_registry(registry, executable_identities):
    assert resolve_retailbot2_promotion_target(
        _candidate(), executable_identities, registry
    ) is None

@pytest.mark.parametrize("source_id", sorted(STRATEGIES))
def test_empty_reviewed_registry_rejects_every_registered_backtest_candidate(source_id, executable_identities):
    # No source/target pair has been approved by the compatibility audit.
    # An empty server-owned registry must never silently infer a mapping.
    assert resolve_retailbot2_promotion_target(
        {"strategy": source_id}, executable_identities, {}
    ) is None
