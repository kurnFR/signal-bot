"""Isolated Telegram transport child process.

Executed in a separate process group and session by paper.outbox_worker.
Communicates strictly via anonymous stdin/stdout pipes; never receives
database credentials, exchange keys, or CLI secrets.

Guarantees:
- Deterministic response classification.
- Never marks sent unless Telegram API explicitly returns HTTP 200 with ok=true.
- Preserves at-least-once delivery semantics (duplicate Telegram messages remain
  possible when Telegram accepts a request but the acknowledgement cannot be persisted).
- Note: D-state processes can outlive SIGKILL and the cleanup deadline. The 90-second
  lease is a normal-path recovery margin, not proof of safety under every kernel or network failure.
"""

import json
import socket
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

import requests


# Status constants matching worker requirements
STATUS_DELIVERED = "DELIVERED"
STATUS_FAILED_CONFIRMED = "FAILED_CONFIRMED"
STATUS_DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"


def classify_telegram_response(
    http_status: Optional[int],
    body_str: str,
    exception_name: Optional[str] = None,
    pre_connect: bool = False,
) -> Dict[str, Any]:
    """Classify a Telegram delivery attempt into DELIVERED, FAILED_CONFIRMED, or DELIVERY_UNKNOWN.

    Rules:
    - HTTP 200 + valid JSON + ok=true: DELIVERED
    - Valid Telegram API JSON + ok=false: confirmed rejection (FAILED_CONFIRMED)
      - HTTP 429 or error_code 420: transient rejection; honor retry delay
      - Known permanent API rejection (400, 401, 403, 404): dead-letter with error details
      - Unknown API rejection: retry with backoff
    - Missing ok, malformed JSON, HTML error page: DELIVERY_UNKNOWN
    - HTTP 5xx, post-transmission timeout, or connection reset: DELIVERY_UNKNOWN
    - DNS failure or connection refusal proven to occur before transmission: FAILED_CONFIRMED (pre-connect)
    - Ambiguous state: DELIVERY_UNKNOWN
    """
    if exception_name:
        if pre_connect:
            return {
                "status": STATUS_FAILED_CONFIRMED,
                "error": exception_name,
                "pre_connect": True,
                "permanent": False,
            }
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": exception_name,
            "pre_connect": False,
            "permanent": False,
        }

    if http_status is None:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": "No HTTP status received",
            "permanent": False,
        }

    # Attempt to parse body as JSON
    try:
        body_json = json.loads(body_str or "{}")
        if not isinstance(body_json, dict):
            body_json = None
    except (ValueError, TypeError):
        body_json = None

    if http_status == 200 and body_json is not None:
        if body_json.get("ok") is True:
            return {
                "status": STATUS_DELIVERED,
                "http_status": 200,
                "result": body_json.get("result"),
            }
        if body_json.get("ok") is not False:
            # A syntactically valid JSON response without an explicit API rejection
            # does not prove whether Telegram accepted the request.
            return {
                "status": STATUS_DELIVERY_UNKNOWN,
                "http_status": 200,
                "error": "HTTP 200 response missing explicit Telegram ok field",
                "raw_body": (body_str or "")[:500],
            }

    # A valid Telegram API ok=false response is a confirmed rejection, including
    # HTTP 200 responses. Classify retryability from Telegram's error_code.
    if body_json is not None and body_json.get("ok") is False:
        err_code = body_json.get("error_code", http_status)
        desc = str(body_json.get("description", "Telegram API rejection"))
        params = body_json.get("parameters") or {}

        # Rate limits / flood control are confirmed transient rejections.
        if http_status == 429 or err_code in (429, 420) or "FLOOD" in desc.upper():
            retry_after = params.get("retry_after", 30)
            try:
                retry_after = int(retry_after)
            except (ValueError, TypeError):
                retry_after = 30
            return {
                "status": STATUS_FAILED_CONFIRMED,
                "http_status": http_status,
                "error_code": err_code,
                "retry_after": max(1, retry_after),
                "description": desc,
                "permanent": False,
            }

        # Dead-letter only recognized permanent Telegram API error codes.
        if err_code in (400, 401, 403, 404):
            return {
                "status": STATUS_FAILED_CONFIRMED,
                "http_status": http_status,
                "error_code": err_code,
                "description": desc,
                "permanent": True,
            }

        return {
            "status": STATUS_FAILED_CONFIRMED,
            "http_status": http_status,
            "error_code": err_code,
            "description": desc,
            "permanent": False,
        }

    if http_status == 200:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "http_status": 200,
            "error": "HTTP 200 response did not explicitly confirm delivery",
            "raw_body": (body_str or "")[:500],
            "permanent": False,
        }

    # Ambiguous 5xx responses (500, 502, 503, 504) or malformed non-200
    if http_status >= 500:
        return {
            "status": STATUS_DELIVERY_UNKNOWN,
            "http_status": http_status,
            "error": f"Server error HTTP {http_status}",
            "raw_body": (body_str or "")[:500],
            "permanent": False,
        }

    # Unknown HTTP status without valid Telegram JSON
    return {
        "status": STATUS_DELIVERY_UNKNOWN,
        "http_status": http_status,
        "error": f"Unexpected HTTP status {http_status} with unparseable body",
        "raw_body": (body_str or "")[:500],
        "permanent": False,
    }


def execute_transport(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute Telegram sendMessage request or simulated mock response."""
    mock = payload.get("mock_response")
    if mock is not None:
        return _execute_mock(mock)

    token = str(payload.get("token") or "").strip()
    chat_id = str(payload.get("chat_id") or "").strip()
    text = str(payload.get("text") or "")
    parse_mode = payload.get("parse_mode", "HTML")
    disable_web_page_preview = bool(payload.get("disable_web_page_preview", True))
    connect_timeout = float(payload.get("connect_timeout", 5.0))
    read_timeout = float(payload.get("read_timeout", 10.0))

    if not token or not chat_id:
        return {
            "status": STATUS_FAILED_CONFIRMED,
            "error": "Missing token or chat_id in transport input",
            "permanent": True,
        }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        body["parse_mode"] = parse_mode

    try:
        resp = requests.post(
            url,
            json=body,
            timeout=(connect_timeout, read_timeout),
        )
        return classify_telegram_response(
            http_status=resp.status_code,
            body_str=resp.text,
        )
    except requests.exceptions.ConnectTimeout:
        return classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name="ConnectTimeout: failed to connect to api.telegram.org before timeout",
            pre_connect=True,
        )
    except requests.exceptions.ConnectionError as exc:
        is_pre = _is_pre_connect_error(exc)
        return classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name=f"ConnectionError: {exc}",
            pre_connect=is_pre,
        )
    except requests.exceptions.ReadTimeout:
        return classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name="ReadTimeout: socket read timed out after request transmission",
            pre_connect=False,
        )
    except Exception as exc:
        return classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name=f"UnexpectedTransportError: {exc}",
            pre_connect=False,
        )


def _is_pre_connect_error(exc: Exception) -> bool:
    """Determine if a ConnectionError definitely occurred before data transmission."""
    err_str = str(exc).lower()
    # DNS name resolution errors
    if "nameresolutionerror" in err_str or "getaddrinfo failed" in err_str:
        return True
    # Connection refused (TCP RST on SYN)
    if "connection refused" in err_str or "connectionrefusederror" in err_str:
        return True
    # Check causes
    cur = exc
    while cur is not None:
        if isinstance(cur, (socket.gaierror, ConnectionRefusedError)):
            return True
        cur = getattr(cur, "__cause__", None)
    return False


def _execute_mock(mock: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate transport behavior for isolated testing."""
    if "fork_descendant" in mock:
        desc = mock["fork_descendant"]
        sleep_sec = float(desc.get("sleep", 10.0))
        ignore_term = bool(desc.get("ignore_sigterm", False))
        pid_file = desc.get("pid_file")
        code = (
            f"import os, signal, time; "
            f"{f'open({pid_file!r}, \"w\").write(str(os.getpid())); ' if pid_file else ''}"
            f"{'signal.signal(signal.SIGTERM, signal.SIG_IGN); ' if ignore_term else ''}"
            f"time.sleep({sleep_sec})"
        )
        p = subprocess.Popen([sys.executable, "-c", code])
        if pid_file:
            try:
                with open(pid_file, "w") as f:
                    f.write(str(p.pid))
            except Exception:
                pass
        sys.exit(0)

    if "sleep" in mock:
        import time
        time.sleep(float(mock["sleep"]))

    if "exception" in mock:
        exc_type = mock["exception"]
        pre_connect = bool(mock.get("pre_connect", False))
        return classify_telegram_response(
            http_status=None,
            body_str="",
            exception_name=str(exc_type),
            pre_connect=pre_connect,
        )
    status_code = mock.get("status_code", 200)
    body = mock.get("body", "{}")
    if isinstance(body, dict):
        body = json.dumps(body)
    return classify_telegram_response(
        http_status=status_code,
        body_str=str(body),
    )


def main():
    """Main CLI entrypoint for child process."""
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            result = {
                "status": STATUS_FAILED_CONFIRMED,
                "error": "Empty stdin provided to outbox transport",
                "permanent": True,
            }
        else:
            payload = json.loads(raw_input)
            result = execute_transport(payload)
    except Exception as exc:
        result = {
            "status": STATUS_DELIVERY_UNKNOWN,
            "error": f"Transport child exception: {exc}",
        }

    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
