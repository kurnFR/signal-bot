"""Unit tests for Telegram response classification and isolated transport."""

import json
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest
import requests

from paper.outbox_transport import (
    STATUS_DELIVERED,
    STATUS_DELIVERY_UNKNOWN,
    STATUS_FAILED_CONFIRMED,
    classify_telegram_response,
    execute_transport,
)


class TestTelegramResponseClassification:
    def test_http_200_ok_true(self):
        body = json.dumps({"ok": True, "result": {"message_id": 12345}})
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_DELIVERED
        assert res["http_status"] == 200
        assert res["result"]["message_id"] == 12345

    def test_http_200_malformed_json(self):
        body = "<html><head><title>502 Bad Gateway</title></head><body>502</body></html>"
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert res["http_status"] == 200
        assert "did not explicitly confirm delivery" in res["error"]

    def test_http_200_missing_ok(self):
        body = json.dumps({"result": {"message_id": 12345}})
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert "missing explicit Telegram ok field" in res["error"]

    def test_http_200_ok_false_is_confirmed_rejection(self):
        body = json.dumps({"ok": False, "description": "Unexpected false ok on 200"})
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["permanent"] is False

    def test_http_200_ok_false_with_permanent_api_error_code(self):
        body = json.dumps({
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found"
        })
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["permanent"] is True

    def test_http_200_ok_false_with_transient_error_code(self):
        body = json.dumps({
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry after 15",
            "parameters": {"retry_after": 15}
        })
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["retry_after"] == 15
        assert res["permanent"] is False

    def test_valid_api_ok_false_general(self):
        body = json.dumps({"ok": False, "error_code": 409, "description": "Conflict"})
        res = classify_telegram_response(409, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["error_code"] == 409
        assert res["permanent"] is False

    def test_http_429_rate_limit_retry_after(self):
        body = json.dumps({
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry after 42",
            "parameters": {"retry_after": 42}
        })
        res = classify_telegram_response(429, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["http_status"] == 429
        assert res["retry_after"] == 42
        assert res["permanent"] is False

    def test_http_420_flood_wait(self):
        body = json.dumps({
            "ok": False,
            "error_code": 420,
            "description": "FLOOD_WAIT_15",
            "parameters": {"retry_after": 15}
        })
        res = classify_telegram_response(420, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["retry_after"] == 15
        assert res["permanent"] is False

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404])
    def test_permanent_api_rejections(self, status_code):
        body = json.dumps({
            "ok": False,
            "error_code": status_code,
            "description": f"Permanent error {status_code}"
        })
        res = classify_telegram_response(status_code, body)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["permanent"] is True

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_http_5xx_ambiguous(self, status_code):
        res = classify_telegram_response(status_code, "Internal Server Error")
        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert res["http_status"] == status_code

    def test_post_send_timeout(self):
        res = classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name="ReadTimeout: socket read timed out",
            pre_connect=False
        )
        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert res["pre_connect"] is False

    def test_preconnect_dns_failure(self):
        res = classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name="ConnectionError: getaddrinfo failed",
            pre_connect=True
        )
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["pre_connect"] is True
        assert res["permanent"] is False

    def test_connection_error_does_not_leak_bot_token(self):
        secret = "123456:VERY_SECRET_TOKEN"
        exc = requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool failed for /bot{secret}/sendMessage"
        )
        with patch("paper.outbox_transport.requests.post", side_effect=exc):
            result = execute_transport({
                "token": secret,
                "chat_id": "-1001",
                "text": "safe test",
            })
        assert result["status"] == STATUS_DELIVERY_UNKNOWN
        assert secret not in json.dumps(result)


class TestTransportCLIExecution:
    def test_transport_cli_delivered_mock(self):
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Hello world",
            "mock_response": {
                "status_code": 200,
                "body": {"ok": True, "result": {"message_id": 999}}
            }
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "paper.outbox_transport"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(json.dumps(payload))
        assert proc.returncode == 0
        res = json.loads(stdout)
        assert res["status"] == STATUS_DELIVERED
        assert res["result"]["message_id"] == 999

    def test_transport_cli_rate_limited_mock(self):
        payload = {
            "token": "123:test_token",
            "chat_id": "-1001",
            "text": "Hello world",
            "mock_response": {
                "status_code": 429,
                "body": {"ok": False, "error_code": 429, "parameters": {"retry_after": 20}}
            }
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "paper.outbox_transport"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, _ = proc.communicate(json.dumps(payload))
        assert proc.returncode == 0
        res = json.loads(stdout)
        assert res["status"] == STATUS_FAILED_CONFIRMED
        assert res["retry_after"] == 20
        assert res["permanent"] is False


class TestWorkerOutcomeHandling:
    def test_permanent_rejection_marks_dead_not_retried(self):
        from paper.outbox_worker import OutboxWorker, OutboxWorkerState
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
        )
        worker.session_mgr = Mock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.claim_event.return_value = {
            "id": 100,
            "claim_token": "token100",
            "event_type": "entry",
            "payload": {
                "signal": {"symbol": "BTC/USDT", "side": "LONG"},
                "units": 1.0,
                "risk_amt": 50.0,
                "risk_pct": 1.0,
                "mode": "shadow",
            },
            "attempts": 1,
        }

        with patch("paper.outbox_worker.dispatch_event_via_subprocess", return_value={
            "status": STATUS_FAILED_CONFIRMED,
            "permanent": True,
            "error_code": 403,
            "description": "Forbidden: bot was blocked by the user",
        }):
            processed = worker.process_one_event()
            assert processed is True

        worker.session_mgr.mark_dead.assert_called_once_with(100, "token100", "Forbidden: bot was blocked by the user")
        worker.session_mgr.mark_retry.assert_not_called()
        worker.session_mgr.mark_sent.assert_not_called()

    def test_ambiguous_delivery_never_marked_sent(self):
        from paper.outbox_worker import OutboxWorker
        worker = OutboxWorker(
            db_config={},
            telegram_token="token",
            telegram_chat_id="chat",
            allowed_databases=("signal_bot_test",),
        )
        worker.session_mgr = Mock()
        worker.session_mgr.check_lock_owned.return_value = True
        worker.session_mgr.claim_event.return_value = {
            "id": 101,
            "claim_token": "token101",
            "event_type": "entry",
            "payload": {
                "signal": {"symbol": "BTC/USDT", "side": "LONG"},
                "units": 1.0,
                "risk_amt": 50.0,
                "risk_pct": 1.0,
                "mode": "shadow",
            },
            "attempts": 1,
        }

        with patch("paper.outbox_worker.dispatch_event_via_subprocess", return_value={
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": "ReadTimeout after transmit",
        }):
            processed = worker.process_one_event()
            assert processed is True

        # Must never mark sent or dead on ambiguous delivery; must mark retry
        worker.session_mgr.mark_sent.assert_not_called()
        worker.session_mgr.mark_dead.assert_not_called()
        worker.session_mgr.mark_retry.assert_called_once_with(101, "token101", 1, "ReadTimeout after transmit")
