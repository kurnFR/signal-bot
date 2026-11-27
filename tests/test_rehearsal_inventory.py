import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from tools.rehearsal_inventory import compare, read_only_connection, safe_output, snapshot, validate_target


@pytest.mark.parametrize("config, allow", [
    ({"host": "localhost", "database": "crypto_signals"}, ()),
    ({"host": "localhost", "database": "signal_bot_rehearsal_"}, ()),
    ({"host": "remote", "database": "signal_bot_rehearsal_test"}, ()),
    ({"host": "localhost", "database": "signal_bot_rehearsal_test", "unix_socket": "/tmp/db.sock"}, ()),
])
def test_guard_rejects_non_rehearsal_targets(config, allow):
    with pytest.raises(ValueError):
        validate_target(config, allow)


def test_guard_allows_explicit_host_and_verifies_database_on_read_only_session(monkeypatch):
    config = {"host": "rehearsal", "database": "signal_bot_rehearsal_test"}
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (config["database"],)
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr("mysql.connector.connect", connect)
    with read_only_connection(config, ["rehearsal"]):
        pass
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SET SESSION TRANSACTION READ ONLY", "SELECT DATABASE()",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
    ]
    connection.rollback.assert_called_once()
    cursor.fetchone.return_value = ("crypto_signals",)
    with pytest.raises(ValueError):
        with read_only_connection(config, ["rehearsal"]):
            pytest.fail("unsafe connected identity")


def test_evidence_output_is_refused_inside_git_worktree(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(ValueError):
        safe_output(tmp_path / "nested" / "output.json")
    with pytest.raises(ValueError):
        safe_output(Path(__file__).resolve().parent / "output.json")


@pytest.mark.skipif(os.getenv("MYSQL_DATABASE") != "signal_bot_test", reason="requires disposable signal_bot_test")
def test_mariadb_compare_detects_data_and_schema_changes():
    from db.db import get_pool

    table = "rehearsal_inventory_fixture"
    with get_pool().get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            assert cursor.fetchone() == ("signal_bot_test",)
            cursor.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, amount INT, INDEX amount_lookup (amount))")
            try:
                before = snapshot(connection)
                assert compare(before, snapshot(connection), {}) == 0
                cursor.execute(f"INSERT INTO {table} VALUES (1, 5)")
                connection.commit()
                inserted = snapshot(connection)
                assert compare(before, inserted, {}) == 1
                assert compare(before, inserted, {"tables": {table: ["rows", "content_hash"]}}) == 0
                cursor.execute(f"ALTER TABLE {table} MODIFY COLUMN amount BIGINT")
                altered = snapshot(connection)
                assert altered["tables"][table]["schema"] != inserted["tables"][table]["schema"]
                assert compare(inserted, altered, {}) == 1
                cursor.execute(f"ALTER TABLE {table} DROP INDEX amount_lookup")
                dropped = snapshot(connection)
                assert dropped["tables"][table]["schema"] != altered["tables"][table]["schema"]
                assert compare(altered, dropped, {}) == 1
            finally:
                cursor.execute(f"DROP TABLE {table}")
