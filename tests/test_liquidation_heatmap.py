import base64

import pytest
from fastapi import HTTPException

from config import FUTURES_WS_COMBINED
from web.routes import liquidations, system_status


def sample_grid():
    return {
        "coin": "BTC", "cols": 2, "rows": 2, "t0": 1_760_000_000_000,
        "dt": 300_000, "as_of": 1_760_000_300_000, "y0": 100, "ratio": 1.1,
        "cap": 10_000, "price": 110, "candles": {"c": [None, 110]},
        "cells": base64.b64encode(bytes([0, 64, 128, 255])).decode(),
    }


def test_decodes_column_major_cells_and_price_buckets():
    result = liquidations.normalize_heatmap(sample_grid())
    assert result["candles"] == [[1_760_000_000_000, None], [1_760_000_300_000, 110]]
    assert result["levels"] == pytest.approx([100 * 1.1**0.5, 100 * 1.1**1.5])
    assert [cell[:2] for cell in result["cells"]] == [[0, 1], [1, 0], [1, 1]]
    assert result["cells"][1][2] == pytest.approx(10_000 * (128 / 255) ** 2)
    assert result["cells"][-1][2] == 10_000


def test_rejects_misaligned_provider_grid():
    with pytest.raises(ValueError):
        liquidations.normalize_heatmap({**sample_grid(), "cells": "AAAAAAA="})


def test_provider_empty_window_is_distinct_from_service_failure():
    assert liquidations.normalize_heatmap({"cols": 0, "rows": 0}) is None
    with pytest.raises(ValueError):
        liquidations.normalize_heatmap({"error": True, "cols": 0, "rows": 0})


def test_only_supported_assets_are_sent_to_provider(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("An unsupported symbol must not reach the provider")

    monkeypatch.setattr(liquidations.requests, "get", unexpected_request)
    with pytest.raises(HTTPException) as error:
        liquidations.get_liquidation_heatmap("UNTRACKED")
    assert error.value.status_code == 400


def test_retired_records_are_not_active_collector_failures(monkeypatch):
    monkeypatch.setattr(system_status, "ENABLE_FUTURES", True)
    status = system_status._display_collector_status
    assert status({"collector": "ws_funding", "status": "error"}) == "retired"
    assert status({"collector": "ws_liquidations", "status": "error"}) == "retired"
    assert status({"collector": "funding_poller", "status": "error"}) == "error"
    assert status({"collector": "ws_kline_futures", "status": "error"}) == "error"
    monkeypatch.setattr(system_status, "ENABLE_FUTURES", False)
    assert status({"collector": "ws_kline_futures", "status": "error"}) == "disabled"


def test_futures_kline_collector_uses_routed_market_endpoint():
    assert FUTURES_WS_COMBINED == "wss://fstream.binance.com/market/stream?streams="
