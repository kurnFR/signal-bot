from unittest.mock import MagicMock

import pytest

from tools.outbox_status import collect


@pytest.mark.parametrize("row, environment, reason", [
    (None, "1", "explicit_authorization_missing"),
    ((0,), "1", "delivery_disabled_in_settings"),
    ((1,), "0", "environment_delivery_disabled"),
    ((1,), "1", None),
])
def test_reports_gate_preview_and_queue_using_select_only(monkeypatch, row, environment, reason):
    monkeypatch.setenv("TELEGRAM_DELIVERY_ENABLED", environment)
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [("signal_bot_rehearsal_test", 1), row, (3,)]
    cursor.fetchall.side_effect = [[("pending", 4)], [("telegram_outbox", 0, None)], [("engine", None, "ok", 15)]]
    evidence = collect(connection)
    assert evidence["production_authorization_preview_reason"] == reason
    assert evidence["delivery_authorized"] is (bool(row[0]) if row is not None else None)
    assert evidence["queue_counts"] == {"pending": 4}
    assert evidence["due"] == 3
    assert evidence["advisory_lock_held"] is True
    assert evidence["worker_cli_accepts_database"] is False
    assert evidence["services"][0]["enabled"] is False
    assert evidence["heartbeats"][0]["age_seconds"] == 15
    assert all(call.args[0].startswith("SELECT") for call in cursor.execute.call_args_list)
