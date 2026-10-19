"""The discovery smoke command runs offline and prints every section.

The command is ``python -m app.smoke_discovery``. These tests execute it twice in
process -- once through ``main()`` and once through ``runpy`` with
``__name__ == "__main__"`` -- with stdout captured, so "the module runs and prints
every section" is asserted rather than assumed.

Network access is made impossible for the duration: connecting, resolving, or
opening a socket raises immediately, which is what "no network" means here.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import re
import runpy
import shutil
import socket
import sys
import unittest

from app import smoke_discovery
from app.discovery.store import DISCOVERY_ROOT_ENV_VAR


class _NoNetwork:
    """Fail loudly if anything in the smoke tries to reach the network."""

    def __enter__(self) -> "_NoNetwork":
        self._original = {
            "connect": socket.socket.connect,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
        }

        def connect(sock_self, address):  # noqa: ANN001 - mirror the real signature
            raise AssertionError(f"smoke tried to connect to {address!r}")

        def create_connection(address, *args, **kwargs):  # noqa: ANN001
            raise AssertionError(f"smoke tried to connect to {address!r}")

        def getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
            raise AssertionError(f"smoke tried to resolve {host!r}")

        socket.socket.connect = connect  # type: ignore[assignment]
        socket.create_connection = create_connection  # type: ignore[assignment]
        socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info) -> None:  # noqa: ANN002
        socket.socket.connect = self._original["connect"]  # type: ignore[assignment]
        socket.create_connection = self._original["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = self._original["getaddrinfo"]  # type: ignore[assignment]


class SmokeDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-discovery-smoke"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        # The smoke uses the deployment's own store; the test points it here so a
        # captured run writes nothing into the repository.
        previous = os.environ.get(DISCOVERY_ROOT_ENV_VAR)
        os.environ[DISCOVERY_ROOT_ENV_VAR] = str(base / "discovery")
        self.addCleanup(self._restore_env, previous)

    @staticmethod
    def _restore_env(previous) -> None:  # noqa: ANN001
        if previous is None:
            os.environ.pop(DISCOVERY_ROOT_ENV_VAR, None)
        else:
            os.environ[DISCOVERY_ROOT_ENV_VAR] = previous

    def test_01_every_section_is_printed_and_the_command_succeeds(self) -> None:
        buffer = io.StringIO()
        with _NoNetwork():
            with contextlib.redirect_stdout(buffer):
                code = smoke_discovery.main()
        output = buffer.getvalue()

        self.assertEqual(code, 0)
        for section in (
            "INITIAL GOAL",
            "QUESTIONS",
            "ANSWERS",
            "PROJECT SPECIFICATION",
            "PLAN",
            "STATUS",
        ):
            self.assertIn(section, output)
        # The sections carry real content, not just headings.
        self.assertIn(smoke_discovery.FIXTURE_GOAL, output)
        self.assertIn("q-users", output)
        self.assertIn("Project type: система бронирования", output)
        self.assertIn("plan id:", output)
        self.assertIn("first step dispatched through the existing pipeline: mock", output)
        self.assertIn("ok", output)
        # Nothing that looks like a credential appears in the output. The pattern is
        # the key *shape*, not the bare prefix: "task-04-testing" is not a secret.
        self.assertIsNone(
            re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}", output)
        )
        self.assertIsNone(re.search(r"\beyJ[A-Za-z0-9_-]{8,}\.", output))

    def test_02_the_module_runs_as_main(self) -> None:
        # ``runpy`` warns when the module is already imported, which it is: the
        # previous test drove ``main()`` directly. Dropping the cached module makes
        # this a fresh execution of the entry point, exactly like ``python -m``.
        cached = sys.modules.pop("app.smoke_discovery", None)
        if cached is not None:
            self.addCleanup(
                lambda: sys.modules.__setitem__("app.smoke_discovery", cached)
            )
        buffer = io.StringIO()
        with _NoNetwork():
            with contextlib.redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_module("app.smoke_discovery", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("PROJECT SPECIFICATION", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
