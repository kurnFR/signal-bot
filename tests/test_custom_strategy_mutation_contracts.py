"""Saved custom-strategy replacement and idempotent deletion contracts.

All filesystem state and global strategy registry entries are isolated per test.
"""
import json

from web.routes import custom_strategy


def _request(name: str, display_name: str, threshold: int) -> custom_strategy.CustomStrategyRequest:
    return custom_strategy.CustomStrategyRequest(
        name=name,
        displayName=display_name,
        description=f"{display_name} description",
        rationale=f"{display_name} rationale",
        longConditions=[{"indicator": "rsi", "operator": "<", "value": threshold}],
        shortConditions=[{"indicator": "rsi", "operator": ">", "value": 70}],
        slAtrMult=2.0,
        riskRewardRatio=3.0,
    )


def test_create_replaces_existing_definition_and_registry_entry(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    path.write_text(json.dumps([
        {"name": "replace_me", "displayName": "Old version", "longConditions": []},
        {"name": "keep_me", "displayName": "Unrelated strategy"},
    ]), encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    old_runner = lambda *_args, **_kwargs: ["old"]
    old_inverse_runner = lambda *_args, **_kwargs: ["old-inverse"]
    monkeypatch.setitem(custom_strategy.STRATEGIES, "replace_me", old_runner)
    monkeypatch.setitem(custom_strategy.STRATEGIES, "inverse_replace_me", old_inverse_runner)
    monkeypatch.setitem(custom_strategy.STRATEGY_METADATA, "replace_me", {"displayName": "Old version"})

    result = custom_strategy.create_custom_strategy(_request("replace_me", "New version", 25))

    saved = json.loads(path.read_text(encoding="utf-8"))
    replaced = [item for item in saved if item["name"] == "replace_me"]
    assert result["status"] == "success"
    assert result["name"] == "replace_me"
    assert len(replaced) == 1
    assert replaced[0]["displayName"] == "New version"
    assert replaced[0]["longConditions"] == [{"indicator": "rsi", "operator": "<", "value": 25.0}]
    assert [item["name"] for item in saved].count("keep_me") == 1
    assert custom_strategy.STRATEGIES["replace_me"] is not old_runner
    assert custom_strategy.STRATEGIES["inverse_replace_me"] is not old_inverse_runner
    assert custom_strategy.STRATEGY_METADATA["replace_me"]["displayName"] == "New version"
    assert custom_strategy.STRATEGY_METADATA["replace_me"]["longConditions"] == [
        {"indicator": "rsi", "operator": "<", "value": 25.0}
    ]


def test_delete_missing_saved_name_is_idempotent_and_preserves_unrelated_strategies(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    original = [{"name": "keep_me", "displayName": "Unrelated strategy"}]
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    unrelated_runner = lambda *_args, **_kwargs: []
    monkeypatch.setitem(custom_strategy.STRATEGIES, "keep_me", unrelated_runner)
    monkeypatch.setitem(custom_strategy.STRATEGIES, "inverse_keep_me", unrelated_runner)
    monkeypatch.setitem(custom_strategy.STRATEGY_METADATA, "keep_me", {"displayName": "Unrelated strategy"})

    result = custom_strategy.delete_custom_strategy("not_saved")

    assert result["status"] == "success"
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert custom_strategy.STRATEGIES["keep_me"] is unrelated_runner
    assert custom_strategy.STRATEGIES["inverse_keep_me"] is unrelated_runner
    assert custom_strategy.STRATEGY_METADATA["keep_me"] == {"displayName": "Unrelated strategy"}
