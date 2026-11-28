"""Guards the RetailBot2 decoupling: shared code must not import the retired worker."""
import asyncio
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest


def _imports_retailbot2(code: str) -> bool:
    out = subprocess.run(
        [sys.executable, "-c", code + "\nimport sys\nprint('paper.retailbot2' in sys.modules)"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    return out == "True"


@pytest.mark.parametrize(
    "module_name",
    [
        "paper.indicators",
        "backtest.strategies",
        "backtest.strategies.retail_death_trap",
        "paper.paper_report",
        "paper.outbox_store",
        "paper.outbox_worker",
        "paper.outbox_health",
        "paper.outbox_retention",
        "paper.report_scheduler",
        "notifications.telegram_notifier",
        "wallet_tracker.telegram",
        "web.routes.telegram_routes",
        "web.routes.research_routes",
        "web.app",
    ],
)
def test_runtime_modules_do_not_import_retailbot2_worker(module_name):
    assert not _imports_retailbot2(f"import {module_name}")


def test_retailbot2_worker_module_is_gone():
    """Step 3: paper/retailbot2.py is deleted; the shared helpers live in
    paper/outbox_store.py (and paper/paper_report.py) with no worker left
    to re-export them."""
    import paper.outbox_store as outbox_store

    with pytest.raises(ModuleNotFoundError):
        import paper.retailbot2  # noqa: F401

    for name in (
        "OUTBOX_LOCK_NAME",
        "ALLOWED_OUTBOX_ENQUEUE_DATABASES",
        "is_observation_only",
        "get_connection_database",
        "_resolve_configured_database",
        "_resolve_database_name",
        "is_outbox_enqueue_authorized",
        "is_shadow_outbox_enqueue_authorized",
        "format_entry_message",
        "format_exit_message",
        "build_entry_alert_payload",
        "build_exit_alert_payload",
    ):
        assert hasattr(outbox_store, name), name
    assert outbox_store.OUTBOX_LOCK_NAME == "rdt_outbox_worker_lock"


def test_paper_report_is_the_shared_report(monkeypatch):
    import paper.paper_report as report

    monkeypatch.setattr(report, "paper_performance_lines", lambda: ["LINE"])
    assert "LINE" in report.format_paper_report()


@pytest.mark.parametrize(
    ("env_vars", "expected"),
    [
        ({}, False),
        ({"TELEGRAM_DELIVERY_ENABLED": "0", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, False),
        ({"TELEGRAM_DELIVERY_ENABLED": "1"}, True),
        ({"RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, True),
        ({"TELEGRAM_DELIVERY_ENABLED": "1", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "0"}, True),
        ({"TELEGRAM_DELIVERY_ENABLED": "0", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY": "1"}, True),
    ],
)
def test_telegram_master_switch_and_deprecated_alias(env_vars, expected):
    from notifications import telegram_notifier
    from paper.report_scheduler import queue_current_hour
    from Screen.screening import SmartMoneyTelegram
    from Screen.smart_money_engine import SmartMoneyTelegram as LegacySmartMoneyTelegram
    from wallet_tracker.telegram import WalletTelegramNotifier
    import run_all

    cleaned = {
        k: v for k, v in os.environ.items()
        if k not in ("TELEGRAM_DELIVERY_ENABLED", "RETAILBOT2_ALLOW_TELEGRAM_DELIVERY")
    }
    cleaned.update(env_vars)

    with patch.dict(os.environ, cleaned, clear=True), \
         patch.object(telegram_notifier, "_get_db_telegram_settings", return_value=None):
        assert telegram_notifier.is_telegram_delivery_authorized() is expected

        wallet = WalletTelegramNotifier(token="t", chat_id="123")
        screener = SmartMoneyTelegram(token="t", chat_id="123")
        legacy_screener = LegacySmartMoneyTelegram(token="t", chat_id="123")
        assert wallet.delivery_authorized is expected
        assert screener.delivery_authorized is expected
        assert legacy_screener.delivery_authorized is expected

        db = MagicMock()
        db.enqueue_telegram_outbox_event.return_value = True
        assert queue_current_hour(db, 3600.0) is expected
        if expected:
            db.enqueue_telegram_outbox_event.assert_called_once()
        else:
            db.enqueue_telegram_outbox_event.assert_not_called()

        sup_env = {"RETAILBOT2_OUTBOX_WORKER_ENABLED": "1", **env_vars}
        procs = run_all.build_processes(sup_env)
        assert ("retailbot2_outbox" in procs) is expected


def test_web_lifespan_always_verifies_retailbot2_archive_schema():
    """Step 3: the worker is gone, so the archive schema check is no longer
    gated on RETAILBOT2_ENABLED -- it runs on every startup (warn-only)."""
    import db.db as db_module
    import Screen.screening as screening_module
    from web.app import app, lifespan

    async def _run_lifespan():
        async with lifespan(app):
            pass

    for flag in ("0", "1"):
        with patch.dict(os.environ, {"RETAILBOT2_ENABLED": flag}, clear=False), \
             patch.object(screening_module, "verify_signal_schema") as sig_verify, \
             patch.object(db_module, "verify_retailbot2_schema") as rb2_verify:
            asyncio.run(_run_lifespan())
            sig_verify.assert_called_once_with()
            rb2_verify.assert_called_once_with()
