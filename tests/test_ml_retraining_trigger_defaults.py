"""ML retraining trigger should not force expensive work by default."""
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks

from web.routes import ml_routes


class TestMLRetrainingTriggerDefaults(unittest.TestCase):
    def test_trigger_defaults_to_due_only_retraining(self):
        background_tasks = BackgroundTasks()
        with patch("ml.online_retrainer.execute_retraining_cycle") as execute:
            result = ml_routes.trigger_continuous_retraining(background_tasks)

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(background_tasks.tasks), 1)
        self.assertIs(background_tasks.tasks[0].func, execute)
        self.assertIs(background_tasks.tasks[0].kwargs["force"], False)

    def test_explicit_force_still_requests_forced_retraining(self):
        background_tasks = BackgroundTasks()
        with patch("ml.online_retrainer.execute_retraining_cycle") as execute:
            result = ml_routes.trigger_continuous_retraining(background_tasks, force=True)

        self.assertEqual(result["status"], "queued")
        self.assertIs(background_tasks.tasks[0].func, execute)
        self.assertIs(background_tasks.tasks[0].kwargs["force"], True)


if __name__ == "__main__":
    unittest.main()
