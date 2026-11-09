import pytest

import db.db as db_module


class _LifecycleCursor:
    def __init__(self, *, rows=None, execute_error=None):
        self.rows = rows if rows is not None else []
        self.execute_error = execute_error
        self.closed = False

    def execute(self, *_args, **_kwargs):
        if self.execute_error is not None:
            raise self.execute_error

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class _LifecycleConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self, **_kwargs):
        return self._cursor

    def close(self):
        self.closed = True


class _LifecyclePool:
    def __init__(self, connection):
        self._connection = connection

    def get_connection(self):
        return self._connection


def test_lifecycle_settings_query_failure_propagates_and_closes_resources(monkeypatch):
    cursor = _LifecycleCursor(execute_error=RuntimeError("SELECT permission denied"))
    connection = _LifecycleConnection(cursor)
    monkeypatch.setattr(
        db_module,
        "get_retailbot2_pool",
        lambda: _LifecyclePool(connection),
    )

    with pytest.raises(RuntimeError, match="SELECT permission denied"):
        db_module.get_retailbot2_strategy_lifecycle_settings()

    assert cursor.closed is True
    assert connection.closed is True


def test_empty_lifecycle_settings_table_remains_a_valid_empty_configuration(monkeypatch):
    cursor = _LifecycleCursor(rows=[])
    connection = _LifecycleConnection(cursor)
    monkeypatch.setattr(
        db_module,
        "get_retailbot2_pool",
        lambda: _LifecyclePool(connection),
    )

    assert db_module.get_retailbot2_strategy_lifecycle_settings() == []
    assert cursor.closed is True
    assert connection.closed is True
