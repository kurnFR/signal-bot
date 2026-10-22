"""API tests for paper order history and cancellation authorization."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from paper.orders import OrderTransitionConflict, OrderValidationError
from web.app import app


def _user(role="trader"):
    return {
        "id": 42, "username": "test-user", "email": None, "role": role,
        "is_active": 1, "must_change_password": 0,
    }


class TestPaperOrderRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_list_orders_returns_service_rows_and_filter(self):
        with patch("web.app.authenticate_request", return_value=_user("viewer")), \
             patch("web.routes.paper_routes.list_orders", return_value=[{"id": 7, "status": "PENDING"}]) as list_orders:
            response = self.client.get("/api/paper/orders?status=PENDING&limit=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders"][0]["id"], 7)
        list_orders.assert_called_once_with(status="PENDING", limit=10)

    def test_invalid_order_state_returns_400(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.list_orders", side_effect=OrderValidationError("invalid order state")):
            response = self.client.get("/api/paper/orders?status=UNKNOWN")
        self.assertEqual(response.status_code, 400)

    def test_cancel_uses_authenticated_actor_id(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.cancel_pending_order", return_value=True) as cancel:
            response = self.client.post("/api/paper/orders/7/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "cancelled", "order_id": 7})
        cancel.assert_called_once_with(7, actor_user_id=42)

    def test_terminal_order_cancel_returns_conflict(self):
        with patch("web.app.authenticate_request", return_value=_user()), \
             patch("web.routes.paper_routes.cancel_pending_order", side_effect=OrderTransitionConflict("order is not pending")):
            response = self.client.post("/api/paper/orders/7/cancel")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
