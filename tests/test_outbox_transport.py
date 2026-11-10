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
        assert "invalid or non-JSON" in res["error"]

    def test_http_200_missing_ok(self):
        body = json.dumps({"result": {"message_id": 12345}})
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_DELIVERY_UNKNOWN
        assert "missing ok=true" in res["error"]

    def test_http_200_ok_false(self):
        body = json.dumps({"ok": False, "description": "Unexpected false ok on 200"})
        res = classify_telegram_response(200, body)
        assert res["status"] == STATUS_DELIVERY_UNKNOWN

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
