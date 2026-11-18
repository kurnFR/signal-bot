"""
Tests for Section 4: P2 — Resolve Integration Test Setup Errors Safely.
Verifies:
1. When isolated test database is unavailable/unreachable, setUpClass converts failure into unittest.SkipTest.
2. When connection succeeds but identifies the wrong database (e.g. crypto_signals), fails immediately with AssertionError.
3. When database exists but required tables are missing, reports clear setup failure (RuntimeError) instead of hiding behind a skip.
"""
import unittest
from unittest.mock import MagicMock, patch

import pytest
from tests.test_paper_position_lifecycle import TestPaperPositionLifecycle as _TestPaperPositionLifecycle
from tests.test_user_management_mariadb_concurrency import TestMariaDBUserManagementConcurrency as _TestMariaDBUserManagementConcurrency

_TestPaperPositionLifecycle.__test__ = False
_TestMariaDBUserManagementConcurrency.__test__ = False


def test_paper_lifecycle_skip_when_database_unavailable():
    """Verify SkipTest is raised when signal_bot_test database connection fails."""
    with patch("db.db.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_pool.get_connection.side_effect = Exception("Can't connect to MySQL server on '127.0.0.1'")
        mock_get_pool.return_value = mock_pool

        with pytest.raises(unittest.SkipTest, match="unavailable or unreachable"):
            _TestPaperPositionLifecycle.setUpClass()


def test_paper_lifecycle_fails_immediately_on_database_identity_mismatch():
    """Verify AssertionError is raised immediately when connected to wrong database."""
    with patch("db.db.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("crypto_signals", "cms@127.0.0.1")
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        with pytest.raises(AssertionError, match="strictly expected 'signal_bot_test'"):
            _TestPaperPositionLifecycle.setUpClass()


def test_paper_lifecycle_fails_immediately_on_missing_schema_tables():
    """Verify RuntimeError is raised immediately when required tables are missing."""
    with patch("db.db.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        # Returns signal_bot_test for DB, but missing paper_positions table
        mock_cur.fetchone.return_value = ("signal_bot_test", "signal_bot_test@127.0.0.1")
        mock_cur.fetchall.return_value = [("paper_trades",), ("paper_orders",)]
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        with pytest.raises(RuntimeError, match="Required schema tables missing"):
            _TestPaperPositionLifecycle.setUpClass()


def test_user_management_concurrency_skip_when_database_unavailable():
    """Verify SkipTest is raised when signal_bot_test is unreachable for user management concurrency suite."""
    with patch("db.db.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_pool.get_connection.side_effect = Exception("Connection refused")
        mock_get_pool.return_value = mock_pool

        with pytest.raises(unittest.SkipTest, match="unavailable or unreachable"):
            _TestMariaDBUserManagementConcurrency.setUpClass()


def test_user_management_concurrency_fails_on_database_identity_mismatch():
    """Verify AssertionError is raised immediately on database identity mismatch."""
    with patch("db.db.get_pool") as mock_get_pool, patch("db.db.get_retailbot2_pool") as mock_get_rb_pool:
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("crypto_signals",)
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn
        mock_get_pool.return_value = mock_pool
        mock_get_rb_pool.return_value = mock_pool

        with pytest.raises(AssertionError, match="strictly expected 'signal_bot_test'"):
            _TestMariaDBUserManagementConcurrency.setUpClass()


def test_user_management_concurrency_fails_on_missing_schema_tables():
    """Verify RuntimeError is raised immediately on missing schema tables."""
    with patch("db.db.get_pool") as mock_get_pool, patch("db.db.get_retailbot2_pool") as mock_get_rb_pool:
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("signal_bot_test",)
        # Empty tables list
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        mock_pool.get_connection.return_value = mock_conn
        mock_get_pool.return_value = mock_pool
        mock_get_rb_pool.return_value = mock_pool

        with pytest.raises(RuntimeError, match="Required schema tables missing"):
            _TestMariaDBUserManagementConcurrency.setUpClass()
