import unittest
from fastapi.testclient import TestClient

from web.app import app
from web.auth import create_access_token


class TestMLWebRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = create_access_token(user_id=1, username="admin", role="admin")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_list_experiments_endpoint(self):
        res = self.client.get("/api/ml/experiments", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("experiments", data)
        self.assertIsInstance(data["experiments"], list)

    def test_list_models_endpoint(self):
        res = self.client.get("/api/ml/models", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("models", data)
        self.assertIsInstance(data["models"], list)

    def test_circuit_breakers_endpoint(self):
        res = self.client.get("/api/paper/circuit-breakers", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertIn("can_open_new_position", data)
        self.assertIn("max_concurrent_positions", data)


if __name__ == "__main__":
    unittest.main()
