"""Offline tests for deterministic project planning templates."""

import unittest

from app.orchestrator.models import TaskCategory
from app.planning import PlannedTaskStatus, Planner, ProjectPlanStatus


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def test_generic_software_plan_has_required_tasks_and_dependencies(self):
        plan = self.planner.create_plan("Build a command line inventory tool")

        self.assertIn("Generic software", plan.title)
        self.assertEqual(
            [task.title for task in plan.tasks],
            ["Requirements", "Architecture", "Implementation", "Testing", "Review", "Documentation"],
        )
        self.assertEqual(plan.tasks[0].status, PlannedTaskStatus.READY)
        self.assertEqual(plan.tasks[1].dependencies, [plan.tasks[0].task_id])
        self.assertEqual(plan.tasks[1].status, PlannedTaskStatus.PENDING)
        self.assertEqual(plan.status, ProjectPlanStatus.PLANNED)

    def test_telegram_goal_uses_generic_bot_workflow(self):
        plan = self.planner.create_plan("Build me a Telegram booking system for my hotel")
        titles = [task.title for task in plan.tasks]

        self.assertIn("Data and storage layer", titles)
        self.assertIn("Core business logic", titles)
        self.assertIn("Telegram bot interface", titles)
        self.assertIn("Admin functionality", titles)
        self.assertIn("Notifications and integrations", titles)
        self.assertIn("Testing", titles)
        self.assertIn("Documentation", titles)
        self.assertNotIn("hotel", " ".join(task.description for task in plan.tasks).lower())

    def test_web_application_includes_authentication_when_goal_mentions_users(self):
        plan = self.planner.create_plan("Build a web application for managing user bookings")
        titles = [task.title for task in plan.tasks]

        self.assertIn("Backend", titles)
        self.assertIn("Data and storage", titles)
        self.assertIn("Frontend", titles)
        self.assertIn("API and integrations", titles)
        self.assertIn("Authentication and authorization", titles)

    def test_web_application_omits_authentication_when_not_indicated(self):
        plan = self.planner.create_plan("Build a web application for public event listings")

        self.assertNotIn(
            "Authentication and authorization", [task.title for task in plan.tasks]
        )

    def test_data_analysis_template_has_expected_stages(self):
        plan = self.planner.create_plan("Analyze this sales dataset")

        self.assertEqual(
            [task.title for task in plan.tasks],
            ["Data inspection", "Data cleaning", "Analysis", "Findings", "Validation", "Final report"],
        )
        self.assertEqual(plan.tasks[0].category, TaskCategory.ANALYSIS)
        self.assertEqual(plan.tasks[-2].category, TaskCategory.REVIEW)

    def test_plan_is_deterministic_and_dependencies_reference_prior_tasks(self):
        goal = "Build a Telegram booking system"
        first = self.planner.create_plan(goal)
        second = self.planner.create_plan(goal)

        self.assertEqual(first, second)
        ids = {task.task_id for task in first.tasks}
        for position, task in enumerate(first.tasks):
            for dependency in task.dependencies:
                self.assertIn(dependency, ids)
                dependency_position = next(
                    i for i, item in enumerate(first.tasks) if item.task_id == dependency
                )
                self.assertLess(dependency_position, position)

    def test_empty_goal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.planner.create_plan("  ")


if __name__ == "__main__":
    unittest.main()
