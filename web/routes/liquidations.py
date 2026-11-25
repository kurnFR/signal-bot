"""Read-only multi-exchange liquidation estimates from Swolecharts."""
import base64
import math
import re
import time
from functools import lru_cache
from typing import Literal

import requests
from fastapi import APIRouter, HTTPException

from config import SYMBOLS

router = APIRouter(prefix="/api/liquidations", tags=["liquidations"])
SOURCE_URL = "https://swolecharts.com/api/liquidation-heatmap/"
COINS = tuple(dict.fromkeys([*(s[:-4] for s in SYMBOLS if s.endswith("USDT")), "SOL", "XRP", "DOGE"]))
HeatmapRange = Literal["12h", "24h", "3d", "7d"]


def normalize_heatmap(data: dict) -> dict | None:
    if data.get("error") or data.get("db") is False:
        raise ValueError("Provider unavailable")
    columns, rows = data["cols"], data["rows"]
    if columns == 0 or rows == 0:
        return None
    if type(columns) is not int or type(rows) is not int or not 2 <= columns <= 720 or not 2 <= rows <= 600:
        raise ValueError("Unexpected heatmap size")
    start, step, updated = (int(data[key]) for key in ("t0", "dt", "as_of"))
    floor, ratio, cap, price = (float(data[key]) for key in ("y0", "ratio", "cap", "price"))
    if (
        not all(math.isfinite(value) for value in (floor, ratio, cap, price))
        or floor <= 0 or not 1 < ratio < 2 or cap < 0 or price <= 0
        or start <= 0 or step <= 0 or updated < start + (columns - 1) * step
    ):
        raise ValueError("Unexpected heatmap axes")
    if cap == 0:
        return None
    encoded = data["cells"]
    if not isinstance(encoded, str) or len(encoded) != ((columns * rows + 2) // 3) * 4:
        raise ValueError("Unexpected heatmap encoding")
    grid = base64.b64decode(encoded, validate=True)
    if len(grid) != columns * rows:
        raise ValueError("Unexpected heatmap grid length")
    closes = data["candles"]["c"]
    if len(closes) != columns:
        raise ValueError("Unexpected candle count")
    candles = [
        [start + index * step, None if close is None else float(close)]
        for index, close in enumerate(closes)
    ]
    if any(close is not None and (not math.isfinite(close) or close <= 0) for _, close in candles):
        raise ValueError("Invalid candle close")
    levels = [floor * ratio ** (row + 0.5) for row in range(rows)]
    if not math.isfinite(levels[-1]):
        raise ValueError("Invalid price levels")
    # The grid is time-column major, with low-to-high prices in each column.
    cells = [[index // rows, index % rows, cap * (value / 255) ** 2] for index, value in enumerate(grid) if value]
    return {
        "levels": levels, "candles": candles, "cells": cells, "updatedAt": updated,
        "price": price, "priceStale": bool(data.get("price_stale")),
    }


@lru_cache(maxsize=12)
def _load_heatmap(coin: str, window: HeatmapRange, refresh_bucket: int) -> dict | None:
    try:
        response = requests.get(SOURCE_URL + coin, params={"range": window}, timeout=(3, 12), allow_redirects=False)
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise HTTPException(status_code=503, detail="The heatmap provider is rate-limited. Try again shortly.")
        if response.status_code != 200:
            raise ValueError("Unexpected provider status")
        data = response.json()
        if data.get("coin") != coin:
            raise ValueError("Unexpected heatmap coin")
        return normalize_heatmap(data)
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise HTTPException(status_code=502, detail="The heatmap provider is temporarily unavailable.") from None


@router.get("/markets")
def list_heatmap_markets():
    return {"symbols": COINS}


@router.get("/heatmap")
def get_liquidation_heatmap(symbol: str = "BTC", window: HeatmapRange = "24h"):
    if symbol not in COINS or not re.fullmatch(r"[A-Z0-9]{1,16}", symbol):
        raise HTTPException(status_code=400, detail="Select a supported asset.")
    return {"heatmap": _load_heatmap(symbol, window, int(time.monotonic()) // 300)}
