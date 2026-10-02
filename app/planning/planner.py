"""Small deterministic templates for common project goals."""

import hashlib
import re
from typing import List, Sequence, Tuple

from app.orchestrator.models import TaskCategory
from app.planning.models import (
    PlannedTask,
    PlannedTaskStatus,
    ProjectPlan,
)


_TaskTemplate = Tuple[str, str, TaskCategory]


class Planner:
    """Create a provider-neutral project plan from a plain-text goal."""

    def create_plan(self, goal: str) -> ProjectPlan:
        """Select a deterministic template and return its dependency sequence."""
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")

        clean_goal = goal.strip()
        normalized = clean_goal.casefold()
        project_type, templates = self._template_for(normalized)
        goal_key = " ".join(normalized.split())
        digest = hashlib.sha256(goal_key.encode("utf-8")).hexdigest()[:12]
        tasks = self._build_tasks(templates)
        return ProjectPlan(
            plan_id=f"plan-{digest}",
            title=f"{project_type} project plan",
            goal=clean_goal,
            tasks=tasks,
        )

    @classmethod
    def _template_for(cls, goal: str) -> Tuple[str, Sequence[_TaskTemplate]]:
        if re.search(r"\btelegram\b", goal):
            return "Telegram bot", cls._telegram_template()
        if re.search(r"\bweb(?:\s+application|\s+app|site)?\b|\bwebsite\b", goal):
            return "Web application", cls._web_template(goal)
        if re.search(
            r"\b(analy[sz]e|analysis|dataset|data set|sales data|data analysis)\b",
            goal,
        ):
            return "Data analysis", cls._data_template()
        return "Generic software", cls._generic_template()

    @staticmethod
    def _generic_template() -> Sequence[_TaskTemplate]:
        return (
            ("Requirements", "Clarify scope, users, constraints, and acceptance criteria.", TaskCategory.ANALYSIS),
            ("Architecture", "Define the system structure and major interfaces.", TaskCategory.ANALYSIS),
            ("Implementation", "Implement the project according to the agreed requirements and architecture.", TaskCategory.CODE),
            ("Testing", "Add and run tests for the implemented behavior.", TaskCategory.CODE),
            ("Review", "Review the implementation and address correctness or completeness issues.", TaskCategory.REVIEW),
            ("Documentation", "Document setup, usage, and important project decisions.", TaskCategory.OTHER),
        )

    @staticmethod
    def _telegram_template() -> Sequence[_TaskTemplate]:
        return (
            ("Architecture", "Define the bot components, user flows, and integration boundaries.", TaskCategory.ANALYSIS),
            ("Data and storage layer", "Design and implement persistence for the bot's project data.", TaskCategory.CODE),
            ("Core business logic", "Implement the main workflows and validation rules.", TaskCategory.CODE),
            ("Telegram bot interface", "Implement Telegram commands, messages, and interaction flows.", TaskCategory.CODE),
            ("Admin functionality", "Implement appropriate administrative workflows and access controls.", TaskCategory.CODE),
            ("Notifications and integrations", "Implement required notifications and external integrations.", TaskCategory.CODE),
            ("Testing", "Test the main workflows, failure cases, and integrations.", TaskCategory.CODE),
            ("Documentation", "Document configuration, operation, and user-facing workflows.", TaskCategory.OTHER),
        )

    @staticmethod
    def _web_template(goal: str) -> Sequence[_TaskTemplate]:
        tasks: List[_TaskTemplate] = [
            ("Architecture", "Define application components, boundaries, and interfaces.", TaskCategory.ANALYSIS),
            ("Backend", "Implement backend services and application rules.", TaskCategory.CODE),
            ("Data and storage", "Design and implement the data model and persistence layer.", TaskCategory.CODE),
            ("Frontend", "Implement the web interface and user workflows.", TaskCategory.CODE),
            ("API and integrations", "Implement the API contracts and required integrations.", TaskCategory.CODE),
        ]
        if re.search(r"\b(auth|authentication|authorization|login|accounts?|users?|admin|secure)\b", goal):
            tasks.append(
                ("Authentication and authorization", "Implement identity, sign-in, and access controls appropriate to the application.", TaskCategory.CODE)
            )
        tasks.extend(
            (
                ("Testing", "Test application flows, API behavior, and important failure cases.", TaskCategory.CODE),
                ("Documentation", "Document setup, configuration, API usage, and user workflows.", TaskCategory.OTHER),
            )
        )
        return tasks

    @staticmethod
    def _data_template() -> Sequence[_TaskTemplate]:
        return (
            ("Data inspection", "Inspect the dataset structure, fields, quality, and coverage.", TaskCategory.ANALYSIS),
            ("Data cleaning", "Prepare the data and document handling of missing or inconsistent values.", TaskCategory.CODE),
            ("Analysis", "Analyze the data to answer the stated goal using suitable methods.", TaskCategory.ANALYSIS),
            ("Findings", "Summarize the results and distinguish observations from assumptions.", TaskCategory.ANALYSIS),
            ("Validation", "Validate calculations, assumptions, and conclusions against the source data.", TaskCategory.REVIEW),
            ("Final report", "Present methods, findings, limitations, and conclusions in a concise report.", TaskCategory.OTHER),
        )

    @staticmethod
    def _build_tasks(templates: Sequence[_TaskTemplate]) -> List[PlannedTask]:
        tasks = []
        for index, (title, description, category) in enumerate(templates, start=1):
            slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
            dependencies = [tasks[-1].task_id] if tasks else []
            tasks.append(
                PlannedTask(
                    task_id=f"task-{index:02d}-{slug}",
                    title=title,
                    description=description,
                    category=category,
                    dependencies=dependencies,
                    status=(
                        PlannedTaskStatus.READY
                        if not dependencies
                        else PlannedTaskStatus.PENDING
                    ),
                )
            )
        return tasks
