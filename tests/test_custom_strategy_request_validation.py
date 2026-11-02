"""Request validation contracts for custom strategy endpoints."""
import pytest
from pydantic import ValidationError

from web.routes.custom_strategy import GenerateAIStrategyRequest, QuickTestRequest


def _quick_test_payload(**overrides):
    payload = {
        "strategy": {
            "name": "validation_test",
            "displayName": "Validation Test",
        }
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("symbol,expected", [
    (" btcusdt ", "BTCUSDT"),
    ("ETH2USDT", "ETH2USDT"),
])
def test_quick_test_normalizes_valid_symbols(symbol, expected):
    request = QuickTestRequest(**_quick_test_payload(symbol=symbol))
    assert request.symbol == expected


@pytest.mark.parametrize("symbol", ["", "A", "BTC/USDT", "../BTC", "BTC USDT", "BTC?USDT", "A" * 21])
def test_quick_test_rejects_invalid_symbols(symbol):
    with pytest.raises(ValidationError):
        QuickTestRequest(**_quick_test_payload(symbol=symbol))


@pytest.mark.parametrize("market,expected", [("SPOT", "spot"), (" futures ", "futures")])
def test_quick_test_normalizes_valid_markets(market, expected):
    request = QuickTestRequest(**_quick_test_payload(market=market))
    assert request.market == expected


@pytest.mark.parametrize("market", ["margin", "options", "", "../spot"])
def test_quick_test_rejects_unsupported_markets(market):
    with pytest.raises(ValidationError):
        QuickTestRequest(**_quick_test_payload(market=market))


@pytest.mark.parametrize("timeframe,expected", [("1m", "1m"), ("15m", "15m"), ("1h", "1h"), ("1d", "1d"), ("1M", "1M"), (" 1w ", "1w")])
def test_quick_test_accepts_supported_timeframes(timeframe, expected):
    request = QuickTestRequest(**_quick_test_payload(timeframe=timeframe))
    assert request.timeframe == expected


@pytest.mark.parametrize("timeframe", ["", "2m", "1H", "../1h"])
def test_quick_test_rejects_unsupported_timeframes(timeframe):
    with pytest.raises(ValidationError):
        QuickTestRequest(**_quick_test_payload(timeframe=timeframe))


def test_ai_history_is_reduced_to_the_six_messages_used_by_handler():
    history = [{"role": "user", "content": str(index)} for index in range(10)]
    request = GenerateAIStrategyRequest(prompt="Create a strategy", history=history)
    assert [item["content"] for item in request.history] == ["4", "5", "6", "7", "8", "9"]


def test_ai_history_rejects_oversized_message():
    with pytest.raises(ValidationError, match="at most 2000 characters"):
        GenerateAIStrategyRequest(
            prompt="Create a strategy",
            history=[{"role": "user", "content": "x" * 2001}],
        )


def test_ai_current_config_rejects_oversized_payload():
    with pytest.raises(ValidationError, match="configuration is too large"):
        GenerateAIStrategyRequest(
            prompt="Create a strategy",
            currentConfig={"notes": "x" * 8100},
        )


def test_ai_request_rejects_oversized_combined_context():
    with pytest.raises(ValidationError, match="16000-character limit"):
        GenerateAIStrategyRequest(
            prompt="x" * 5000,
            history=[{"role": "user", "content": "y" * 2000} for _ in range(6)],
            currentConfig={"rules": "z" * 3000},
        )
