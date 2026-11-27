"""Test-only entry point for the regular ASGI app with local-only market fixtures."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

if (
    os.environ.get("MYSQL_HOST") != "127.0.0.1"
    or os.environ.get("MYSQL_DATABASE") != "signal_bot_test"
    or not os.environ.get("SIGNAL_BOT_TEST_RUN_ID")
    or not os.environ.get("SIGNAL_BOT_REAL_E2E_PASSWORD")
):
    raise RuntimeError("Browser fixtures require a disposable loopback database and test identity.")

from web.app import app
from web.routes import master_data, ml_routes


def wait_for_cancellation(_url, symbol, _timeframe, _start, _end):
    if symbol != "E2ECANCELUSDT":
        raise ValueError("No market data fixture for this symbol")
    with master_data.JOBS_LOCK:
        job = next(row for row in master_data.JOBS.values() if row["symbol"] == symbol and row.get("cancel_event"))
        cancel_event = job["cancel_event"]
    if not cancel_event.wait(timeout=20):
        raise TimeoutError("Browser did not cancel the fixture backfill")
    return []


master_data.fetch_klines = wait_for_cancellation
model_directory = TemporaryDirectory(prefix="signal-bot-e2e-models-")
ml_routes.ML_MODELS_DIR = Path(model_directory.name)
(ml_routes.ML_MODELS_DIR / "e2e-filter.json").write_text(
    json.dumps({"model_id": "e2e-filter", "base_strategy": "trend_ema_v1"}),
    encoding="utf-8",
)
