"""Route validation, definition reload, and auditable strategy state changes."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db.custom_strategy_store import StrategyInUseError
from web.routes import custom_strategy


def _request(name="replace_me", indicator="rsi"):
    return custom_strategy.CustomStrategyRequest(
        name=name, displayName="New version",
        longConditions=[{"indicator": indicator, "operator": "<", "value": 25}],
    )


def _actor():
    return SimpleNamespace(state=SimpleNamespace(user={"username": "tester", "id": 1}), client=None)


def test_update_persists_definition_and_reloads_both_runners(monkeypatch):
    saved = []
    def mutate(name, action, *, definition):
        assert action == "update"
        saved.append(dict(definition, enabled=True))
        return True

    monkeypatch.setattr(custom_strategy, "mutate_definition", mutate)
    monkeypatch.setattr(custom_strategy, "list_definitions", lambda: saved)
    monkeypatch.setattr(custom_strategy, "STRATEGIES", {})
    monkeypatch.setattr(custom_strategy, "STRATEGY_METADATA", {})
    monkeypatch.setattr(custom_strategy, "log_audit", lambda *args: True)
    result = custom_strategy.update_custom_strategy(_request(), _actor(), "replace_me")
    assert result["status"] == "success"
    assert saved[0]["longConditions"] == [{"indicator": "rsi", "operator": "<", "value": 25.0}]
    assert set(custom_strategy.STRATEGIES) == {"replace_me", "inverse_replace_me"}

    saved[0]["enabled"] = False
    custom_strategy.load_custom_strategies()
    assert custom_strategy.STRATEGIES == {}


@pytest.mark.parametrize("request_body", [_request("renamed"), _request(indicator="not_an_indicator")])
def test_update_rejects_name_changes_and_invalid_rules(monkeypatch, request_body):
    def unexpected_mutation(*args, **kwargs):
        pytest.fail("invalid request reached storage")

    monkeypatch.setattr(custom_strategy, "mutate_definition", unexpected_mutation)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.update_custom_strategy(request_body, _actor(), "replace_me")
    assert exc.value.status_code == 400


def test_active_deployment_conflict_exposes_symbol_timeframe_and_id(monkeypatch):
    deployments = [{"id": 42, "symbol": "BTCUSDT", "timeframe": "1h"}]
    def conflict(*args, **kwargs):
        raise StrategyInUseError("Pause active deployments first.", deployments)

    monkeypatch.setattr(custom_strategy, "mutate_definition", conflict)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.toggle_custom_strategy(
            custom_strategy.StrategyEnabledRequest(enabled=False), _actor(), "replace_me",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["deployments"] == deployments
