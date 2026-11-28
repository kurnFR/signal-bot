import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token, verify_access_token


class TestPhase2ApiContractsAndPurity(unittest.TestCase):
    def setUp(self):
        self._revocation_patcher = patch("web.auth.is_token_revoked", return_value=False)
        self._revocation_patcher.start()
        self._middleware_auth_patcher = patch(
            "web.app.authenticate_request", side_effect=self._authenticate_test_request
        )
        self._middleware_auth_patcher.start()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self._middleware_auth_patcher.stop)
        self.addCleanup(self._revocation_patcher.stop)
        self.admin_token = create_access_token(user_id=1, username="admin", role="admin")

    def _authenticate_test_request(self, request):
        """Verify signed test tokens without querying the live user-profile store."""
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        payload = verify_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Session expired or invalid token")
        return {
            "id": payload["sub"],
            "username": payload["username"],
            "role": payload["role"],
            "is_active": 1,
            "must_change_password": 0,
            "session_version": payload.get("session_version", 0),
        }

    # ------------------------------------------------------------------------
    # 1. GET Endpoints Must Not Mutate Schema or Commit Transactions
    # ------------------------------------------------------------------------
    def test_get_research_stats_does_not_execute_ddl_or_commit(self):
        # GET /retailbot2/strategies is deleted with migration 041 (its
        # rdt_strategy_lifecycle source table is dropped); the purity
        # contract now targets GET /retailbot2/stats, which reads
        # paper_trades through the shared pool.
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_cur.fetchone.return_value = {"open_count": 0}
        mock_conn.cursor.return_value = mock_cur

        with patch("db.db.get_pool") as mock_pool:
            mock_pool.return_value.get_connection.return_value = mock_conn

            res = self.client.get(
                "/api/research/retailbot2/stats",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(res.status_code, 200)

            # Assert no CREATE TABLE queries were executed on read
            executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
            for sql in executed_sqls:
                self.assertNotIn("CREATE TABLE", sql.upper())

            # Read-only query should not commit transactions
            mock_conn.commit.assert_not_called()

    # ------------------------------------------------------------------------
    # 2. GET ML Endpoints Must Not Create Directories
    # ------------------------------------------------------------------------
    def test_get_ml_experiments_and_models_do_not_mkdir(self):
        non_existent_dir = Path("data/definitely_non_existent_dir_for_test")

        with patch("web.routes.ml_routes.ML_RESULTS_DIR", non_existent_dir), \
             patch("web.routes.ml_routes.ML_MODELS_DIR", non_existent_dir):

            res1 = self.client.get(
                "/api/ml/experiments",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.json(), {"experiments": []})
            self.assertFalse(non_existent_dir.exists())

            res2 = self.client.get(
                "/api/ml/models",
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json(), {"models": []})
            self.assertFalse(non_existent_dir.exists())

    # ------------------------------------------------------------------------
    # 3. Master Data Allowlists
    # ------------------------------------------------------------------------
    def test_master_data_rejects_disallowed_market_and_timeframe(self):
        # Invalid market
        res = self.client.post(
            "/api/master-data/backfill",
            json={"symbol": "BTCUSDT", "market": "invalid_market", "timeframe": "1h"},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res.status_code, 422)

        # Invalid timeframe
        res2 = self.client.post(
            "/api/master-data/backfill",
            json={"symbol": "BTCUSDT", "market": "spot", "timeframe": "999years"},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res2.status_code, 422)

    # ------------------------------------------------------------------------
    # 4. Custom Strategy AI Prompt Length Limits
    # ------------------------------------------------------------------------
    def test_ai_strategy_generation_rejects_empty_or_oversized_prompts(self):
        # Too short (< 3 chars)
        res1 = self.client.post(
            "/api/custom-strategy/generate-ai",
            json={"prompt": "hi"},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res1.status_code, 422)

        # Too long (> 5000 chars)
        oversized = "a" * 5001
        res2 = self.client.post(
            "/api/custom-strategy/generate-ai",
            json={"prompt": oversized},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res2.status_code, 422)


if __name__ == "__main__":
    unittest.main()
