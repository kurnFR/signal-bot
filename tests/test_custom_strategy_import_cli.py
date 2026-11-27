import json
import os
from unittest.mock import patch

import pytest

from db.custom_strategy_store import legacy_strategy_drift
from tools import export_custom_strategies, import_custom_strategies


def test_missing_cli_file_warns_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYSQL_DATABASE", "signal_bot_test")
    with patch.object(import_custom_strategies, "get_pool") as pool:
        assert import_custom_strategies.main(["--path", str(tmp_path / "absent.json")]) == 1
    output = capsys.readouterr()
    assert "WARNING" in output.err
    assert "'inserted': 0" in output.out
    pool.assert_not_called()


def test_any_invalid_definition_rejects_the_batch_before_connecting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYSQL_DATABASE", "signal_bot_test")
    path = tmp_path / "strategies.json"
    path.write_text(json.dumps([{"name": "valid"}, {"name": "invalid", "slAtrMult": "bad"}]))
    with patch.object(import_custom_strategies, "get_pool") as pool:
        assert import_custom_strategies.main(["--path", str(path)]) == 1
    assert "'invalid': 1" in capsys.readouterr().out
    pool.assert_not_called()


def test_readiness_surfaces_drift_without_failing(tmp_path, monkeypatch):
    from db import custom_strategy_store
    from web.app import readyz

    source = tmp_path / "strategies.json"
    source.write_text('[{"name":"missing"},{"name":"present"}]')
    monkeypatch.setattr(custom_strategy_store, "LEGACY_PATH", source)
    with patch("db.db.get_pool") as pool, patch.object(custom_strategy_store, "get_pool", pool):
        cursor = pool.return_value.get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"cnt": 6}
        cursor.fetchall.return_value = [("present",)]
        assert readyz() == {"status": "ready", "detail": {"legacy_strategies_not_imported": 1}}
        cursor.fetchall.return_value = [("missing",), ("present",)]
        assert readyz() == {"status": "ready"}


@pytest.mark.skipif(os.getenv("MYSQL_DATABASE") != "signal_bot_test", reason="requires disposable signal_bot_test")
def test_mariadb_import_drift_idempotency_and_export_round_trip(tmp_path):
    from db.db import get_pool

    definition = {"name": "wo13_import_fixture", "displayName": "Fixture", "enabled": False,
                  "longConditions": [{"indicator": "rsi", "operator": "<", "value": 25.5}]}
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps([definition]))
    exported = tmp_path / "exported.json"

    def remove_fixture():
        with get_pool().get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE()")
                assert cursor.fetchone() == ("signal_bot_test",)
                cursor.execute("DELETE FROM custom_strategies WHERE name=%s", (definition["name"],))
                connection.commit()

    remove_fixture()
    try:
        assert legacy_strategy_drift(path=source) == 1
        assert import_custom_strategies.run(source, True) == (1, 0, 0, 0)
        assert import_custom_strategies.run(source, False) == (1, 1, 0, 0)
        assert import_custom_strategies.run(source, False) == (1, 0, 1, 0)
        assert legacy_strategy_drift(path=source) == 0
        assert export_custom_strategies.main(["--out", str(exported)]) == 0
        exported_definition = next(item for item in json.loads(exported.read_text())
                                   if item["name"] == definition["name"])
        assert json.dumps(exported_definition) == json.dumps(definition)
        remove_fixture()
        reimport = tmp_path / "reimport.json"
        reimport.write_text(json.dumps([exported_definition]))
        assert import_custom_strategies.run(reimport, False) == (1, 1, 0, 0)
        assert legacy_strategy_drift(path=source) == 0
    finally:
        remove_fixture()
