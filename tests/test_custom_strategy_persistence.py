import json

import pytest
from fastapi import HTTPException

from web.routes import custom_strategy


def test_read_saved_strategies_rejects_corrupt_json_without_overwriting(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    original = "{not valid json"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    with pytest.raises(HTTPException) as exc:
        custom_strategy._read_saved_strategies()

    assert exc.value.status_code == 500
    assert path.read_text(encoding="utf-8") == original


def test_read_saved_strategies_rejects_non_list_payload(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    path.write_text(json.dumps({"name": "not-a-list"}), encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    with pytest.raises(HTTPException) as exc:
        custom_strategy._read_saved_strategies()

    assert exc.value.status_code == 500


def test_write_saved_strategies_atomically_persists_json(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    path.write_text('[{"name": "old"}]', encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    custom_strategy._write_saved_strategies([{"name": "new"}])

    assert json.loads(path.read_text(encoding="utf-8")) == [{"name": "new"}]
    assert list(tmp_path.glob(".custom-strategies-*.tmp")) == []


def test_failed_atomic_replace_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "custom_strategies.json"
    original = '[{"name": "preserve-me"}]'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(custom_strategy, "CUSTOM_FILE", str(path))

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(custom_strategy.os, "replace", fail_replace)

    with pytest.raises(HTTPException) as exc:
        custom_strategy._write_saved_strategies([{"name": "new"}])

    assert exc.value.status_code == 500
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".custom-strategies-*.tmp")) == []
