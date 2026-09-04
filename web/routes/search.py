import time
import requests
import logging
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/crypto", tags=["crypto"])
logger = logging.getLogger("web.crypto")

# In-memory caches: { "timestamp": float, "data": list }
_SPOT_CACHE = {"timestamp": 0, "data": []}
_FUTURES_CACHE = {"timestamp": 0, "data": []}
CACHE_TTL = 60  # seconds


def get_spot_tickers():
    now = time.time()
    if now - _SPOT_CACHE["timestamp"] < CACHE_TTL and _SPOT_CACHE["data"]:
        return _SPOT_CACHE["data"]
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=8)
        resp.raise_for_status()
        raw = resp.json()
        usdt_pairs = []
        for item in raw:
            s = item.get("symbol", "")
            if s.endswith("USDT"):
                usdt_pairs.append({
                    "symbol": s,
                    "baseAsset": s[:-4],
                    "quoteAsset": "USDT",
                    "market": "spot",
                    "lastPrice": float(item.get("lastPrice", 0)),
                    "priceChangePercent": float(item.get("priceChangePercent", 0)),
                    "highPrice": float(item.get("highPrice", 0)),
                    "lowPrice": float(item.get("lowPrice", 0)),
                    "volume": float(item.get("volume", 0)),
                    "quoteVolume": float(item.get("quoteVolume", 0)),
                })
        usdt_pairs.sort(key=lambda x: x["quoteVolume"], reverse=True)
        _SPOT_CACHE["timestamp"] = now
        _SPOT_CACHE["data"] = usdt_pairs
        return usdt_pairs
    except Exception as e:
        logger.error(f"Failed to fetch Binance spot tickers: {e}")
        return _SPOT_CACHE["data"]


def get_futures_tickers():
    now = time.time()
    if now - _FUTURES_CACHE["timestamp"] < CACHE_TTL and _FUTURES_CACHE["data"]:
        return _FUTURES_CACHE["data"]
    try:
        resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=8)
        resp.raise_for_status()
        raw = resp.json()
        usdt_pairs = []
        for item in raw:
            s = item.get("symbol", "")
            if s.endswith("USDT"):
                usdt_pairs.append({
                    "symbol": s,
                    "baseAsset": s[:-4],
                    "quoteAsset": "USDT",
                    "market": "futures",
                    "lastPrice": float(item.get("lastPrice", 0)),
                    "priceChangePercent": float(item.get("priceChangePercent", 0)),
                    "highPrice": float(item.get("highPrice", 0)),
                    "lowPrice": float(item.get("lowPrice", 0)),
                    "volume": float(item.get("volume", 0)),
                    "quoteVolume": float(item.get("quoteVolume", 0)),
                })
        usdt_pairs.sort(key=lambda x: x["quoteVolume"], reverse=True)
        _FUTURES_CACHE["timestamp"] = now
        _FUTURES_CACHE["data"] = usdt_pairs
        return usdt_pairs
    except Exception as e:
        logger.error(f"Failed to fetch Binance futures tickers: {e}")
        return _FUTURES_CACHE["data"]


@router.get("/search")
def search_crypto(
    q: str = Query("", description="Symbol or keyword to search"),
    market: str = Query("all", description="Market: spot, futures, or all"),
    limit: int = Query(25, ge=1, le=100),
):
    query = q.strip().upper()
    results = []

    if market in ("spot", "all"):
        spot_list = get_spot_tickers()
        if query:
            spot_list = [x for x in spot_list if query in x["symbol"] or query in x["baseAsset"]]
        results.extend(spot_list)

    if market in ("futures", "all"):
        futures_list = get_futures_tickers()
        if query:
            futures_list = [x for x in futures_list if query in x["symbol"] or query in x["baseAsset"]]
        results.extend(futures_list)

    results.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return {
        "query": q,
        "market": market,
        "count": len(results[:limit]),
        "results": results[:limit],
    }


@router.get("/popular")
def popular_crypto(market: str = Query("spot")):
    if market == "futures":
        items = get_futures_tickers()[:15]
    else:
        items = get_spot_tickers()[:15]
    return {"market": market, "items": items}
