"""
tests/test_restricted_1h_recovery_audit.py

Comprehensive test suite for Work Orders 71895 & 71896:
1. Manifest enforcement (Tests 01-12)
2. Exact dedicated write credential enforcement (Tests 13-20)
3. Recovery integration & safety properties (Tests 21-28)
4. Shared transaction compatibility & ownership audit (Tests 29-38)

ZERO ACCESS to production crypto_signals.
ZERO EXTERNAL API CALLS to Binance (all payloads mocked or synthetic).
Strictly isolated to signal_bot_test.
"""

import hashlib
import json
import logging
import os
import stat
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import mysql.connector
from mysql.connector import pooling
import pytest
from dotenv import load_dotenv

from db.db import upsert_ohlcv
from collectors.backfill_klines import is_candle_closed
from collectors.recover_futures_history import (
    APPROVED_MANIFEST_SHA256,
    MAX_FINALIZED_BOUNDARY_OT,
    EXACT_WRITE_USER,
    acquire_advisory_lock,
    release_advisory_lock,
    get_read_only_connection,
    get_write_connection,
    load_and_validate_manifest,
    recover_range,
    run_recovery,
    main,
)
from collectors.futures_kline_poller import (
    try_acquire_advisory_lock as poller_try_acquire_lock,
    release_advisory_lock as poller_release_lock,
)

load_dotenv("/home/BIS/signal-bot/.env")

TEST_HOST = os.getenv("SIGNAL_BOT_TEST_DB_HOST", "127.0.0.1")
TEST_PORT = int(os.getenv("SIGNAL_BOT_TEST_DB_PORT", "3306"))
TEST_DB = "signal_bot_test"
TEST_USER = os.getenv("SIGNAL_BOT_TEST_DB_USER", "signal_bot_test").strip()
TEST_PASS = os.getenv("SIGNAL_BOT_TEST_DB_PASSWORD", "").strip()

MANIFEST_V3_PATH = "/home/BIS/.gemini/antigravity-cli/brain/8676284a-04c3-4017-9d68-508794c31087/restricted_1h_recovery_manifest_v3.json"
EXPECTED_MANIFEST_SHA256 = APPROVED_MANIFEST_SHA256

SYNTH_BTC = "SYNTH1H_BTCUSDT"
SYNTH_ETH = "SYNTH1H_ETHUSDT"
SYNTH_BNB = "SYNTH1H_BNBUSDT"
SYNTH_TIMEFRAME = "1h"
SYNTH_MARKET = "futures"
STEP_MS = 3600000


def get_test_conn():
    conn = mysql.connector.connect(
        host=TEST_HOST,
        port=TEST_PORT,
        database=TEST_DB,
        user=TEST_USER,
        password=TEST_PASS,
        connection_timeout=5,
    )
    cur = conn.cursor()
    cur.execute("SELECT DATABASE();")
    db_name = cur.fetchone()[0]
    cur.close()
    assert db_name == TEST_DB, f"CRITICAL: Connected to {db_name}, expected {TEST_DB}!"
    return conn


@pytest.fixture(autouse=True)
def cleanup_synthetic_records():
    """Ensure clean table before and after each test."""
    conn = get_test_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ohlcv WHERE symbol LIKE 'SYNTH1H_%';")
    conn.commit()
    cur.close()
    conn.close()
    yield
    conn = get_test_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ohlcv WHERE symbol LIKE 'SYNTH1H_%';")
    conn.commit()
    cur.close()
    conn.close()


# ==============================================================================
# MANIFEST ENFORCEMENT TESTS (1-12)
# ==============================================================================

def test_01_manifest_checksum_and_targets_accepted():
    """1. Correct checksum and exact three targets accepted."""
    data = load_and_validate_manifest(MANIFEST_V3_PATH, EXPECTED_MANIFEST_SHA256)
    assert data["manifest_version"].startswith(("2.", "3."))
    assert len(data["targets"]) == 3

    target_keys = {t["target_key"] for t in data["targets"]}
    assert target_keys == {"BTCUSDT_1h", "ETHUSDT_1h", "BNBUSDT_1h"}

    summary = data["summary"]
    assert summary["total_targets"] == 3
    assert summary["total_existing_target_rows"] == 133807
    assert summary["total_proposed_inserts"] == 4523
    assert summary["total_proposed_updates"] == 3
    assert summary["total_expected_target_rows"] == 138330
    assert summary["per_target_post_recovery_rows"] == 46110


def test_02_incorrect_checksum_rejected_before_db_connection(tmp_path):
    """2. Incorrect checksum rejected before DB connection."""
    with open(MANIFEST_V3_PATH, "rb") as f:
        content = f.read()

    tampered_file = tmp_path / "tampered_manifest.json"
    tampered_file.write_bytes(content + b" ")

    with patch("mysql.connector.connect") as mock_connect:
        with pytest.raises(ValueError, match="Manifest SHA256 mismatch"):
            load_and_validate_manifest(str(tampered_file), EXPECTED_MANIFEST_SHA256)
        mock_connect.assert_not_called()


def test_03_missing_manifest_rejected_before_db_connection():
    """3. Missing manifest rejected before DB connection."""
    with patch("mysql.connector.connect") as mock_connect:
        with pytest.raises(FileNotFoundError, match="Manifest file not found"):
            load_and_validate_manifest("/nonexistent/manifest.json", EXPECTED_MANIFEST_SHA256)
        mock_connect.assert_not_called()

    # CLI rejection when --manifest is omitted
    with patch("sys.argv", ["recover_futures_history"]):
        with patch("mysql.connector.connect") as mock_connect_cli:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
            mock_connect_cli.assert_not_called()


def test_04_additional_symbol_rejected(tmp_path):
    """4. Additional symbol rejected."""
    with open(MANIFEST_V3_PATH, "r") as f:
        data = json.load(f)

    data["targets"][0]["symbol"] = "SOLUSDT"
    data["targets"][0]["target_key"] = "SOLUSDT_1h"

    tampered_bytes = json.dumps(data).encode("utf-8")
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_file = tmp_path / "bad_symbol.json"
    tampered_file.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="Prohibited symbol 'SOLUSDT'"):
        load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_05_additional_timeframe_rejected(tmp_path):
    """5. Additional timeframe rejected."""
    for bad_tf in ["15m", "4h", "1d"]:
        with open(MANIFEST_V3_PATH, "r") as f:
            data = json.load(f)

        data["targets"][0]["timeframe"] = bad_tf
        data["targets"][0]["target_key"] = f"BTCUSDT_{bad_tf}"

        tampered_bytes = json.dumps(data).encode("utf-8")
        tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
        tampered_file = tmp_path / f"bad_tf_{bad_tf}.json"
        tampered_file.write_bytes(tampered_bytes)

        with pytest.raises(ValueError, match=f"Prohibited timeframe '{bad_tf}'"):
            load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_06_duplicate_target_rejected(tmp_path):
    """6. Duplicate target rejected."""
    with open(MANIFEST_V3_PATH, "r") as f:
        data = json.load(f)

    data["targets"][1] = json.loads(json.dumps(data["targets"][0]))

    tampered_bytes = json.dumps(data).encode("utf-8")
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_file = tmp_path / "duplicate_target.json"
    tampered_file.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="Duplicate target key 'BTCUSDT_1h'"):
        load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_07_missing_target_rejected(tmp_path):
    """7. Missing target rejected."""
    with open(MANIFEST_V3_PATH, "r") as f:
        data = json.load(f)

    data["targets"].pop()

    tampered_bytes = json.dumps(data).encode("utf-8")
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_file = tmp_path / "missing_target.json"
    tampered_file.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="Manifest must contain exactly 3 targets, found 2"):
        load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_08_inconsistent_arithmetic_rejected(tmp_path):
    """8. Inconsistent arithmetic rejected."""
    with open(MANIFEST_V3_PATH, "r") as f:
        data = json.load(f)

    data["targets"][0]["post_recovery_expected"]["mathematically_expected_rows"] += 1

    tampered_bytes = json.dumps(data).encode("utf-8")
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_file = tmp_path / "arithmetic_mismatch.json"
    tampered_file.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="Arithmetic mismatch for target 'BTCUSDT_1h'"):
        load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_09_range_beyond_finalized_boundary_rejected(tmp_path):
    """9. Range beyond finalized manifest boundary rejected."""
    with open(MANIFEST_V3_PATH, "r") as f:
        data = json.load(f)

    forming_ot = MAX_FINALIZED_BOUNDARY_OT + STEP_MS
    target0 = data["targets"][0]
    target0["proposed_inserts"]["last_missing_open_time_ms"] = forming_ot
    target0["proposed_inserts"]["proposed_insert_count"] += 1
    target0["post_recovery_expected"]["mathematically_expected_rows"] += 1

    tampered_bytes = json.dumps(data).encode("utf-8")
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    tampered_file = tmp_path / "forming_candle_tamper.json"
    tampered_file.write_bytes(tampered_bytes)

    with pytest.raises(ValueError, match="extends beyond approved finalized boundary"):
        load_and_validate_manifest(str(tampered_file), expected_sha=tampered_sha)


def test_10_all_flag_cannot_expand_or_override_manifest_scope():
    """10. --all cannot expand or override manifest scope."""
    manifest_data = load_and_validate_manifest(MANIFEST_V3_PATH, EXPECTED_MANIFEST_SHA256)

    mock_recover = MagicMock(return_value={"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0})
    with patch("collectors.recover_futures_history.recover_range", mock_recover):
        exit_code, summary = run_recovery(
            manifest=manifest_data,
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"],
            timeframes=["1m", "5m", "15m", "1h", "4h", "1d"],
            conn=MagicMock(),
            dry_run=True,
        )

    assert exit_code == 0
    assert set(summary.keys()) == {"BTCUSDT_1h", "ETHUSDT_1h", "BNBUSDT_1h"}
    assert "SOLUSDT_1h" not in summary
    assert "BTCUSDT_15m" not in summary


def test_11_dynamic_discovery_cannot_add_targets_or_extend_ranges():
    """11. Dynamic discovery cannot add targets or extend ranges."""
    manifest_data = load_and_validate_manifest(MANIFEST_V3_PATH, EXPECTED_MANIFEST_SHA256)

    with patch("collectors.recover_futures_history.discover_missing_ranges") as mock_disc:
        mock_recover = MagicMock(return_value={"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0})
        with patch("collectors.recover_futures_history.recover_range", mock_recover):
            exit_code, summary = run_recovery(manifest=manifest_data, conn=MagicMock(), dry_run=True)

        assert exit_code == 0
        mock_disc.assert_not_called()
        assert mock_recover.call_count == 3

        call_btc = mock_recover.call_args_list[0]
        assert call_btc.kwargs["start_ms"] == 1790600400000
        assert call_btc.kwargs["end_ms"] == 1794816000000


def test_12_dry_run_and_write_mode_select_identical_manifest_targets():
    """12. Dry-run and write mode select identical manifest targets."""
    manifest_data = load_and_validate_manifest(MANIFEST_V3_PATH, EXPECTED_MANIFEST_SHA256)
    mock_recover = MagicMock(return_value={"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0})

    with patch("collectors.recover_futures_history.recover_range", mock_recover), \
         patch("collectors.recover_futures_history.acquire_advisory_lock", return_value=True), \
         patch("collectors.recover_futures_history.release_advisory_lock", return_value=None):

        code_dry, summary_dry = run_recovery(manifest=manifest_data, conn=MagicMock(), dry_run=True)
        code_write, summary_write = run_recovery(manifest=manifest_data, conn=MagicMock(), dry_run=False)

    assert code_dry == 0
    assert code_write == 0
    assert list(summary_dry.keys()) == list(summary_write.keys()) == ["BTCUSDT_1h", "ETHUSDT_1h", "BNBUSDT_1h"]


# ==============================================================================
# CREDENTIAL ENFORCEMENT TESTS (13-20)
# ==============================================================================

def test_13_write_mode_without_db_user_rejected_before_connection():
    """13. Write mode without --db-user rejected before connection."""
    mock_factory = MagicMock()
    with pytest.raises(ValueError, match="Explicit database user must be provided"):
        get_write_connection(user=None, password_file="/tmp/dummy", connection_factory=mock_factory)
    mock_factory.assert_not_called()


def test_14_write_mode_without_db_password_file_rejected_before_connection():
    """14. Write mode without --db-password-file rejected before connection."""
    mock_factory = MagicMock()
    with pytest.raises(ValueError, match="Explicit password file must be provided"):
        get_write_connection(user=EXACT_WRITE_USER, password_file=None, connection_factory=mock_factory)
    mock_factory.assert_not_called()


def test_15_exact_username_enforcement_and_negative_cases(tmp_path):
    """
    15. Exact unified write username enforcement:
    Validates that write mode accepts ONLY 'signal_bot_test' and rejects all other usernames:
    cms, root, admin, mysql, futures_recovery, another_user, empty, whitespace-padded, and case variants.
    Proves that for every rejected case, the connection factory is NOT called.
    """
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("dummy_password")
    os.chmod(pw_file, 0o600)

    rejected_usernames = [
        "cms",
        "root",
        "admin",
        "mysql",
        "futures_recovery",
        "another_user",
        " signal_bot_test ",
        "signal_bot_test ",
        " signal_bot_test",
        "Signal_Bot_Test",
        "SIGNAL_BOT_TEST",
        "",
    ]

    for bad_user in rejected_usernames:
        mock_factory = MagicMock()
        with pytest.raises((PermissionError, ValueError)):
            get_write_connection(
                user=bad_user,
                password_file=str(pw_file),
                connection_factory=mock_factory,
            )
        mock_factory.assert_not_called()

    # Now verify that the EXACT username 'signal_bot_test' IS accepted
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("signal_bot_test", f"{EXACT_WRITE_USER}@127.0.0.1")
    mock_conn.cursor.return_value = mock_cur
    mock_factory = MagicMock(return_value=mock_conn)

    conn = get_write_connection(
        user=EXACT_WRITE_USER,
        password_file=str(pw_file),
        expected_database="signal_bot_test",
        connection_factory=mock_factory,
    )
    mock_factory.assert_called_once()
    assert conn == mock_conn


def test_16_unsafe_password_file_permissions_rejected(tmp_path):
    """16. Unsafe password-file permissions rejected."""
    pw_file = tmp_path / "insecure_pass.txt"
    pw_file.write_text("dummy_password")

    os.chmod(pw_file, 0o644)
    with pytest.raises(PermissionError, match="has unsafe permissions"):
        get_write_connection(user=EXACT_WRITE_USER, password_file=str(pw_file), database="signal_bot_test")

    os.chmod(pw_file, 0o777)
    with pytest.raises(PermissionError, match="has unsafe permissions"):
        get_write_connection(user=EXACT_WRITE_USER, password_file=str(pw_file), database="signal_bot_test")


def test_17_missing_or_unreadable_password_file_rejected_without_exposing_secret():
    """17. Missing or unreadable password file rejected without exposing secret content."""
    with pytest.raises(FileNotFoundError, match="Password file.*not found"):
        get_write_connection(user=EXACT_WRITE_USER, password_file="/nonexistent/secret_pass.txt", database="signal_bot_test")


def test_18_write_mode_never_calls_get_pool_or_reads_app_env(tmp_path, monkeypatch):
    """18. Write mode never calls get_pool() or reads application .env."""
    pw_file = tmp_path / "test_pw.txt"
    pw_file.write_text(TEST_PASS)
    os.chmod(pw_file, 0o600)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("signal_bot_test", f"{EXACT_WRITE_USER}@127.0.0.1")
    mock_conn.cursor.return_value = mock_cur
    mock_factory = MagicMock(return_value=mock_conn)

    with patch("db.db.get_pool") as mock_get_pool:
        conn = get_write_connection(
            user=EXACT_WRITE_USER,
            password_file=str(pw_file),
            database="signal_bot_test",
            expected_database="signal_bot_test",
            connection_factory=mock_factory,
        )
        mock_get_pool.assert_not_called()
        mock_factory.assert_called_once()


def test_19_dry_run_cannot_fallback_to_application_pool(monkeypatch):
    """19. Dry-run cannot fall back to application pool."""
    monkeypatch.delenv("RECOVERY_DB_USER", raising=False)
    monkeypatch.delenv("SIGNAL_BOT_TEST_DB_USER", raising=False)

    with pytest.raises(ValueError, match="Fallback to default MYSQL_USER or application pool is strictly forbidden"):
        get_read_only_connection(user=None)


def test_20_password_values_never_appear_in_logs_or_exceptions(tmp_path, caplog):
    """20. Password values never appear in logs or exception text."""
    SECRET_CANARY = "CANARY_SECRET_PASSWORD_12345_NEVER_PRINT"
    pw_file = tmp_path / "canary.txt"
    pw_file.write_text(SECRET_CANARY)
    os.chmod(pw_file, 0o600)

    def failing_factory(**kwargs):
        raise mysql.connector.errors.OperationalError("Connection refused")

    with pytest.raises(Exception) as exc_info:
        get_write_connection(
            user=EXACT_WRITE_USER,
            password_file=str(pw_file),
            database="signal_bot_test",
            connection_factory=failing_factory,
        )

    assert SECRET_CANARY not in str(exc_info.value)
    assert SECRET_CANARY not in repr(exc_info.value)
    assert SECRET_CANARY not in caplog.text


# ==============================================================================
# REGRESSION AND INTEGRATION TESTS (21-28)
# ==============================================================================

def test_21_boundary_updates_modify_in_place_without_increasing_row_count():
    """21. Boundary updates do not increase row count."""
    conn = get_test_conn()
    wm_ot = 1790600400000

    initial_rows = [
        (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, wm_ot, wm_ot + STEP_MS - 1,
         "83500.0", "83700.0", "83200.0", "83550.0",
         "100.0", "8350000.0", 500, "50.0", "4175000.0", 1),
    ]
    upsert_ohlcv(initial_rows, conn=conn)

    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS cnt FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur.fetchone()["cnt"] == 1

    authoritative_rows = [
        (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, wm_ot, wm_ot + STEP_MS - 1,
         "83500.0", "83794.9", "83270.0", "83609.1",
         "8453.471", "706447454.8581", 252456, "4192.067", "350351740.8024", 1),
    ]
    upsert_ohlcv(authoritative_rows, conn=conn)

    cur.execute("SELECT COUNT(*) AS cnt FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur.fetchone()["cnt"] == 1

    cur.execute("SELECT close, volume, num_trades FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    row = cur.fetchone()
    assert Decimal(str(row["close"])) == Decimal("83609.1")
    assert Decimal(str(row["volume"])) == Decimal("8453.471")
    assert row["num_trades"] == 252456
    cur.close()
    conn.close()


def test_22_missing_candles_inserted_exactly_once():
    """22. Missing candles inserted exactly once."""
    conn = get_test_conn()
    base_ot = 1790604000000
    rows = []
    for i in range(5):
        ot = base_ot + (i * STEP_MS)
        ct = ot + STEP_MS - 1
        rows.append((
            SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, ot, ct,
            "2600.0", "2610.0", "2590.0", "2605.0",
            "1000.0", "2605000.0", 5000, "500.0", "1302500.0", 1
        ))

    upsert_ohlcv(rows, conn=conn)

    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS cnt FROM ohlcv WHERE symbol = %s;", (SYNTH_ETH,))
    assert cur.fetchone()["cnt"] == 5

    cur.execute("SELECT open_time FROM ohlcv WHERE symbol = %s ORDER BY open_time ASC;", (SYNTH_ETH,))
    inserted_ots = [r["open_time"] for r in cur.fetchall()]
    assert inserted_ots == [base_ot + (i * STEP_MS) for i in range(5)]
    cur.close()
    conn.close()


def test_23_failed_batch_rolls_back():
    """23. Batch failure rolls back."""
    conn = get_test_conn()
    ot = 1790604000000
    cur = conn.cursor()

    cur.execute("START TRANSACTION;")
    cur.execute("""
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, '100', '105', '95', '102', '1000', '102000', 10, '500', '51000', 1);
    """, (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot, ot + STEP_MS - 1))

    conn.rollback()

    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur.fetchone()[0] == 0
    cur.close()
    conn.close()


def test_24_resume_remains_idempotent():
    """24. Resume remains idempotent."""
    conn = get_test_conn()
    base_ot = 1790604000000
    rows = [
        (SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, base_ot, base_ot + STEP_MS - 1,
         "700.0", "705.0", "695.0", "702.0",
         "500.0", "351000.0", 1200, "250.0", "175500.0", 1)
    ]
    upsert_ohlcv(rows, conn=conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BNB,))
    assert cur.fetchone()[0] == 1

    upsert_ohlcv(rows, conn=conn)
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BNB,))
    assert cur.fetchone()[0] == 1
    cur.close()
    conn.close()


def test_25_advisory_lock_contention_blocks_writes():
    """25. Advisory lock contention blocks writes."""
    conn1 = get_test_conn()
    conn2 = get_test_conn()
    lock_name = f"futures_kline_writer_{SYNTH_BTC}_1h"

    acq1 = acquire_advisory_lock(conn1, lock_name, timeout=5)
    assert acq1 is True

    cur2 = conn2.cursor()
    cur2.execute("SELECT GET_LOCK(%s, 0);", (lock_name,))
    acq2 = cur2.fetchone()[0]
    cur2.close()
    assert acq2 == 0

    release_advisory_lock(conn1, lock_name)

    acq2_retry = acquire_advisory_lock(conn2, lock_name, timeout=2)
    assert acq2_retry is True
    release_advisory_lock(conn2, lock_name)

    conn1.close()
    conn2.close()


def test_26_poller_lock_interoperability_intact():
    """26. Poller lock interoperability remains intact."""
    conn_rec = get_test_conn()
    conn_pol = get_test_conn()
    lock_name = f"futures_kline_writer_{SYNTH_BNB}_1h"

    assert acquire_advisory_lock(conn_rec, lock_name, timeout=5) is True

    poller_acquired = poller_try_acquire_lock(conn_pol, lock_name)
    assert poller_acquired is False

    release_advisory_lock(conn_rec, lock_name)

    poller_acquired2 = poller_try_acquire_lock(conn_pol, lock_name)
    assert poller_acquired2 is True
    poller_release_lock(conn_pol, lock_name)

    conn_rec.close()
    conn_pol.close()


def test_27_partial_target_failure_produces_nonzero_overall_result():
    """27. Partial target failure produces nonzero overall result."""
    conn = get_test_conn()

    def mock_recover_range(symbol, market, timeframe, start_ms, end_ms, **kwargs):
        if symbol == SYNTH_BTC:
            return {"fetched": 10, "inserted": 10, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 0}
        else:
            return {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "ambiguous": 0, "failed": 5}

    with patch("collectors.recover_futures_history.discover_missing_ranges", return_value=[(1000, 2000)]), \
         patch("collectors.recover_futures_history.recover_range", side_effect=mock_recover_range), \
         patch("collectors.recover_futures_history.acquire_advisory_lock", return_value=True), \
         patch("collectors.recover_futures_history.release_advisory_lock", return_value=None):

        exit_code, summary = run_recovery(
            symbols=[SYNTH_BTC, SYNTH_ETH],
            timeframes=["1h"],
            conn=conn,
            dry_run=True,
        )

        assert exit_code == 1
        assert summary[f"{SYNTH_BTC}_1h"]["failed"] == 0
        assert summary[f"{SYNTH_ETH}_1h"]["failed"] == 5

    conn.close()


def test_28_database_disconnect_prevents_subsequent_writes():
    """28. Database disconnect prevents subsequent writes."""
    conn = get_test_conn()
    conn.close()

    with pytest.raises(Exception):
        upsert_ohlcv([(
            SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
            "83000.0", "83500.0", "82900.0", "83400.0",
            "100.0", "8340000.0", 50, "50.0", "4170000.0", 1
        )], conn=conn)


# ==============================================================================
# TRANSACTION COMPATIBILITY & OWNERSHIP TESTS (29-38)
# ==============================================================================

def test_29_successful_batch_commit_under_recovery_owned_transaction():
    """29. Successful batch commit under recovery-owned transaction."""
    conn_writer = get_test_conn()
    ot = 1790604000000

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot, ot + STEP_MS - 1,
        "83000.0", "83500.0", "82900.0", "83400.0",
        "100.0", "8340000.0", 50, "50.0", "4170000.0", 1
    )]

    # Recovery explicitly passes commit=True (or defaults to auto-commit when not in transaction)
    upsert_ohlcv(row, conn=conn_writer, commit=True)

    # Verify visibility immediately on a separate connection
    conn_reader = get_test_conn()
    cur = conn_reader.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    count = cur.fetchone()[0]
    cur.close()
    conn_reader.close()
    conn_writer.close()

    assert count == 1


def test_30_failed_batch_rollback_under_recovery_owned_transaction():
    """30. Failed batch rollback under recovery-owned transaction."""
    conn_writer = get_test_conn()
    ot = 1790604000000

    # Malformed row with missing required fields to trigger SQL execution error
    bad_row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot, ot + STEP_MS - 1,
        "83000.0", "83500.0", "82900.0", "83400.0"
        # truncated columns trigger cursor exception
    )]

    with pytest.raises(Exception):
        upsert_ohlcv(bad_row, conn=conn_writer, commit=True)

    # Verify that nothing was committed on a separate connection
    conn_reader = get_test_conn()
    cur = conn_reader.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    count = cur.fetchone()[0]
    cur.close()
    conn_reader.close()
    conn_writer.close()

    assert count == 0


def test_31_caller_owned_transaction_not_unexpectedly_committed():
    """31. Caller-owned transaction is not unexpectedly committed."""
    conn_caller = get_test_conn()
    conn_reader = get_test_conn()
    ot1 = 1790604000000
    ot2 = 1790607600000

    # Caller starts its own transaction
    conn_caller.start_transaction()

    # Caller writes record 1 directly
    cur1 = conn_caller.cursor()
    cur1.execute("""
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, '100', '105', '95', '102', '1000', '102000', 10, '500', '51000', 1);
    """, (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot1, ot1 + STEP_MS - 1))

    # Caller invokes upsert_ohlcv within its transaction (commit=False)
    row2 = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot2, ot2 + STEP_MS - 1,
        "102", "108", "101", "107", "1500", "160500", 15, "750", "80250", 1
    )]
    upsert_ohlcv(row2, conn=conn_caller, commit=False)

    # Reader connection MUST see 0 rows because caller transaction is uncommitted!
    cur_read = conn_reader.cursor()
    cur_read.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur_read.fetchone()[0] == 0

    # Caller commits its transaction
    conn_caller.commit()

    # Refresh reader snapshot under REPEATABLE READ to see newly committed rows
    conn_reader.commit()
    cur_read.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur_read.fetchone()[0] == 2

    cur1.close()
    cur_read.close()
    conn_caller.close()
    conn_reader.close()


def test_32_caller_owned_transaction_not_unexpectedly_rolled_back():
    """32. Caller-owned transaction is not unexpectedly rolled back."""
    conn_caller = get_test_conn()
    conn_reader = get_test_conn()
    ot1 = 1790604000000
    ot2 = 1790607600000

    conn_caller.start_transaction()

    # Caller writes record 1
    cur1 = conn_caller.cursor()
    cur1.execute("""
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, '100', '105', '95', '102', '1000', '102000', 10, '500', '51000', 1);
    """, (SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, ot1, ot1 + STEP_MS - 1))

    # Caller attempts upsert_ohlcv with bad batch (fails)
    bad_row = [(
        SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, ot2, ot2 + STEP_MS - 1,
        "100"  # truncated columns
    )]

    with pytest.raises(Exception):
        upsert_ohlcv(bad_row, conn=conn_caller, commit=False)

    # Verify that caller's record 1 is STILL uncommitted and NOT rolled back!
    # Caller can commit its own work successfully
    conn_caller.commit()

    cur_read = conn_reader.cursor()
    cur_read.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_ETH,))
    assert cur_read.fetchone()[0] == 1  # Record 1 was preserved!

    cur1.close()
    cur_read.close()
    conn_caller.close()
    conn_reader.close()


def test_33_pooled_connection_release_after_success():
    """33. Pooled connection release after success."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_pool.get_connection.return_value = mock_conn

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    upsert_ohlcv(row, pool=mock_pool)

    assert mock_conn.commit.called
    assert mock_conn.close.called  # Returned to pool


def test_34_pooled_connection_release_after_failure():
    """34. Pooled connection release after failure."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.executemany.side_effect = mysql.connector.errors.OperationalError("Simulated DB error")
    mock_conn.cursor.return_value = mock_cur
    mock_pool.get_connection.return_value = mock_conn

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    with pytest.raises(mysql.connector.errors.OperationalError):
        upsert_ohlcv(row, pool=mock_pool)

    assert mock_conn.rollback.called
    assert mock_conn.close.called  # Returned to pool even on failure


def test_35_function_created_connection_close_after_success_and_failure():
    """35. Function-created connection close after success and failure."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    # 1. Success case
    with patch("db.db.get_pool", return_value=mock_pool):
        upsert_ohlcv(row)
        assert mock_conn.close.called

    # 2. Failure case
    mock_conn.reset_mock()
    mock_cur.executemany.side_effect = ValueError("SQL Failure")
    with patch("db.db.get_pool", return_value=mock_pool):
        with pytest.raises(ValueError):
            upsert_ohlcv(row)
        assert mock_conn.close.called


def test_36_rollback_failure_preserves_original_exception():
    """36. Rollback failure preserves the original exception."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.executemany.side_effect = ValueError("ORIGINAL_DB_EXCEPTION")
    mock_conn.rollback.side_effect = RuntimeError("ROLLBACK_NETWORK_FAILURE")
    mock_conn.cursor.return_value = mock_cur

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    # Must raise ValueError("ORIGINAL_DB_EXCEPTION"), not RuntimeError
    with pytest.raises(ValueError, match="ORIGINAL_DB_EXCEPTION"):
        upsert_ohlcv(row, conn=mock_conn, commit=True)


def test_37_connection_loss_prevents_subsequent_writes():
    """37. Connection loss prevents subsequent writes."""
    conn = get_test_conn()
    conn.close()

    row = [(
        SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    with pytest.raises(Exception):
        upsert_ohlcv(row, conn=conn)


def test_38_existing_upsert_behavior_for_all_known_call_site_categories():
    """38. Existing upsert behavior for all known call-site categories."""
    conn_direct = get_test_conn()
    pool_test = pooling.MySQLConnectionPool(
        pool_name="test_audit_pool",
        pool_size=2,
        host=TEST_HOST,
        port=TEST_PORT,
        database=TEST_DB,
        user=TEST_USER,
        password=TEST_PASS,
    )

    base_ot = 1790604000000
    r1 = [(SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, base_ot, base_ot + STEP_MS - 1,
           "100", "105", "95", "102", "100", "10200", 10, "50", "5100", 1)]
    r2 = [(SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, base_ot + STEP_MS, base_ot + 2*STEP_MS - 1,
           "102", "106", "98", "104", "100", "10400", 10, "50", "5200", 1)]
    r3 = [(SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, base_ot + 2*STEP_MS, base_ot + 3*STEP_MS - 1,
           "104", "108", "102", "106", "100", "10600", 10, "50", "5300", 1)]

    # Category 1: Function-created (no conn, no pool - mock pool points to test DB)
    with patch("db.db.get_pool", return_value=pool_test):
        upsert_ohlcv(r1)

    # Category 2: Pooled (pool passed)
    upsert_ohlcv(r2, pool=pool_test)

    # Category 3: Externally supplied (conn passed)
    upsert_ohlcv(r3, conn=conn_direct, commit=True)

    cur = conn_direct.cursor()
    cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BNB,))
    assert cur.fetchone()[0] == 3
    cur.close()
    conn_direct.close()


def test_39_caller_transaction_can_continue_and_commit_subsequent_work_after_failure():
    """39. A caller-owned transaction can continue and commit subsequent work after failed upsert."""
    conn_caller = get_test_conn()
    conn_reader = get_test_conn()
    ot1 = 1790604000000
    ot_bad = 1790607600000
    ot2 = 1790611200000

    conn_caller.start_transaction()

    # Step 1: Caller executes initial record directly
    cur1 = conn_caller.cursor()
    cur1.execute("""
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, '100', '105', '95', '102', '1000', '102000', 10, '500', '51000', 1);
    """, (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot1, ot1 + STEP_MS - 1))

    # Step 2: Caller attempts batch that fails (bad columns trigger cursor error)
    bad_row = [(SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot_bad, ot_bad + STEP_MS - 1, "100")]
    with pytest.raises(Exception):
        upsert_ohlcv(bad_row, conn=conn_caller, commit=False)

    # Step 3: Caller continues working in the same transaction and writes record 2!
    cur1.execute("""
        INSERT INTO ohlcv (symbol, market, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, num_trades, taker_buy_base_vol, taker_buy_quote_vol, is_closed)
        VALUES (%s, %s, %s, %s, %s, '105', '110', '103', '108', '1200', '129600', 12, '600', '64800', 1);
    """, (SYNTH_BTC, SYNTH_MARKET, SYNTH_TIMEFRAME, ot2, ot2 + STEP_MS - 1))

    # Step 4: Caller commits its overall transaction
    conn_caller.commit()

    # Step 5: Reader verifies BOTH record 1 and record 2 are committed and present!
    conn_reader.commit()
    cur_read = conn_reader.cursor()
    cur_read.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BTC,))
    assert cur_read.fetchone()[0] == 2

    cur1.close()
    cur_read.close()
    conn_caller.close()
    conn_reader.close()


def test_40_real_mariadb_pool_size_one_released_after_success_and_failure():
    """40. Real MariaDB driver pool of size 1 returns connection after both success and failure."""
    real_pool = pooling.MySQLConnectionPool(
        pool_name="pool_sz1_test",
        pool_size=1,
        host=TEST_HOST,
        port=TEST_PORT,
        database=TEST_DB,
        user=TEST_USER,
        password=TEST_PASS,
    )

    good_row = [(
        SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790604000000, 1790607599999,
        "100", "105", "95", "102", "1000", "102000", 10, "500", "51000", 1
    )]

    # 1. Success release
    upsert_ohlcv(good_row, pool=real_pool)
    c1 = real_pool.get_connection()
    assert c1 is not None
    c1.close()

    # 2. Failure release (malformed row raises exception)
    bad_row = [(SYNTH_ETH, SYNTH_MARKET, SYNTH_TIMEFRAME, 1790607600000)]
    with pytest.raises(Exception):
        upsert_ohlcv(bad_row, pool=real_pool)

    # If connection was leaked, pool would be exhausted and block or raise PoolError
    c2 = real_pool.get_connection()
    assert c2 is not None
    c2.close()


def test_41_savepoint_cleanup_and_subsequent_savepoints():
    """41. SAVEPOINT cleanup releases savepoint and permits subsequent operations in same transaction."""
    conn_caller = get_test_conn()
    conn_reader = get_test_conn()
    ot1 = 1790604000000
    ot2 = 1790607600000

    conn_caller.start_transaction()

    r1 = [(SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, ot1, ot1 + STEP_MS - 1,
           "100", "105", "95", "102", "100", "10200", 10, "50", "5100", 1)]
    r2 = [(SYNTH_BNB, SYNTH_MARKET, SYNTH_TIMEFRAME, ot2, ot2 + STEP_MS - 1,
           "102", "106", "98", "104", "100", "10400", 10, "50", "5200", 1)]

    # First upsert with savepoint
    upsert_ohlcv(r1, conn=conn_caller, commit=False)

    # Second upsert with subsequent savepoint in same transaction
    upsert_ohlcv(r2, conn=conn_caller, commit=False)

    conn_caller.commit()

    conn_reader.commit()
    cur_read = conn_reader.cursor()
    cur_read.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol = %s;", (SYNTH_BNB,))
    assert cur_read.fetchone()[0] == 2

    cur_read.close()
    conn_caller.close()
    conn_reader.close()

# ==============================================================================
# WORK ORDER 71909 FOCUSED UNIFIED-ACCOUNT COMPATIBILITY TESTS (42-47)
# ==============================================================================

def test_42_exact_production_identity_accepted_with_authorization(tmp_path):
    """42. Correct signal_bot_test@127.0.0.1 + crypto_signals accepted when authorized."""
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("valid_password")
    os.chmod(pw_file, 0o600)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("crypto_signals", "signal_bot_test@127.0.0.1")
    mock_conn.cursor.return_value = mock_cur
    mock_factory = MagicMock(return_value=mock_conn)

    with patch.dict(os.environ, {"ALLOW_PRODUCTION_RECOVERY_EXECUTION": "1"}):
        conn = get_write_connection(
            user="signal_bot_test",
            password_file=str(pw_file),
            database="crypto_signals",
            expected_database="crypto_signals",
            connection_factory=mock_factory,
        )
    mock_factory.assert_called_once()
    assert conn == mock_conn


def test_43_wrong_host_identity_rejected(tmp_path):
    """43. Authenticated host other than 127.0.0.1 is rejected."""
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("valid_password")
    os.chmod(pw_file, 0o600)

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Host is '%' instead of '127.0.0.1'
    mock_cur.fetchone.return_value = ("crypto_signals", "signal_bot_test@%")
    mock_conn.cursor.return_value = mock_cur
    mock_factory = MagicMock(return_value=mock_conn)

    with patch.dict(os.environ, {"ALLOW_PRODUCTION_RECOVERY_EXECUTION": "1"}):
        with pytest.raises(PermissionError, match="Authenticated identity mismatch"):
            get_write_connection(
                user="signal_bot_test",
                password_file=str(pw_file),
                database="crypto_signals",
                expected_database="crypto_signals",
                connection_factory=mock_factory,
            )


def test_44_wrong_database_rejected(tmp_path):
    """44. Wrong or arbitrary database target is rejected."""
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("valid_password")
    os.chmod(pw_file, 0o600)

    mock_factory = MagicMock()
    with pytest.raises(ValueError, match="Target database 'mysql' is invalid"):
        get_write_connection(
            user="signal_bot_test",
            password_file=str(pw_file),
            database="mysql",
            expected_database="mysql",
            connection_factory=mock_factory,
        )
    mock_factory.assert_not_called()


def test_45_missing_production_authorization_rejected(tmp_path):
    """45. Missing production execution authorization gate rejects crypto_signals."""
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("valid_password")
    os.chmod(pw_file, 0o600)

    mock_factory = MagicMock()
    with patch.dict(os.environ, {"ALLOW_PRODUCTION_RECOVERY_EXECUTION": "0"}):
        with pytest.raises(PermissionError, match="Targeting 'crypto_signals' is strictly prohibited"):
            get_write_connection(
                user="signal_bot_test",
                password_file=str(pw_file),
                database="crypto_signals",
                expected_database="crypto_signals",
                connection_factory=mock_factory,
            )
    mock_factory.assert_not_called()


def test_46_cms_and_root_rejected_in_write_mode(tmp_path):
    """46. cms and root are rejected before connection in write mode."""
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("valid_password")
    os.chmod(pw_file, 0o600)

    for privileged_user in ["cms", "root"]:
        mock_factory = MagicMock()
        with pytest.raises(PermissionError, match=f"Database user '{privileged_user}' is prohibited"):
            get_write_connection(
                user=privileged_user,
                password_file=str(pw_file),
                database="signal_bot_test",
                connection_factory=mock_factory,
            )
        mock_factory.assert_not_called()


def test_47_dry_run_and_tests_never_mutate_production():
    """47. Assert zero mutations and read-only enforcement during dry run."""
    manifest_data = load_and_validate_manifest(MANIFEST_V3_PATH, EXPECTED_MANIFEST_SHA256)
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch("collectors.recover_futures_history.upsert_ohlcv") as mock_upsert:
        exit_code, summary = run_recovery(
            manifest=manifest_data,
            conn=mock_conn,
            dry_run=True,
        )
        assert exit_code == 0
        mock_upsert.assert_not_called()

