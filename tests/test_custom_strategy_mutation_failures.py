"""Mutation failure contracts for saved custom strategies; filesystem and registries are isolated."""
import pytest
from fastapi import HTTPException

from web.routes import custom_strategy


def _request(name: str = "mutation_failure_contract") -> custom_strategy.CustomStrategyRequest:
    return custom_strategy.CustomStrategyRequest(
        name=name,
        displayName="Mutation Failure Contract",
        longConditions=[],
        shortConditions=[],
    )


def test_create_write_failure_does_not_register_strategy_or_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(tmp_path / "custom_strategies.json"))
    name = "mutation_failure_contract"
    monkeypatch.delitem(custom_strategy.STRATEGIES, name, raising=False)
    monkeypatch.delitem(custom_strategy.STRATEGIES, f"inverse_{name}", raising=False)
    monkeypatch.delitem(custom_strategy.STRATEGY_METADATA, name, raising=False)

    def fail_write(_saved):
        raise HTTPException(status_code=500, detail="simulated persistence failure")

    monkeypatch.setattr(custom_strategy, "_write_saved_strategies", fail_write)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.create_custom_strategy(_request(name))

    assert exc.value.status_code == 500
    assert name not in custom_strategy.STRATEGIES
    assert f"inverse_{name}" not in custom_strategy.STRATEGIES
    assert name not in custom_strategy.STRATEGY_METADATA
    assert not (tmp_path / "custom_strategies.json").exists()


def test_delete_write_failure_keeps_strategy_registered(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    path.write_text('[{"name": "mutation_failure_contract"}]', encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))
    name = "mutation_failure_contract"
    runner = lambda *_args, **_kwargs: []
    metadata = {"displayName": "Mutation Failure Contract"}
    monkeypatch.setitem(custom_strategy.STRATEGIES, name, runner)
    monkeypatch.setitem(custom_strategy.STRATEGIES, f"inverse_{name}", runner)
    monkeypatch.setitem(custom_strategy.STRATEGY_METADATA, name, metadata)

    def fail_write(_saved):
        raise HTTPException(status_code=500, detail="simulated persistence failure")

    monkeypatch.setattr(custom_strategy, "_write_saved_strategies", fail_write)
    with pytest.raises(HTTPException) as exc:
        custom_strategy.delete_custom_strategy(name)

    assert exc.value.status_code == 500
    assert custom_strategy.STRATEGIES[name] is runner
    assert custom_strategy.STRATEGIES[f"inverse_{name}"] is runner
    assert custom_strategy.STRATEGY_METADATA[name] is metadata
    assert path.read_text(encoding="utf-8") == '[{"name": "mutation_failure_contract"}]'
