"""Regression tests for the Core API's exposure configuration.

The audit found ``allow_origins=["*"]`` together with ``allow_credentials=True`` on a
surface where no route requires authentication. That combination lets any page that can
reach the port drive the API in the operator's browser context, and it was never caught
because nothing in the repository actually launched the server -- there was no point at
which a bind address had to be chosen.

These tests pin the three decisions that replace it: an explicit origin allow-list, no
credentialed CORS, and loopback-by-default with a loud warning when asked to bind wider.
They also assert the residual risk explicitly, so a future reader cannot mistake the
absence of a 401 for an oversight that was never noticed.
"""

import unittest
import warnings

from app.api import main as api_main
from app.api.server import (
    CORS_ORIGINS_ENV_VAR,
    DEFAULT_API_HOST,
    DEFAULT_CORS_ORIGINS,
    create_api_app,
    is_loopback_host,
    resolve_cors_origins,
    warn_if_exposed,
)


class CorsOriginTests(unittest.TestCase):
    def test_01_default_origins_are_local_and_explicit(self):
        origins = resolve_cors_origins(environ={})
        self.assertEqual(origins, DEFAULT_CORS_ORIGINS)
        self.assertNotIn("*", origins)
        for origin in origins:
            self.assertTrue(
                origin.startswith(("http://127.0.0.1", "http://localhost")), origin
            )

    def test_02_a_wildcard_is_refused_not_narrowed(self):
        """Silently narrowing would leave the operator believing the surface is open."""

        with self.assertRaises(ValueError) as caught:
            resolve_cors_origins("https://app.example.test,*")
        self.assertIn("wildcard", str(caught.exception))

    def test_03_an_explicit_list_is_honoured(self):
        origins = resolve_cors_origins(
            " https://a.example.test , https://b.example.test "
        )
        self.assertEqual(
            origins, ("https://a.example.test", "https://b.example.test")
        )

    def test_04_blank_setting_falls_back_to_the_local_default(self):
        self.assertEqual(resolve_cors_origins("   ", environ={}), DEFAULT_CORS_ORIGINS)
        self.assertEqual(
            resolve_cors_origins(environ={CORS_ORIGINS_ENV_VAR: " "}),
            DEFAULT_CORS_ORIGINS,
        )

    def test_05_the_environment_variable_is_read(self):
        origins = resolve_cors_origins(environ={CORS_ORIGINS_ENV_VAR: "http://x.test"})
        self.assertEqual(origins, ("http://x.test",))


class AppConfigurationTests(unittest.TestCase):
    def _cors_options(self) -> dict:
        """Read the CORS settings the app actually installed."""

        app = create_api_app()
        for middleware in app.user_middleware:
            if middleware.cls.__name__ == "CORSMiddleware":
                return dict(middleware.kwargs)
        self.fail("CORSMiddleware was not installed")

    def test_10_credentialed_cors_is_never_enabled(self):
        options = self._cors_options()
        self.assertFalse(options["allow_credentials"])
        self.assertNotIn("*", options["allow_origins"])

    def test_11_methods_and_headers_are_narrowed(self):
        options = self._cors_options()
        self.assertNotIn("*", options["allow_methods"])
        self.assertNotIn("*", options["allow_headers"])
        self.assertEqual(sorted(options["allow_methods"]), ["GET", "POST"])

    def test_12_the_caller_can_supply_an_explicit_list(self):
        app = create_api_app(cors_origins=("https://only.example.test",))
        options = None
        for middleware in app.user_middleware:
            if middleware.cls.__name__ == "CORSMiddleware":
                options = dict(middleware.kwargs)
        self.assertIsNotNone(options)
        self.assertEqual(options["allow_origins"], ["https://only.example.test"])

    def test_13_the_routes_are_unchanged(self):
        """The fix must not alter the API contract."""

        app = create_api_app()
        paths = sorted(
            route.path for route in app.routes if hasattr(route, "path")
        )
        for expected in (
            "/health",
            "/api/status",
            "/api/projects",
            "/api/agents",
            "/api/dashboard",
            "/api/capabilities",
            "/api/tools",
            "/api/tasks/run",
            "/api/runs/{run_id}",
        ):
            self.assertIn(expected, paths, expected)

    def test_14_no_route_requires_authentication_and_that_is_recorded(self):
        """The residual risk, asserted rather than assumed.

        If this test ever fails because a dependency was added, that is good news and the
        assertion should be updated to require one -- but it must not fail silently by
        never having existed.
        """

        app = create_api_app()
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            self.assertEqual(
                list(getattr(dependant, "dependencies", [])),
                [],
                f"route {getattr(route, 'path', '?')} gained an authentication "
                "dependency; update SOURCE_OF_TRUTH.md and this test together",
            )


class BindAddressTests(unittest.TestCase):
    def test_20_loopback_is_the_default(self):
        host, port = api_main.resolve_bind(environ={})
        self.assertEqual(host, DEFAULT_API_HOST)
        self.assertTrue(is_loopback_host(host))
        self.assertEqual(port, 8000)

    def test_21_the_environment_can_change_the_bind(self):
        host, port = api_main.resolve_bind(
            environ={"FORGE_API_HOST": "0.0.0.0", "FORGE_API_PORT": "9001"}
        )
        self.assertEqual(host, "0.0.0.0")
        self.assertEqual(port, 9001)
        self.assertFalse(is_loopback_host(host))

    def test_22_a_bad_port_is_refused(self):
        for bad in ("not-a-number", "0", "70000", ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                api_main.resolve_bind(environ={"FORGE_API_PORT": bad})

    def test_23_binding_beyond_loopback_warns_loudly(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            message = warn_if_exposed("0.0.0.0")
        self.assertIsNotNone(message)
        self.assertIn("NO authentication", message)
        self.assertEqual(len(caught), 1)
        self.assertTrue(issubclass(caught[0].category, RuntimeWarning))

    def test_24_binding_to_loopback_is_silent(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            message = warn_if_exposed("127.0.0.1")
        self.assertIsNone(message)
        self.assertEqual(caught, [])

    def test_25_the_launcher_warns_before_it_serves(self):
        """The warning must happen at startup, not on the first request."""

        served: list[tuple] = []

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port, log_level=None):
                served.append((host, port))

        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {"uvicorn": _FakeUvicorn}):
            with patch.dict(
                "os.environ",
                {"FORGE_API_HOST": "0.0.0.0", "FORGE_API_PORT": "9100"},
                clear=False,
            ):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    api_main.main([])
        self.assertEqual(served, [("0.0.0.0", 9100)])
        self.assertTrue(
            any("NO authentication" in str(w.message) for w in caught),
            f"expected an exposure warning, got {[str(w.message) for w in caught]}",
        )


if __name__ == "__main__":
    unittest.main()
