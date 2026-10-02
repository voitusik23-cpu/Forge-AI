"""Print deterministic Planner v0.1 examples without provider calls."""

from app.planning import Planner


_EXAMPLES = (
    ("Telegram booking project", "Build me a Telegram booking system for my hotel"),
    ("Web application project", "Build a web application for managing user bookings"),
    ("Data/analysis project", "Analyze this sales dataset"),
)


def main() -> int:
    planner = Planner()
    for project, goal in _EXAMPLES:
        plan = planner.create_plan(goal)
        print(f"PROJECT: {project}")
        print(f"PLAN ID: {plan.plan_id}")
        print("TASKS:")
        for task in plan.tasks:
            dependencies = ", ".join(task.dependencies) or "none"
            print(f"  - {task.title}")
            print(f"    CATEGORY: {task.category.value}")
            print(f"    DEPENDENCIES: {dependencies}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
