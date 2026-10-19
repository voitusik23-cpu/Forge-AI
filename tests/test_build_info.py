"""Tests for the build/version status surface (``/api/version`` and ``/api/status.details``).

What is asserted, and why each assertion exists:

* the payload has exactly the six documented fields, so a field that could carry an
  environment value cannot be added unnoticed;
* the commit is a hexadecimal object name or the literal ``"unknown"``;
* a machine **without git**, a git that fails, a git that prints garbage, and a git that
  raises each degrade to ``"unknown"`` instead of failing the read;
* a missing interface bundle is ``"missing"`` rather than an error;
* nothing that looks like a credential appears anywhere in the payload;
* the route is declared, and the service method is what answers it.

Every test here runs offline: no network call is made and no provider key is read.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import unittest
from pathlib import Path

from app import build_info
from app.build_info import (
    BUILD_IDENTITY,
    FORGE_VERSION,
    UI_BUILD_MISSING,
    UNKNOWN,
    BuildIdentity,
    build_version_payload,
    resolve_build_identity,
    ui_build_stamp,
)

PAYLOAD_FIELDS = {
    "version",
    "commit",
    "commit_short",
    "built_at",
    "backend",
    "ui_build",
}

_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_COMMIT_HEX = re.compile(r"[0-9a-f]{4,64}\Z")

#: Shapes of a credential that must never reach a status payload.
_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
)


class _Completed:
    """The part of ``subprocess.CompletedProcess`` this module reads."""

    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def _runner_returning(returncode: int, stdout: str):
    def run(*_args, **_kwargs):  # noqa: ANN001 - signature mirrors subprocess.run
        return _Completed(returncode, stdout)

    return run


def _runner_without_git(*_args, **_kwargs):  # noqa: ANN001
    raise FileNotFoundError("git is not installed")


def _runner_that_hangs(*_args, **_kwargs):  # noqa: ANN001
    raise TimeoutError("git did not answer")


class BuildIdentityTests(unittest.TestCase):
    def test_01_the_payload_has_exactly_the_six_documented_fields(self) -> None:
        payload = build_version_payload()
        self.assertEqual(set(payload), PAYLOAD_FIELDS)

    def test_02_the_commit_is_a_hex_object_name_or_unknown(self) -> None:
        identity = BUILD_IDENTITY
        for value in (identity.commit, identity.commit_short):
            self.assertTrue(
                value == UNKNOWN or _COMMIT_HEX.match(value),
                f"{value!r} is neither a hexadecimal object name nor {UNKNOWN!r}",
            )
        if identity.commit != UNKNOWN:
            self.assertTrue(identity.commit.startswith(identity.commit_short))

    def test_03_the_version_is_the_one_spelling_of_the_application_version(self) -> None:
        self.assertEqual(build_version_payload()["version"], FORGE_VERSION)
        from app.api.models import HealthResponse

        self.assertEqual(
            HealthResponse(status="ok", core_status="connected").version, FORGE_VERSION
        )

    def test_04_built_at_is_an_iso_utc_timestamp(self) -> None:
        self.assertRegex(build_version_payload()["built_at"], _ISO_UTC)

    def test_05_the_ui_build_is_a_timestamp_or_the_literal_missing(self) -> None:
        value = build_version_payload()["ui_build"]
        self.assertTrue(value == UI_BUILD_MISSING or _ISO_UTC.match(value), value)

    def test_06_a_missing_interface_bundle_is_reported_as_missing(self) -> None:
        absent = Path("this-ui-dist-does-not-exist") / "index.html"
        self.assertEqual(ui_build_stamp(absent), UI_BUILD_MISSING)

    def test_07_an_existing_bundle_reports_its_own_modification_time(self) -> None:
        # This test file always exists, so it stands in for the bundle without
        # creating anything in the working tree.
        stamp = ui_build_stamp(Path(__file__))
        self.assertRegex(stamp, _ISO_UTC)

    def test_08_the_backend_word_is_whatever_the_caller_observed(self) -> None:
        self.assertEqual(build_version_payload(backend="connected")["backend"], "connected")
        self.assertEqual(
            build_version_payload(backend="disconnected")["backend"], "disconnected"
        )


class MissingGitDegradesTests(unittest.TestCase):
    """A status read must never fail because git cannot answer."""

    def test_01_no_git_binary_degrades_to_unknown(self) -> None:
        identity = resolve_build_identity(run=_runner_without_git)
        self.assertEqual(identity.commit, UNKNOWN)
        self.assertEqual(identity.commit_short, UNKNOWN)

    def test_02_a_failing_git_degrades_to_unknown(self) -> None:
        identity = resolve_build_identity(run=_runner_returning(128, ""))
        self.assertEqual((identity.commit, identity.commit_short), (UNKNOWN, UNKNOWN))

    def test_03_garbage_on_stdout_is_not_reported_as_a_commit(self) -> None:
        identity = resolve_build_identity(
            run=_runner_returning(0, "not a commit at all\n")
        )
        self.assertEqual((identity.commit, identity.commit_short), (UNKNOWN, UNKNOWN))

    def test_04_a_git_that_times_out_degrades_to_unknown(self) -> None:
        identity = resolve_build_identity(run=_runner_that_hangs)
        self.assertEqual((identity.commit, identity.commit_short), (UNKNOWN, UNKNOWN))

    def test_05_an_unusable_short_form_is_derived_from_the_full_name(self) -> None:
        full = "0123456789abcdef0123456789abcdef01234567"

        def run(arguments, **_kwargs):  # noqa: ANN001 - mirrors subprocess.run
            if tuple(arguments[1:]) == ("rev-parse", "HEAD"):
                return _Completed(0, full + "\n")
            return _Completed(1, "")

        identity = resolve_build_identity(run=run)
        self.assertEqual(identity.commit, full)
        self.assertEqual(identity.commit_short, full[:7])

    def test_06_an_explicit_identity_is_used_verbatim(self) -> None:
        identity = BuildIdentity(
            version="9.9.9",
            commit="a" * 40,
            commit_short="aaaaaaa",
            built_at="2026-01-02T03:04:05Z",
        )
        payload = build_version_payload(identity=identity)
        self.assertEqual(payload["version"], "9.9.9")
        self.assertEqual(payload["commit"], "a" * 40)
        self.assertEqual(payload["commit_short"], "aaaaaaa")
        self.assertEqual(payload["built_at"], "2026-01-02T03:04:05Z")


class NoSecretsTests(unittest.TestCase):
    def test_01_no_credential_shaped_value_appears_in_the_payload(self) -> None:
        payload = json.dumps(build_version_payload(), ensure_ascii=False)
        for shape in _SECRET_SHAPES:
            self.assertEqual(
                shape.findall(payload), [], f"{shape.pattern} matched the payload"
            )

    def test_02_no_environment_value_reaches_the_payload(self) -> None:
        payload = json.dumps(build_version_payload(), ensure_ascii=False)
        leaked = []
        for name, value in os.environ.items():
            if not isinstance(value, str) or len(value) < 8:
                continue
            if not any(
                token in name.upper()
                for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            ):
                continue
            if value in payload:
                leaked.append(name)
        self.assertEqual(leaked, [], f"environment values leaked: {leaked}")

    def test_03_the_field_names_cannot_smuggle_a_credential(self) -> None:
        for field in PAYLOAD_FIELDS:
            lowered = field.lower()
            for forbidden in ("key", "token", "secret", "password", "credential", "env"):
                self.assertNotIn(forbidden, lowered)


class ServiceAndRouteTests(unittest.TestCase):
    """The service method and the route are the surface the interface reads."""

    def setUp(self) -> None:
        from app.api.service import ForgeApiService

        self.service = ForgeApiService()

    def test_01_the_service_reports_the_same_six_fields(self) -> None:
        payload = self.service.get_version_info()
        self.assertEqual(set(payload), PAYLOAD_FIELDS)
        self.assertEqual(payload["version"], FORGE_VERSION)
        self.assertEqual(payload["backend"], "connected")
        self.assertRegex(payload["built_at"], _ISO_UTC)

    def test_02_the_status_details_carry_the_same_identity(self) -> None:
        class _NoProbeMonitor:
            """Answers "nothing observed" so this test opens no socket."""

            def check_all(self, force: bool = False):  # noqa: ANN001, ARG002
                return {}

            def cached_statuses(self):
                return {}

        self.service._health_monitor = _NoProbeMonitor()
        details = self.service.get_system_status().to_dict()["details"]
        self.assertEqual(details["version"], FORGE_VERSION)
        for field in ("commit", "commit_short", "built_at", "backend", "ui_build"):
            self.assertIn(field, details)
        # The pre-existing provider facts are still there: this is an extension.
        self.assertIn("enabled_providers", details)
        self.assertIn("default_provider", details)

    def test_03_the_route_is_declared_and_read_only(self) -> None:
        from app.api import server

        source = inspect.getsource(server)
        self.assertIn('@app.get("/api/version")', source)
        self.assertIn("get_version_info", source)

    def test_04_the_build_identity_is_resolved_once_at_import(self) -> None:
        # A module-level constant is what "read at import/startup" means: repeated
        # payload reads report the same commit for the life of the process.
        first = build_info.build_version_payload()
        second = build_info.build_version_payload()
        self.assertEqual(first["commit"], BUILD_IDENTITY.commit)
        self.assertEqual(second["built_at"], first["built_at"])


if __name__ == "__main__":
    unittest.main()
