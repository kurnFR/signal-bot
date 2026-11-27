"""Mutation failure contracts for saved custom strategies; database and registries are isolated."""
import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from web.routes import custom_strategy


def _request(name: str = "mutation_failure_contract") -> custom_strategy.CustomStrategyRequest:
    return custom_strategy.CustomStrategyRequest(
        name=name,
        displayName="Mutation Failure Contract",
        longConditions=[{"indicator": "rsi", "operator": "<", "value": 30}],
        shortConditions=[],
    )


def test_create_write_failure_does_not_register_strategy_or_metadata(monkeypatch):
    name = "mutation_failure_contract"
    monkeypatch.delitem(custom_strategy.STRATEGIES, name, raising=False)
    monkeypatch.delitem(custom_strategy.STRATEGIES, f"inverse_{name}", raising=False)
    monkeypatch.delitem(custom_strategy.STRATEGY_METADATA, name, raising=False)

    def fail_write(*args, **kwargs):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(custom_strategy, "mutate_definition", fail_write)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.create_custom_strategy(_request(name), SimpleNamespace())

    assert exc.value.status_code == 503
    assert name not in custom_strategy.STRATEGIES
    assert f"inverse_{name}" not in custom_strategy.STRATEGIES
    assert name not in custom_strategy.STRATEGY_METADATA


def test_delete_write_failure_keeps_strategy_registered(monkeypatch):
    name = "mutation_failure_contract"
    runner = lambda *_args, **_kwargs: []
    metadata = {"displayName": "Mutation Failure Contract"}
    monkeypatch.setitem(custom_strategy.STRATEGIES, name, runner)
    monkeypatch.setitem(custom_strategy.STRATEGIES, f"inverse_{name}", runner)
    monkeypatch.setitem(custom_strategy.STRATEGY_METADATA, name, metadata)

    def fail_write(*args, **kwargs):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(custom_strategy, "mutate_definition", fail_write)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.delete_custom_strategy(SimpleNamespace(), name)

    assert exc.value.status_code == 503
    assert custom_strategy.STRATEGIES[name] is runner
    assert custom_strategy.STRATEGIES[f"inverse_{name}"] is runner
    assert custom_strategy.STRATEGY_METADATA[name] is metadata
