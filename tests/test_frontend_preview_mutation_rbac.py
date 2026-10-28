"""Guard the explicit mutation boundary exposed by the React preview.

These checks exercise the same authorization policy used by the FastAPI
security middleware. They do not send requests or execute any mutation.
"""
import unittest

from web.app import _role_allowed


class TestFrontendPreviewMutationRBAC(unittest.TestCase):
    """Preview controls must not bypass server-side role enforcement."""

    def test_screener_controls_require_admin_or_trader(self):
        path = "/api/research/screener/control"
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                self.assertTrue(_role_allowed(method, path, "admin"))
                self.assertTrue(_role_allowed(method, path, "trader"))
                self.assertFalse(_role_allowed(method, path, "viewer"))

    def test_master_data_job_launch_requires_admin_or_trader(self):
        for path in (
            "/api/master-data/backfill",
            "/api/master-data/build-features",
        ):
            with self.subTest(path=path):
                self.assertTrue(_role_allowed("POST", path, "admin"))
                self.assertTrue(_role_allowed("POST", path, "trader"))
                self.assertFalse(_role_allowed("POST", path, "viewer"))

    def test_strategy_library_mutations_require_admin_or_trader(self):
        paths = (
            "/api/custom-strategy/quick-test",
            "/api/custom-strategy/create",
            "/api/custom-strategy/generate-ai",
            "/api/custom-strategy/example",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(_role_allowed("POST", path, "admin"))
                self.assertTrue(_role_allowed("POST", path, "trader"))
                self.assertFalse(_role_allowed("POST", path, "viewer"))

        delete_path = "/api/custom-strategy/example"
        self.assertTrue(_role_allowed("DELETE", delete_path, "admin"))
        self.assertTrue(_role_allowed("DELETE", delete_path, "trader"))
        self.assertFalse(_role_allowed("DELETE", delete_path, "viewer"))

    def test_backtest_run_requires_admin_or_trader(self):
        path = "/api/backtest/run"
        self.assertTrue(_role_allowed("POST", path, "admin"))
        self.assertTrue(_role_allowed("POST", path, "trader"))
        self.assertFalse(_role_allowed("POST", path, "viewer"))

    def test_unknown_mutation_fails_closed_for_every_role(self):
        path = "/api/new-unclassified-mutation"
        for role in ("admin", "trader", "viewer"):
            with self.subTest(role=role):
                self.assertFalse(_role_allowed("POST", path, role))
                self.assertFalse(_role_allowed("DELETE", path, role))

    def test_read_only_status_endpoint_remains_available_to_authenticated_roles(self):
        path = "/api/telegram/status"
        for role in ("admin", "trader", "viewer"):
            with self.subTest(role=role):
                self.assertTrue(_role_allowed("GET", path, role))


if __name__ == "__main__":
    unittest.main()
