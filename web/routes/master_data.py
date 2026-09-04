import time
import uuid
import threading
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from db.db import get_pool, upsert_ohlcv, fetch_ohlcv_df
from collectors.backfill_klines import fetch_klines, klines_to_rows, KLINE_FETCH_LIMIT
from config import SPOT_KLINE_URL, FUTURES_KLINE_URL
from features.build_features import build_one

router = APIRouter(prefix="/api/master-data", tags=["master-data"])
logger = logging.getLogger("web.master_data")

# In-memory jobs tracking
JOBS = {}
JOBS_LOCK = threading.Lock()


class BackfillRequest(BaseModel):
    symbol: str = Field(..., example="BTCUSDT")
    market: str = Field("spot", example="spot")  # spot or futures
    timeframe: str = Field("1h", example="1h")   # 1m, 5m, 15m, 1h, 4h, 1d
    years: float = Field(1.0, ge=0.01, le=10.0, example=1.0)
    auto_compute_features: bool = Field(True, example=True)


class FeatureBuildRequest(BaseModel):
    symbol: str = Field(..., example="BTCUSDT")
    market: str = Field("spot", example="spot")
    timeframe: str = Field("1h", example="1h")


def _run_backfill_job(job_id: str, symbol: str, market: str, timeframe: str, years: float, auto_compute_features: bool):
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["message"] = f"Initializing backfill for {symbol} {market} {timeframe} ({years} years)..."
            JOBS[job_id]["updated_at"] = time.time()

        url = SPOT_KLINE_URL if market == "spot" else FUTURES_KLINE_URL
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=365 * years)).timestamp() * 1000)
        total_time_span = max(1, end_ms - start_ms)

        cursor = start_ms
        total_rows = 0

        while cursor < end_ms:
            raw = fetch_klines(url, symbol, timeframe, cursor, end_ms)
            if not raw:
                break
            rows = klines_to_rows(symbol, market, timeframe, raw)
            upsert_ohlcv(rows)
            total_rows += len(rows)

            last_open_time = raw[-1][0]
            next_cursor = last_open_time + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            # Calculate progress
            progress_pct = min(95.0, round(((cursor - start_ms) / total_time_span) * 90.0, 1))
            with JOBS_LOCK:
                JOBS[job_id]["progress"] = progress_pct
                JOBS[job_id]["candles_fetched"] = total_rows
                JOBS[job_id]["message"] = f"Fetched & upserted {total_rows:,} candles..."
                JOBS[job_id]["updated_at"] = time.time()

            if len(raw) < KLINE_FETCH_LIMIT:
                break

            time.sleep(0.15)  # respectful of rate limits

        if auto_compute_features and total_rows > 0:
            with JOBS_LOCK:
                JOBS[job_id]["progress"] = 96.0
                JOBS[job_id]["message"] = f"Backfill complete ({total_rows:,} candles). Computing indicators (RSI, MACD, ATR, S/R, Fib)..."
                JOBS[job_id]["updated_at"] = time.time()

            try:
                feat_count = build_one(symbol, market, timeframe)
                msg = f"Completed successfully: {total_rows:,} candles backfilled, {feat_count:,} features computed."
            except Exception as feat_err:
                logger.error(f"Feature computation error for {symbol}: {feat_err}")
                msg = f"Candles backfilled ({total_rows:,}), but feature calculation encountered an error: {feat_err}"
        else:
            msg = f"Backfill complete: {total_rows:,} candles stored."

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["progress"] = 100.0
            JOBS[job_id]["candles_fetched"] = total_rows
            JOBS[job_id]["message"] = msg
            JOBS[job_id]["updated_at"] = time.time()

    except Exception as e:
        logger.exception(f"Backfill job {job_id} failed: {e}")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["message"] = f"Job failed: {str(e)}"
            JOBS[job_id]["updated_at"] = time.time()


def _run_features_job(job_id: str, symbol: str, market: str, timeframe: str):
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["message"] = f"Computing indicators for {symbol} {market} {timeframe}..."
            JOBS[job_id]["updated_at"] = time.time()

        count = build_one(symbol, market, timeframe)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["progress"] = 100.0
            JOBS[job_id]["message"] = f"Successfully computed {count:,} feature rows for {symbol} {market} {timeframe}."
            JOBS[job_id]["updated_at"] = time.time()
    except Exception as e:
        logger.exception(f"Feature job {job_id} failed: {e}")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["message"] = f"Failed to compute features: {str(e)}"
            JOBS[job_id]["updated_at"] = time.time()


@router.get("/coverage")
def get_data_coverage():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Query OHLCV coverage
        cur.execute("""
            SELECT 
                symbol, market, timeframe, 
                COUNT(*) as candle_count,
                MIN(open_time) as min_open_time,
                MAX(close_time) as max_close_time,
                MIN(open_time_wib) as min_wib,
                MAX(close_time_wib) as max_wib
            FROM ohlcv
            GROUP BY symbol, market, timeframe
            ORDER BY symbol, market, FIELD(timeframe, "1m", "5m", "15m", "1h", "4h", "1d")
        """)
        ohlcv_rows = cur.fetchall()

        # Query Features count
        cur.execute("""
            SELECT symbol, market, timeframe, COUNT(*) as feature_count
            FROM features
            GROUP BY symbol, market, timeframe
        """)
        feature_counts = {(r["symbol"], r["market"], r["timeframe"]): r["feature_count"] for r in cur.fetchall()}

        cur.close()

        total_candles = sum(r["candle_count"] for r in ohlcv_rows)
        symbols_set = set(r["symbol"] for r in ohlcv_rows)

        items = []
        for r in ohlcv_rows:
            key = (r["symbol"], r["market"], r["timeframe"])
            f_count = feature_counts.get(key, 0)
            items.append({
                "symbol": r["symbol"],
                "market": r["market"],
                "timeframe": r["timeframe"],
                "candleCount": r["candle_count"],
                "featureCount": f_count,
                "hasFeatures": f_count > 0,
                "minOpenTime": r["min_open_time"],
                "maxCloseTime": r["max_close_time"],
                "startDate": str(r["min_wib"]) if r["min_wib"] else None,
                "endDate": str(r["max_wib"]) if r["max_wib"] else None,
            })

        return {
            "summary": {
                "totalCandles": total_candles,
                "distinctSymbols": len(symbols_set),
                "coverageEntries": len(items),
            },
            "coverage": items,
        }
    finally:
        conn.close()


@router.post("/backfill")
def trigger_backfill(req: BackfillRequest, background_tasks: BackgroundTasks):
    symbol = req.symbol.strip().upper()
    market = req.market.strip().lower()
    timeframe = req.timeframe.strip()

    job_id = str(uuid.uuid4())[:8]
    now = time.time()
    job_info = {
        "id": job_id,
        "type": "backfill",
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "years": req.years,
        "status": "queued",
        "progress": 0.0,
        "candles_fetched": 0,
        "message": f"Queued backfill for {symbol} {market} {timeframe}...",
        "created_at": now,
        "updated_at": now,
    }

    with JOBS_LOCK:
        JOBS[job_id] = job_info

    # Launch background thread
    thread = threading.Thread(
        target=_run_backfill_job,
        args=(job_id, symbol, market, timeframe, req.years, req.auto_compute_features),
        daemon=True
    )
    thread.start()

    return job_info


@router.post("/build-features")
def trigger_build_features(req: FeatureBuildRequest):
    symbol = req.symbol.strip().upper()
    market = req.market.strip().lower()
    timeframe = req.timeframe.strip()

    job_id = str(uuid.uuid4())[:8]
    now = time.time()
    job_info = {
        "id": job_id,
        "type": "build_features",
        "symbol": symbol,
        "market": market,
        "timeframe": timeframe,
        "status": "queued",
        "progress": 0.0,
        "message": f"Queued feature computation for {symbol} {market} {timeframe}...",
        "created_at": now,
        "updated_at": now,
    }

    with JOBS_LOCK:
        JOBS[job_id] = job_info

    thread = threading.Thread(
        target=_run_features_job,
        args=(job_id, symbol, market, timeframe),
        daemon=True
    )
    thread.start()

    return job_info


@router.get("/jobs")
def list_jobs():
    with JOBS_LOCK:
        job_list = list(JOBS.values())
    job_list.sort(key=lambda x: x["created_at"], reverse=True)
    return {"jobs": job_list[:30]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
