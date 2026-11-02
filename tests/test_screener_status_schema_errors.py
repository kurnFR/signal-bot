"""Screener status must not mask migration/schema failures as an enabled worker."""
import unittest
from unittest.mock import patch

from tests.test_research_routes import TestResearchRoutes


class TestScreenerStatusSchemaErrors(TestResearchRoutes):
    @patch("web.routes.research_routes.get_screener_control")
    @patch("web.routes.research_routes.get_screener_status")
    def test_status_returns_500_when_control_schema_is_unavailable(self, mock_status, mock_control):
        mock_control.side_effect = RuntimeError("screener_control table missing")
        mock_status.return_value = {"running": False, "database_ok": True}

        response = self.client.get("/api/research/screener/status", headers=self.headers)

        self.assertEqual(response.status_code, 500)
        mock_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
