"""Policy models and validation for Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentHarnessPolicy:
    """Enforces conservative bounds on agent runtime iterations and actions."""

    max_iterations: int = 10
    max_actions: int = 10
    max_execution_attempts: int = 3
    max_revision_attempts: int = 2
    allow_parallel_actions: bool = False
    fail_on_unknown_decision: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.max_iterations, int) or self.max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        if not isinstance(self.max_actions, int) or self.max_actions <= 0:
            raise ValueError("max_actions must be a positive integer")
        if not isinstance(self.max_execution_attempts, int) or self.max_execution_attempts < 0:
            raise ValueError("max_execution_attempts must be a non-negative integer")
        if not isinstance(self.max_revision_attempts, int) or self.max_revision_attempts < 0:
            raise ValueError("max_revision_attempts must be a non-negative integer")
        if not isinstance(self.allow_parallel_actions, bool):
            raise ValueError("allow_parallel_actions must be a boolean")
        if not isinstance(self.fail_on_unknown_decision, bool):
            raise ValueError("fail_on_unknown_decision must be a boolean")
