import json
import logging
from unittest.mock import Mock, patch
import pytest

from paper.retailbot2 import DatabaseManager, RetailDeathTrapBot, TelegramAlert


@pytest.fixture(autouse=True)
def _authorize_outbox_enqueue(monkeypatch):
    monkeypatch.setenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY", "1")


class Cursor:
    def __init__(self, *, rowcount=1, lastrowid=91, event=None, fail_on=None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.event = event
        self.fail_on = fail_on
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.fail_on == len(self.calls):
            raise RuntimeError("simulated SQL failure")

    def fetchone(self):
        return self.event

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def db_for(conn):
    db = object.__new__(DatabaseManager)
    db.logger = logging.getLogger("test.retailbot2.outbox")
    db.get_conn = Mock(return_value=conn)
    return db


def test_entry_trade_and_outbox_event_commit_in_one_transaction():
    cursor = Cursor(lastrowid=123)
    conn = Connection(cursor)
    db = db_for(conn)
    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0,
        "stop": 95.0, "target": 110.0, "atr": 2.0, "rr": 2.0,
        "telegram_enabled": True,
    }

    trade_id = db.save_trade(
        signal, 2.0, "shadow",
        entry_alert={"signal": {"symbol": "BTC/USDT"}, "mode": "shadow"},
    )

    assert trade_id == 123
    assert len(cursor.calls) == 2
    assert "INSERT INTO rdt_trades" in cursor.calls[0][0]
    assert "INSERT INTO rdt_telegram_outbox" in cursor.calls[1][0]
    assert cursor.calls[1][1][0] == "retailbot2:entry:123"
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_entry_outbox_failure_rolls_back_trade_insert():
    cursor = Cursor(lastrowid=123, fail_on=2)
    conn = Connection(cursor)
    db = db_for(conn)
    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0,
        "stop": 95.0, "target": 110.0, "atr": 2.0, "rr": 2.0,
        "telegram_enabled": True,
    }

    assert db.save_trade(
        signal, 2.0, "shadow", entry_alert={"signal": {}}
    ) is None
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_exit_close_stats_and_outbox_event_commit_atomically():
    cursor = Cursor()
    conn = Connection(cursor)
    db = db_for(conn)
    alert = {
        "telegram_enabled": True,
        "trade": {"id": 7, "symbol": "BTC/USDT"},
        "exit_price": 110.0, "reason": "TAKE_PROFIT",
        "pnl": 20.0, "pnl_pct": 10.0,
    }

    assert db.close_trade_and_update_stats(
        7, 110.0, "TAKE_PROFIT", 20.0, 10.0, "BB_REV", "shadow",
        exit_alert=alert,
    )
    assert len(cursor.calls) == 3
    assert "INSERT INTO rdt_telegram_outbox" in cursor.calls[2][0]
    assert cursor.calls[2][1][0] == "retailbot2:exit:7"
    assert json.loads(cursor.calls[2][1][2])["trade"]["symbol"] == "BTC/USDT"
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_exit_outbox_failure_rolls_back_trade_close_and_stats():
    cursor = Cursor(fail_on=3)
    conn = Connection(cursor)
    db = db_for(conn)
    alert = {
        "telegram_enabled": True, "trade": {"id": 7},
        "exit_price": 110.0, "reason": "TAKE_PROFIT",
        "pnl": 20.0, "pnl_pct": 10.0,
    }

    assert not db.close_trade_and_update_stats(
        7, 110.0, "TAKE_PROFIT", 20.0, 10.0, "BB_REV", "shadow",
        exit_alert=alert,
    )
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_claim_event_uses_transaction_and_reclaims_expired_lease():
    event = {
        "id": 5, "trade_id": 12, "event_type": "entry",
        "payload_json": '{"mode":"shadow"}', "attempts": 2,
    }
    cursor = Cursor(event=event)
    conn = Connection(cursor)
    db = db_for(conn)

    claimed = db.claim_telegram_outbox_event(lease_seconds=60)

    assert claimed["id"] == 5
    assert claimed["attempts"] == 3
    assert claimed["payload"] == {"mode": "shadow"}
    assert len(claimed["claim_token"]) == 32
    assert "UPDATE rdt_telegram_outbox" in cursor.calls[1][0]
    assert "attempts >= 8" in cursor.calls[1][0]
    assert "FOR UPDATE" in cursor.calls[2][0]
    assert "locked_until <= UTC_TIMESTAMP()" in cursor.calls[2][0]
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_retry_uses_exponential_backoff_then_dead_letters():
    cursor = Cursor()
    conn = Connection(cursor)
    db = db_for(conn)

    token = "a" * 32
    assert db.retry_telegram_outbox_event(5, token, 1, "temporary failure")
    assert cursor.calls[0][1] == ("pending", 15, "temporary failure", 5, token)
    assert db.retry_telegram_outbox_event(5, token, 8, "permanent failure")
    assert cursor.calls[1][1][0] == "dead"
    assert "claim_token=NULL" in cursor.calls[1][0]
    assert "claim_token=%s" in cursor.calls[1][0]
    assert "locked_until > UTC_TIMESTAMP()" in cursor.calls[1][0]


def test_mark_sent_requires_processing_state_and_commits():
    cursor = Cursor()
    conn = Connection(cursor)
    db = db_for(conn)

    assert db.mark_telegram_outbox_sent(5, "b" * 32)
    assert "status='sent'" in cursor.calls[0][0]
    assert "status='processing'" in cursor.calls[0][0]
    assert "claim_token=%s" in cursor.calls[0][0]
    assert "locked_until > UTC_TIMESTAMP()" in cursor.calls[0][0]
    assert conn.commits == 1


def test_legacy_dispatcher_never_sends_or_claims_outbox_events():
    bot = object.__new__(RetailDeathTrapBot)
    bot.tg = Mock()
    bot.tg.enabled = True
    bot.db = Mock()
    bot.logger = Mock()

    assert bot._dispatch_telegram_outbox(limit=3) == 0
    bot.db.get_conn.assert_not_called()
    bot.db.claim_telegram_outbox_event.assert_not_called()
    bot.tg.send_entry.assert_not_called()
    bot.tg.send_exit.assert_not_called()


def test_dispatcher_leaves_events_queued_when_telegram_disabled():
    bot = object.__new__(RetailDeathTrapBot)
    bot.tg = Mock()
    bot.tg.enabled = False
    bot.db = Mock()

    assert bot._dispatch_telegram_outbox() == 0
    bot.db.claim_telegram_outbox_event.assert_not_called()


def test_telegram_send_requires_api_ok_true_even_when_http_200():
    telegram = object.__new__(TelegramAlert)
    telegram.enabled = True
    telegram.base_url = "https://api.telegram.org/botTEST"
    telegram.chat_id = "123"
    telegram.logger = Mock()
    telegram._last_send_time = 0.0
    telegram._min_interval = 0.0

    response = Mock(status_code=200)
    response.json.return_value = {"ok": False, "description": "rejected"}
    with patch("paper.retailbot2.requests.post", return_value=response), \
         patch("paper.retailbot2.time.sleep"):
        assert telegram.send("message", retries=0) is False


def test_telegram_send_accepts_only_http_200_with_api_ok_true():
    telegram = object.__new__(TelegramAlert)
    telegram.enabled = True
    telegram.base_url = "https://api.telegram.org/botTEST"
    telegram.chat_id = "123"
    telegram.logger = Mock()
    telegram._last_send_time = 0.0
    telegram._min_interval = 0.0

    response = Mock(status_code=200)
    response.json.return_value = {"ok": True, "result": {"message_id": 77}}
    with patch("paper.retailbot2.requests.post", return_value=response):
        assert telegram.send("message", retries=0) is True


def test_disabled_trade_never_enqueues_entry_even_if_alert_payload_is_passed():
    cursor = Cursor(lastrowid=321)
    conn = Connection(cursor)
    db = db_for(conn)
    signal = {
        "symbol": "BTC/USDT", "side": "LONG", "entry": 100.0,
        "stop": 95.0, "target": 110.0, "atr": 2.0, "rr": 2.0,
        "telegram_enabled": False,
    }

    assert db.save_trade(
        signal, 2.0, "shadow", entry_alert={"signal": {"symbol": "BTC/USDT"}}
    ) == 321
    assert len(cursor.calls) == 1
    assert "INSERT INTO rdt_telegram_outbox" not in cursor.calls[0][0]
    assert conn.commits == 1


def test_claim_dead_letters_invalid_payload_instead_of_retrying_forever():
    event = {
        "id": 9, "trade_id": 19, "event_type": "entry",
        "payload_json": "{not-json", "attempts": 0,
    }
    cursor = Cursor(event=event)
    conn = Connection(cursor)
    db = db_for(conn)

    assert db.claim_telegram_outbox_event() is None
    assert "SET status='dead'" in cursor.calls[3][0]
    assert conn.commits == 1
    assert conn.rollbacks == 0
