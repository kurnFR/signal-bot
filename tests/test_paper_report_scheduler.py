from unittest.mock import Mock

from paper.report_scheduler import queue_current_hour
from run_all import build_processes


def test_report_scheduler_runs_without_retailbot2_worker():
    processes = build_processes({
        "PAPER_REPORT_ENABLED": "1",
        "RETAILBOT2_OUTBOX_WORKER_ENABLED": "1",
        "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1",
    })
    assert "paper_report" in processes
    assert "retailbot2" not in processes
    assert "paper_report" not in build_processes({"PAPER_REPORT_ENABLED": "1"})


def test_scheduler_uses_hourly_idempotent_key_and_delivery_gate(monkeypatch):
    database = Mock()
    monkeypatch.setattr("paper.report_scheduler.format_paper_report", lambda: "Paper report")
    monkeypatch.setenv("TELEGRAM_DELIVERY_ENABLED", "0")
    monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    assert not queue_current_hour(database, 3607)
    database.enqueue_telegram_outbox_event.assert_not_called()

    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "1")
    assert queue_current_hour(database, 3607)
    assert queue_current_hour(database, 3700)
    assert database.enqueue_telegram_outbox_event.call_count == 2
    for call in database.enqueue_telegram_outbox_event.call_args_list:
        assert call.kwargs == {
            "event_key": "paper-performance:1",
            "event_type": "operational",
            "payload": {"text": "Paper report"},
            "deduplicate": True,
        }
