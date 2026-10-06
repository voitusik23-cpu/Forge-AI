"""Security regression tests for credential redaction and the credential boundary.

Block 0 hardening: GitHub credential formats introduced or changed by GitHub must
be redacted, and a credential must not be able to reach an execution result or an
audit event merely because the caller named its key something innocent.

These tests exist to protect the invariant, not to benchmark regular expressions.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.identity import CommandIdentity
from app.execution.intent import AuthorizedExecution, IntentBuilder, _COORDINATOR_SENTINEL
from app.execution.profile import ProjectExecutionProfile
from app.execution.redaction import (
    REDACTED_PLACEHOLDER,
    DefaultSecretRedactor,
    looks_like_credential,
)
from app.execution.request import ExecutionRequest, ExecutionResult, ExecutionStatus
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace

# Realistic credential fixtures. No fixed length is assumed anywhere: the App
# installation token is deliberately ~520 characters because GitHub made
# installation tokens stateless on 2026-10-02.
LEGACY_PAT = "ghp_" + ("0123456789abcdefghij" * 2)                       # 44
FINE_GRAINED_PAT = (
    "github_pat_11ABCDEFG0aBcDeFgHiJkL_"
    "9zYxWvUtSrQpOnMlKjIhGfEdCbA1234567890abcdefgh"
)
SHORT_APP_TOKEN = "ghs_AbCdEf1234567890abcdef"
LONG_APP_TOKEN = "ghs_" + ("A1b2C3d4E5f6G7h8I9j0" * 26)                   # ~524
REFRESH_TOKEN = "ghr_AbCdEf1234567890abcdefgh"
OAUTH_TOKEN = "gho_AbCdEf1234567890abcdefgh"
USER_TOKEN = "ghu_AbCdEf1234567890abcdefgh"
CI_TOKEN = "ght_AbCdEf1234567890abcdefgh"
OPENAI_KEY = "sk-proj123456789012345678901234567890"

ALL_GITHUB_TOKENS = (
    LEGACY_PAT,
    FINE_GRAINED_PAT,
    SHORT_APP_TOKEN,
    LONG_APP_TOKEN,
    REFRESH_TOKEN,
    OAUTH_TOKEN,
    USER_TOKEN,
    CI_TOKEN,
)


class TestCredentialValueDetection(unittest.TestCase):
    """A credential is recognised by value shape, not only by key name."""

    def test_known_prefixes_are_detected(self) -> None:
        for token in ALL_GITHUB_TOKENS:
            with self.subTest(token=token[:16]):
                self.assertTrue(looks_like_credential(token))
        self.assertTrue(looks_like_credential(OPENAI_KEY))

    def test_ordinary_values_are_not_credentials(self) -> None:
        for value in (
            "gh",
            "ghost_writer",
            "github_pat",
            "production",
            "/usr/bin/python",
            "C:\\Program Files\\Python313\\python.exe",
            "8080",
            "",
            None,
            12345,
        ):
            with self.subTest(value=value):
                self.assertFalse(looks_like_credential(value))

    def test_prefix_without_credential_body_is_not_treated_as_credential(self) -> None:
        # Guard against treating an ordinary word that merely starts with a
        # credential prefix as a secret.
        self.assertFalse(looks_like_credential("ghs_short"))


class TestGitHubCredentialRedaction(unittest.TestCase):
    """Every supported GitHub credential format is redacted."""

    def setUp(self) -> None:
        self.redactor = DefaultSecretRedactor()

    def test_all_github_token_formats_are_redacted(self) -> None:
        for token in ALL_GITHUB_TOKENS:
            with self.subTest(token=token[:16]):
                cleaned = self.redactor.redact_text(f"value={token} done")
                self.assertNotIn(token, cleaned)
                self.assertIn(REDACTED_PLACEHOLDER, cleaned)

    def test_long_running_length_is_not_truncated(self) -> None:
        token = LONG_APP_TOKEN
        self.assertGreater(len(token), 500)
        cleaned = self.redactor.redact_text(f"token {token} end")
        self.assertEqual(cleaned, f"token {REDACTED_PLACEHOLDER} end")
        # Redaction removes the whole credential; it must not leave a fragment.
        self.assertNotIn(token[:60], cleaned)
        self.assertNotIn(token[-60:], cleaned)

    def test_over_long_credential_is_fully_removed_not_partially(self) -> None:
        # A bounded quantifier would redact only a prefix and leave a usable
        # fragment behind, so the boundary must not be applied to credential bodies.
        token = "ghs_" + ("A1b2" * 2000)
        cleaned = self.redactor.redact_text(f"x {token} y")
        self.assertNotIn("ghs_", cleaned)
        self.assertNotIn(token[:80], cleaned)
        self.assertNotIn(token[-80:], cleaned)

    def test_bearer_prefixed_tokens_are_redacted(self) -> None:
        for token in ALL_GITHUB_TOKENS:
            with self.subTest(token=token[:16]):
                cleaned = self.redactor.redact_text(f"Authorization: Bearer {token}")
                self.assertNotIn(token, cleaned)
                self.assertIn(REDACTED_PLACEHOLDER, cleaned)

    def test_legacy_and_prefix_only_patterns_still_work(self) -> None:
        # Regression guard: previously supported formats keep working.
        samples = {
            f"key {OPENAI_KEY} end": "key",
            "slack xoxb-1234567890-abcdefghij end": "slack",
        }
        for original, _ in samples.items():
            with self.subTest(original=original[:20]):
                self.assertIn(REDACTED_PLACEHOLDER, self.redactor.redact_text(original))


class TestAdversarialRedaction(unittest.TestCase):
    """Attempts to smuggle a credential past redaction."""

    def setUp(self) -> None:
        self.redactor = DefaultSecretRedactor()

    def test_token_inside_json_is_redacted(self) -> None:
        payload = json.dumps({"auth": {"token": LONG_APP_TOKEN}, "ok": True})
        cleaned = self.redactor.redact_text(payload)
        self.assertNotIn(LONG_APP_TOKEN, cleaned)
        self.assertIn(REDACTED_PLACEHOLDER, cleaned)
        # The document must remain parseable so redaction is not silent corruption.
        json.loads(cleaned)

    def test_token_inside_escaped_json_string_is_redacted(self) -> None:
        payload = json.dumps({"note": f"token={FINE_GRAINED_PAT}"})
        cleaned = self.redactor.redact_text(payload)
        self.assertNotIn(FINE_GRAINED_PAT, cleaned)

    def test_token_inside_multiline_log_is_redacted(self) -> None:
        log = (
            "[2026-10-04 10:00:00] starting\n"
            f"[2026-10-04 10:00:01] cloning with {LONG_APP_TOKEN}\n"
            "[2026-10-04 10:00:02] done\n"
        )
        cleaned = self.redactor.redact_text(log)
        self.assertNotIn(LONG_APP_TOKEN, cleaned)
        self.assertIn("starting", cleaned)
        self.assertIn("done", cleaned)
        self.assertEqual(len(cleaned.splitlines()), 3)

    def test_token_inside_exception_text_is_redacted(self) -> None:
        message = f"HTTPError: 401 for url: https://api.github.com with token {SHORT_APP_TOKEN}"
        cleaned = self.redactor.redact_text(message)
        self.assertNotIn(SHORT_APP_TOKEN, cleaned)

    def test_token_inside_event_metadata_is_redacted(self) -> None:
        metadata = {
            "output": f"pushed with {LONG_APP_TOKEN}",
            "nested": {"log_line": f"Bearer {FINE_GRAINED_PAT}"},
            "items": [f"x {LEGACY_PAT} y", {"deep": f"t={SHORT_APP_TOKEN}"}],
        }
        cleaned = self.redactor.redact_mapping(metadata)
        rendered = json.dumps(cleaned)
        for token in (LONG_APP_TOKEN, FINE_GRAINED_PAT, LEGACY_PAT, SHORT_APP_TOKEN):
            self.assertNotIn(token, rendered)

    def test_token_adjacent_to_punctuation_is_redacted(self) -> None:
        variants = (
            f"({LONG_APP_TOKEN})",
            f"[{LONG_APP_TOKEN}]",
            f'"{LONG_APP_TOKEN}"',
            f"'{LONG_APP_TOKEN}'",
            f"<{LONG_APP_TOKEN}>",
            f"{LONG_APP_TOKEN};",
            f"{LONG_APP_TOKEN},",
            f"token:{LONG_APP_TOKEN}",
            f"token={LONG_APP_TOKEN}.",
        )
        for variant in variants:
            with self.subTest(variant=variant[:12]):
                cleaned = self.redactor.redact_text(variant)
                self.assertNotIn(LONG_APP_TOKEN, cleaned)
                self.assertIn(REDACTED_PLACEHOLDER, cleaned)

    def test_multiple_and_repeated_tokens_are_all_redacted(self) -> None:
        text = (
            f"{LONG_APP_TOKEN} then {FINE_GRAINED_PAT} then {LONG_APP_TOKEN} again "
            f"and {LEGACY_PAT}"
        )
        cleaned = self.redactor.redact_text(text)
        for token in (LONG_APP_TOKEN, FINE_GRAINED_PAT, LEGACY_PAT):
            self.assertNotIn(token, cleaned)
        self.assertEqual(cleaned.count(REDACTED_PLACEHOLDER), 4)

    def test_token_in_url_and_windows_path_context_is_redacted(self) -> None:
        samples = (
            f"https://x-access-token:{LONG_APP_TOKEN}@github.com/org/repo.git",
            f"C:\\cache\\{SHORT_APP_TOKEN}\\file.txt",
            f"--header=Authorization: Bearer {SHORT_APP_TOKEN}",
        )
        for sample in samples:
            with self.subTest(sample=sample[:24]):
                self.assertNotIn(SHORT_APP_TOKEN if SHORT_APP_TOKEN in sample else LONG_APP_TOKEN,
                                 self.redactor.redact_text(sample))

    def test_bare_token_assignment_is_redacted_without_key_name_hint(self) -> None:
        for sample in (
            f"GH_PAT={LONG_APP_TOKEN}",
            f'GITHUB_TOKEN="{LONG_APP_TOKEN}"',
            f"access_token: {FINE_GRAINED_PAT}",
            f"auth_token={SHORT_APP_TOKEN}",
        ):
            with self.subTest(sample=sample[:20]):
                cleaned = self.redactor.redact_text(sample)
                self.assertNotIn(LONG_APP_TOKEN, cleaned)
                self.assertNotIn(FINE_GRAINED_PAT, cleaned)
                self.assertNotIn(SHORT_APP_TOKEN, cleaned)

    def test_credentials_in_command_arguments_are_redacted(self) -> None:
        redacted = self.redactor.redact_command(
            (
                "git",
                "clone",
                f"https://x-access-token:{LONG_APP_TOKEN}@github.com/org/repo.git",
                f"--config=extra={FINE_GRAINED_PAT}",
                "origin",
            )
        )
        self.assertNotIn(LONG_APP_TOKEN, " ".join(redacted))
        self.assertNotIn(FINE_GRAINED_PAT, " ".join(redacted))
        self.assertEqual(redacted[0], "git")
        self.assertEqual(redacted[-1], "origin")

    def test_unrelated_text_is_preserved(self) -> None:
        text = "gh is not a token; ghs_ is a prefix; github_pat is a word"
        self.assertEqual(self.redactor.redact_text(text), text)


class TestRedactedRequestIsEquivalent(unittest.TestCase):
    """Redaction must only remove secrets, never alter authorization-relevant state."""

    def test_redacted_request_preserves_declared_fields(self) -> None:
        redactor = DefaultSecretRedactor()
        request = ExecutionRequest(
            command=("deploy", f"--token={LONG_APP_TOKEN}"),
            working_directory="sub/dir",
            environment_variables={"GH_PAT": LONG_APP_TOKEN, "PORT": "8080"},
            timeout_seconds=12.5,
            artifact_targets=("out.txt",),
            metadata={"note": f"used {LONG_APP_TOKEN}"},
            approval_required=True,
        )
        safe = redactor.redact_request(request)

        self.assertTrue(safe.approval_required)
        self.assertEqual(safe.artifact_targets, ("out.txt",))
        self.assertEqual(safe.working_directory, "sub/dir")
        self.assertEqual(safe.timeout_seconds, 12.5)
        self.assertEqual(safe.request_id, request.request_id)
        self.assertEqual(safe.profile, request.profile)
        self.assertNotIn(LONG_APP_TOKEN, " ".join(safe.command))
        self.assertNotIn(LONG_APP_TOKEN, json.dumps(safe.metadata))


class TestExecutionCredentialBoundary(unittest.TestCase):
    """End-to-end: a credential reaches the child process but never the artefacts."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self._run_seq = 0

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _execute_with_env(self, key: str, value: str):
        """Run a real subprocess that echoes the variable, then inspect artefacts."""
        program = f"import os;print('OUT=' + os.environ.get({key!r}, 'missing'))"
        profile = ProjectExecutionProfile("p", allowed_commands=(CommandIdentity(sys.executable, ("-c", program)),))
        request = ExecutionRequest(
            (sys.executable, "-c", program),
            profile=profile,
            environment_variables={key: value},
        )
        events: list[tuple[str, dict]] = []
        coordinator = ExecutionCoordinator(
            adapter=LocalExecutionAdapter(workspace_root=self.workspace, isolate_workspace=False)
        )
        # This run declares dispatch authority (allowed_commands), so it needs an
        # explicit run id and its single frozen RunScope. The perimeter carries
        # only the executable the profile already permits; no authority is added.
        # The command text differs per call, so each call is a different
        # perimeter and therefore a different run id.
        self._run_seq += 1
        run_id = f"credential-boundary-run-{self._run_seq}"
        scope = RunScope(
            run_id=run_id,
            workspace=Workspace(self.workspace),
            execution_profile=profile,
            allowed_execution_commands=frozenset({sys.executable}),
            acceptance_criteria=(
                AcceptanceCriterion(
                    criterion_id="credential-boundary-anchor",
                    description="credential redaction boundary",
                ),
            ),
        )
        scope.freeze()
        result = coordinator.execute(
            request,
            workspace_root=self.workspace,
            allowed_commands=frozenset({sys.executable}),
            observer=lambda event_type, data: events.append((str(event_type), data)),
            run_id=run_id,
            run_scope=scope,
        )
        return result, events

    def test_credential_under_innocent_key_is_redacted_from_result(self) -> None:
        result, events = self._execute_with_env("GH_PAT", LONG_APP_TOKEN)

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertNotIn(LONG_APP_TOKEN, result.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, result.stdout)
        self.assertNotIn(LONG_APP_TOKEN, result.stderr)
        self.assertNotIn(LONG_APP_TOKEN, json.dumps(result.metadata))

        rendered_events = repr(events)
        self.assertNotIn(LONG_APP_TOKEN, rendered_events)
        for _, data in events:
            self.assertNotIn("stdout", data)
            self.assertNotIn("stderr", data)

    def test_credential_under_sensitive_key_is_redacted_from_result(self) -> None:
        result, events = self._execute_with_env("GITHUB_TOKEN", LONG_APP_TOKEN)

        self.assertNotIn(LONG_APP_TOKEN, result.stdout)
        self.assertNotIn(LONG_APP_TOKEN, repr(events))

    def test_credential_under_innocent_key_does_not_change_delivery(self) -> None:
        """Redaction must not silently drop the variable from the child environment."""
        result, _ = self._execute_with_env("GH_PAT", LONG_APP_TOKEN)
        # The child saw either the real value or the redaction placeholder; it must
        # not have seen nothing, which would mean the variable was dropped.
        self.assertIn("OUT=", result.stdout)
        self.assertNotIn("OUT=missing", result.stdout)

    def test_safe_environment_values_survive_redaction(self) -> None:
        result, _ = self._execute_with_env("BUILD_LABEL", "release-candidate-7")
        self.assertIn("release-candidate-7", result.stdout)


if __name__ == "__main__":
    unittest.main()
