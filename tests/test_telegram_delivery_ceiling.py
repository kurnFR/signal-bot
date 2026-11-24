"""Telegram delivery ceiling matrix tests.

RETAILBOT2_ALLOW_TELEGRAM_DELIVERY is a hard environment ceiling over the
Settings UI database toggle (app_telegram_settings.delivery_authorized):

    env flag    DB setting    authorized?
    ----------  ------------  ----------
    not "1"     on            NO  (ceiling closed — the pre-fix bug)
    not "1"     off           NO
    not "1"     (no DB row)   NO
    "1"         on            YES
    "1"         off           NO  (UI toggle off)
    "1"         (no DB row)   YES (env alone decides)

Every delivery consumer must agree: the shared helper in
notifications.telegram_notifier, both Screener engines, the paper report
scheduler, and RetailBot2's direct TelegramAlert.
"""
from unittest.mock import MagicMock, patch

import pytest

import notifications.telegram_notifier as notifier
from notifications.telegram_notifier import is_telegram_delivery_authorized

DB_ON = {"encrypted_bot_token": None, "chat_id": "-1001", "delivery_authorized": 1}
DB_OFF = {"encrypted_bot_token": None, "chat_id": "-1001", "delivery_authorized": 0}


def _gate(monkeypatch, env_value, db_row):
    if env_value is None:
        monkeypatch.delenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", raising=False)
    else:
        monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", env_value)
    return patch.object(notifier, "_get_db_telegram_settings", return_value=db_row)


@pytest.mark.parametrize(
    "env_value,db_row,expected",
    [
        ("0", DB_ON, False),
        ("0", DB_OFF, False),
        ("0", None, False),
        (None, DB_ON, False),
        ("1", DB_ON, True),
        ("1", DB_OFF, False),
        ("1", None, True),
    ],
)
def test_helper_env_is_hard_ceiling(monkeypatch, env_value, db_row, expected):
    with _gate(monkeypatch, env_value, db_row):
        assert is_telegram_delivery_authorized() is expected


def test_config_reports_effective_authorization(monkeypatch):
    """GET /api/telegram/status (via get_telegram_config) must not show
    delivery enabled while the env ceiling is closed."""
    with _gate(monkeypatch, "0", DB_ON):
        cfg = notifier.get_telegram_config()
    assert cfg["source"] == "database"
    assert cfg["delivery_authorized"] is False

    with _gate(monkeypatch, "1", DB_ON):
        cfg = notifier.get_telegram_config()
    assert cfg["delivery_authorized"] is True


@pytest.mark.parametrize(
    "env_value,db_row,expected",
    [("0", DB_ON, False), ("1", DB_ON, True), ("1", DB_OFF, False), ("1", None, True)],
)
def test_screener_engines_follow_ceiling(monkeypatch, env_value, db_row, expected):
    from Screen.screening import SmartMoneyTelegram as ScreeningTelegram
    from Screen.smart_money_engine import SmartMoneyTelegram as EngineTelegram

    with _gate(monkeypatch, env_value, db_row):
        assert ScreeningTelegram("tok", "chat").delivery_authorized is expected
        assert EngineTelegram("tok", "chat").delivery_authorized is expected


@pytest.mark.parametrize(
    "env_value,db_row,expected",
    [("0", DB_ON, False), ("1", DB_OFF, False), ("1", DB_ON, True)],
)
def test_report_scheduler_follows_ceiling(monkeypatch, env_value, db_row, expected):
    from paper.report_scheduler import queue_current_hour

    database = MagicMock()
    database.enqueue_telegram_outbox_event.return_value = True
    with _gate(monkeypatch, env_value, db_row):
        assert queue_current_hour(database, 3600.0 * 100) is expected
    if not expected:
        database.enqueue_telegram_outbox_event.assert_not_called()


@pytest.mark.parametrize(
    "env_value,db_row,expected",
    [("0", DB_ON, False), ("1", DB_OFF, False), ("1", DB_ON, True), ("1", None, True)],
)
def test_retailbot2_alert_global_authorization_follows_ceiling(
    monkeypatch, env_value, db_row, expected
):
    import logging

    from paper.retailbot2 import TelegramAlert

    with _gate(monkeypatch, env_value, db_row):
        alert = TelegramAlert(
            token="<redacted>", chat_id="123456", logger=logging.getLogger("test-ceiling")
        )
    assert alert.global_authorized is expected
