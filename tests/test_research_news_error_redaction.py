"""Research and News AI route errors must not expose raw exception details."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from web.routes import news_routes, research_routes


class TestResearchNewsErrorRedaction(unittest.TestCase):
    def test_retailbot_positions_error_is_generic(self):
        secret = "database-password=secret-token"
        with patch.object(research_routes, "get_retailbot2_open_trades", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.research_routes", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                research_routes.retailbot2_positions()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Failed to load retailbot2 positions")
        self.assertNotIn("secret-token", "\n".join(captured.output))

    def test_news_stats_error_is_generic(self):
        secret = "database-password=secret-token"
        with patch.object(news_routes, "get_news_overlay_stats", side_effect=RuntimeError(secret)), \
             self.assertLogs("web.news_routes", level="ERROR") as captured:
            with self.assertRaises(HTTPException) as raised:
                news_routes.news_stats()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Failed to load News AI stats")
        self.assertNotIn("secret-token", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
