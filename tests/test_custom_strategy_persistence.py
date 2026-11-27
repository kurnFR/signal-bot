"""Migration import and validation for the legacy on-disk definitions."""
import json
from unittest.mock import MagicMock

import pytest

from backtest.custom_strategy import read_definitions
from db.custom_strategy_store import import_legacy_definitions


@pytest.mark.parametrize("contents", ["{not valid json", '{"name": "not-a-list"}'])
def test_legacy_import_rejects_corrupt_input_without_writing(tmp_path, contents):
    path = tmp_path / "custom_strategies.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        read_definitions(path)
    assert path.read_text(encoding="utf-8") == contents


def test_legacy_import_maps_enabled_flag_and_preserves_existing_definition():
    cursor = MagicMock()
    saved = {"name": "prior_rule", "enabled": False}
    import_legacy_definitions(cursor, [saved])
    sql, args = cursor.execute.call_args.args
    assert "ON DUPLICATE KEY UPDATE name = name" in sql
    assert args == ("prior_rule", json.dumps(saved), False)


def test_legacy_import_rejects_oversize_name_before_insert():
    cursor = MagicMock()
    with pytest.raises(ValueError):
        import_legacy_definitions(cursor, [{"name": "a" * 81}])
    cursor.execute.assert_not_called()
