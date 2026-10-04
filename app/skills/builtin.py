"""Built-in skills bundled with Forge AI core."""

from __future__ import annotations

import hashlib

from app.skills.models import (
    SkillDefinition,
    SkillManifest,
    SkillProvenance,
    SkillTrustLevel,
)

_PYTHON_TEST_RUNNER_INSTRUCTIONS = """## Procedure for Python Test Verification

1. Inspect the task requirements and verification expectations to identify the target test suite.
2. If tests have not yet been executed in the current run attempt, recommend action EXECUTE for tool python_test_runner.
3. Upon receiving test execution observations:
   - If test execution failed (non-zero exit status, test failure, or assertion error), recommend action REQUEST_REVISION with reason tests_failed.
   - If test execution succeeded with all tests passing, recommend action RUN_VERIFICATION to confirm workspace acceptance.
4. Do not recommend premature run completion before verification has confirmed passing acceptance criteria.
"""

_PYTHON_TEST_RUNNER_HASH = hashlib.sha256(
    _PYTHON_TEST_RUNNER_INSTRUCTIONS.strip().encode("utf-8")
).hexdigest()


def get_builtin_python_test_runner() -> SkillDefinition:
    """Return the standard built-in Python test runner procedural skill."""
    manifest = SkillManifest(
        skill_id="forge.builtin.python_test_runner",
        name="Python Test Runner",
        version="0.1.0",
        description="Procedural guidance for executing and verifying Python test suites",
        required_capabilities=("process:execute",),
        requested_tools=("python_test_runner",),
        trust_level=SkillTrustLevel.BUILTIN,
        provenance=SkillProvenance(
            origin="forge.builtin",
            author="Forge Core Team",
            content_hash=_PYTHON_TEST_RUNNER_HASH,
        ),
        metadata={"target_languages": ["python"]},
    )
    return SkillDefinition(
        manifest=manifest,
        instructions=_PYTHON_TEST_RUNNER_INSTRUCTIONS.strip(),
    )
