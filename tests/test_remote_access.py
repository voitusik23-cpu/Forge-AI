"""Regression tests for driving Forge over a network.

The Core API was built for one local operator: no route requires a credential, so the
launcher refused every bind beyond loopback. The owner's plan is to run Forge on a server
and open its interface in a browser, which puts ``POST /api/tasks/run`` (writes files) and
``POST /api/projects/{id}/run`` (starts processes) behind a socket anyone could reach.

These tests pin the access model that makes that deployment safe, and they pin the absent
case as well, so "no token" can never quietly become "public without a token":

* a bind beyond loopback without a token **refuses to start**, with the reason;
* with a token, every ``/api/*`` route requires it in either spelling and answers 401
  otherwise, compared in constant time;
* health and the build identity stay open;
* 30 mutations a minute per address, then 429 with ``Retry-After``; a body over the
  ceiling is 413;
* the token never appears in a log line, a payload or an error body -- only its
  fingerprint does;
* a loopback deployment with no token is exactly as frictionless as before.

Nothing here opens a socket: every check is in-process, and the one subprocess test
asserts a *refusal*, so it binds nothing either.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import main as api_main
from app.api.access import (
    ACCESS_TOKEN_ENV_VAR,
    DEFAULT_MAX_BODY_BYTES,
    AccessPolicy,
    Limit,
    RateLimiter,
    access_log_line,
    client_address,
    extract_token,
    is_protected_path,
    refuse_if_exposed,
    resolve_access_token,
    token_fingerprint,
    tokens_match,
)
from app.api.server import create_api_app, warn_if_exposed
from app.config.app_settings import AppSettingsStore
from app.config.secrets import SecretStore

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "9f4c1d0b7a2e5c8f"  # a fixture token, never a real one


def _app(policy: AccessPolicy):
    """One API application with the access policy under test."""

    return create_api_app(access=policy)


class BindRefusalTests(unittest.TestCase):
    """A surface without a token must not be reachable from a network, ever."""

    def test_01_non_loopback_without_a_token_is_refused(self):
        with self.assertRaises(RuntimeError) as caught:
            warn_if_exposed("0.0.0.0")
        message = str(caught.exception)
        # The exact reason, the variable to set and the way out, in one sentence an
        # operator reads in a terminal or in `journalctl`.
        self.assertIn("refuses non-loopback", message)
        self.assertIn("0.0.0.0", message)
        self.assertIn("no access token is configured", message)
        self.assertIn(ACCESS_TOKEN_ENV_VAR, message)
        self.assertIn("deploy/README-SSH-TUNNEL.md", message)

    def test_02_the_same_bind_with_a_token_is_allowed(self):
        self.assertIsNone(warn_if_exposed("0.0.0.0", "any-token"))
        self.assertIsNone(refuse_if_exposed("10.0.0.5", TOKEN))

    def test_03_loopback_needs_no_token(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertIsNone(warn_if_exposed(host), host)

    def test_04_the_shell_server_names_itself_in_the_refusal(self):
        with self.assertRaises(RuntimeError) as caught:
            refuse_if_exposed("0.0.0.0", "", surface="Forge UI")
        self.assertIn("Forge UI refuses non-loopback", str(caught.exception))

    def test_05_the_launcher_refuses_before_it_builds_or_serves(self):
        """The refusal must happen before app construction and before uvicorn runs."""

        served: list[tuple] = []

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port, log_level=None):
                served.append((host, port))

        with patch.dict(sys.modules, {"uvicorn": _FakeUvicorn}):
            with patch.object(api_main, "resolve_bind", return_value=("0.0.0.0", 9100)):
                with patch.object(api_main, "compose_access_policy", return_value=AccessPolicy()):
                    with self.assertRaisesRegex(RuntimeError, "refuses non-loopback"):
                        api_main.main([])
        self.assertEqual(served, [])

    def test_06_the_launcher_serves_a_non_loopback_bind_once_a_token_exists(self):
        """The same bind is served when a token guards it -- and the reason is not lost."""

        served: list[tuple] = []
        created: list[dict] = []

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port, log_level=None):
                served.append((host, port))

        class _FakeSettings:
            """Only what ``main`` touches before serving: no Desktop folder is created."""

            def __init__(self, path=None):
                self.path = pathlib.Path(path or "fake-settings.json")

            def ensure_root(self):
                return None

            def api_bind_host(self):
                return "0.0.0.0"

            def api_port(self):
                return 9100

            def access_token(self):
                return ""

        def _record_app(**kwargs):
            created.append(kwargs)
            return object()

        with patch.dict(sys.modules, {"uvicorn": _FakeUvicorn}):
            with patch.object(api_main, "resolve_bind", return_value=("0.0.0.0", 9100)):
                with patch.object(
                    api_main,
                    "compose_access_policy",
                    return_value=AccessPolicy(token=TOKEN),
                ):
                    with patch.object(api_main, "build_idempotency_guard", return_value=None):
                        with patch.object(api_main, "compose_project_service", return_value=None):
                            with patch.object(api_main, "create_api_app", side_effect=_record_app):
                                with patch("app.config.app_settings.AppSettingsStore", _FakeSettings):
                                    self.assertEqual(api_main.main([]), 0)
        self.assertEqual(served, [("0.0.0.0", 9100)])
        self.assertEqual(created[0]["access"].token, TOKEN)


class TokenResolutionTests(unittest.TestCase):
    """Where the token comes from, in one documented order."""

    def test_10_the_secrets_layer_wins_over_the_settings_file(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = pathlib.Path(scratch) / "settings.json"
            path.write_text(json.dumps({"access_token": "from-the-settings-file"}), encoding="utf-8")
            settings = AppSettingsStore(path)
            store = SecretStore(environ={ACCESS_TOKEN_ENV_VAR: "from-the-environment"})
            self.assertEqual(
                resolve_access_token(secret_store=store, settings=settings),
                "from-the-environment",
            )

    def test_11_the_settings_file_is_the_last_resort(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = pathlib.Path(scratch) / "settings.json"
            path.write_text(json.dumps({"access_token": "  from-the-settings-file  "}), encoding="utf-8")
            self.assertEqual(
                resolve_access_token(
                    secret_store=SecretStore(environ={}), settings=AppSettingsStore(path)
                ),
                "from-the-settings-file",
            )

    def test_12_no_token_anywhere_answers_empty(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = pathlib.Path(scratch) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_access_token(
                    secret_store=SecretStore(environ={}), settings=AppSettingsStore(path)
                ),
                "",
            )

    def test_13_an_unreadable_settings_file_is_no_token(self):
        class _Broken:
            def access_token(self):
                raise OSError("unreadable")

        self.assertEqual(
            resolve_access_token(secret_store=SecretStore(environ={}), settings=_Broken()), ""
        )

    def test_14_a_blank_token_is_no_token(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = pathlib.Path(scratch) / "settings.json"
            path.write_text(json.dumps({"access_token": "   "}), encoding="utf-8")
            self.assertEqual(AppSettingsStore(path).access_token(), "")


class ConstantTimeCompareTests(unittest.TestCase):
    """The comparison must not leak where two tokens differ."""

    def test_20_hmac_compare_digest_is_the_comparison(self):
        calls: list[tuple] = []
        real = __import__("hmac").compare_digest

        def spy(left, right):
            calls.append((left, right))
            return real(left, right)

        with patch("app.api.access.hmac.compare_digest", side_effect=spy):
            self.assertTrue(tokens_match(TOKEN, TOKEN))
            self.assertFalse(tokens_match("wrong", TOKEN))
        self.assertEqual(len(calls), 2)
        # Bytes, so the comparison is over a fixed-width representation rather than over
        # whatever str would do with an encoding difference.
        self.assertEqual(calls[0], (TOKEN.encode("utf-8"), TOKEN.encode("utf-8")))

    def test_21_the_helper_is_total(self):
        """No input makes it raise, and an empty expected token accepts nothing."""

        for provided in ("", None, 0, TOKEN[:-1], TOKEN + "x", b"bytes"):
            self.assertFalse(tokens_match(provided, TOKEN), repr(provided))
        self.assertFalse(tokens_match("", ""))
        self.assertFalse(tokens_match(TOKEN, ""))

    def test_22_the_request_path_uses_the_helper(self):
        calls: list[tuple] = []
        real = __import__("hmac").compare_digest

        def spy(left, right):
            calls.append((left, right))
            return real(left, right)

        with patch("app.api.access.hmac.compare_digest", side_effect=spy):
            client = TestClient(_app(AccessPolicy(token=TOKEN)))
            self.assertEqual(
                client.get("/api/status", headers={"X-Forge-Token": TOKEN}).status_code, 200
            )
        self.assertTrue(calls, "the request path did not compare in constant time")


class TokenAuthenticationTests(unittest.TestCase):
    """`/api/*` with a token: either spelling works, nothing else does."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(_app(AccessPolicy(token=TOKEN)))

    def test_30_no_token_is_401_with_a_plain_message(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["detail"]["error_code"], "unauthorized")
        self.assertFalse(body["detail"]["executed"])
        self.assertIn("access token", body["detail"]["message"])
        self.assertIn("Bearer", body["detail"]["message"])
        self.assertEqual(response.headers.get("WWW-Authenticate"), 'Bearer realm="forge"')

    def test_31_bearer_authorization_is_accepted(self):
        response = self.client.get(
            "/api/status", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api"], "connected")

    def test_32_the_direct_header_is_accepted(self):
        response = self.client.get("/api/status", headers={"X-Forge-Token": TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_33_a_wrong_token_is_401(self):
        for headers in (
            {"Authorization": "Bearer wrong-token"},
            {"X-Forge-Token": "wrong-token"},
            {"Authorization": TOKEN},  # no scheme: not a bearer credential
            {"Authorization": "Basic " + TOKEN},
        ):
            response = self.client.get("/api/status", headers=headers)
            self.assertEqual(response.status_code, 401, headers)

    def test_34_every_mutating_route_is_protected(self):
        for path in (
            "/api/tasks/run",
            "/api/projects/proj-x/run",
            "/api/projects/proj-x/launch",
        ):
            response = self.client.post(path, json={"description": "run something"})
            self.assertEqual(response.status_code, 401, path)

    def test_35_health_and_the_build_identity_are_open(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/version").status_code, 200)
        # `/api/health` is not a route on this surface; it is unauthenticated anyway, so it
        # answers 404 rather than 401 -- a load balancer gets a truthful "no such route".
        self.assertEqual(self.client.get("/api/health").status_code, 404)

    def test_36_a_trailing_slash_is_not_an_escape(self):
        self.assertEqual(self.client.get("/api/status/").status_code, 401)
        self.assertEqual(self.client.get("/api/status/../../api/status").status_code, 401)

    def test_37_the_path_rule_is_shared(self):
        self.assertTrue(is_protected_path("/api/status"))
        self.assertTrue(is_protected_path("/api/tasks/run"))
        self.assertTrue(is_protected_path("/api"))
        self.assertFalse(is_protected_path("/health"))
        self.assertFalse(is_protected_path("/api/version"))
        self.assertFalse(is_protected_path("/api/health"))


class LoopbackStaysFrictionlessTests(unittest.TestCase):
    """No token configured: nothing changes, and nothing is protected by accident."""

    def test_40_no_policy_installs_no_middleware(self):
        app = create_api_app()
        self.assertFalse(app.state.forge_access.enabled)
        self.assertIsNone(app.state.forge_rate_limiter)
        client = TestClient(app)
        self.assertEqual(client.get("/api/status").status_code, 200)

    def test_41_a_policy_without_a_token_is_the_same_thing(self):
        app = _app(AccessPolicy())
        self.assertFalse(app.state.forge_access.enabled)
        self.assertIsNone(app.state.forge_rate_limiter)


class RateLimitTests(unittest.TestCase):
    """A public box gets a per-address ceiling, and says when to come back."""

    def test_50_the_31st_mutation_in_a_minute_is_429(self):
        policy = AccessPolicy(token=TOKEN)
        client = TestClient(_app(policy))
        headers = {"X-Forge-Token": TOKEN}
        statuses = [
            client.post("/api/not-a-route", headers=headers).status_code for _ in range(30)
        ]
        self.assertEqual(statuses, [404] * 30)
        refused = client.post("/api/not-a-route", headers=headers)
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(refused.json()["detail"]["error_code"], "rate_limited")
        retry_after = int(refused.headers["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)

    def test_51_reads_have_their_own_larger_bucket(self):
        """A refused mutation does not spend the read budget the interface polls with."""

        policy = AccessPolicy(token=TOKEN)
        client = TestClient(_app(policy))
        headers = {"X-Forge-Token": TOKEN}
        for _ in range(30):
            client.post("/api/not-a-route", headers=headers)
        self.assertEqual(client.post("/api/not-a-route", headers=headers).status_code, 429)
        self.assertEqual(client.get("/api/status", headers=headers).status_code, 200)

    def test_53_behind_the_proxy_the_real_client_is_charged(self):
        """nginx appends the client it saw, so the last entry is the one to trust.

        A forwarded header is only read when the peer is this machine: from anywhere else
        it is client-controlled and must not be believed.
        """

        self.assertEqual(client_address("127.0.0.1", ""), "127.0.0.1")
        self.assertEqual(client_address("127.0.0.1", "203.0.113.9"), "203.0.113.9")
        self.assertEqual(
            client_address("::ffff:127.0.0.1", "1.2.3.4, 203.0.113.9"), "203.0.113.9"
        )
        self.assertEqual(
            client_address("198.51.100.7", "203.0.113.9"), "198.51.100.7"
        )
        self.assertEqual(client_address(None, ""), "unknown")

    def test_52_the_bucket_refills_and_reports_the_wait(self):
        now = [0.0]
        limiter = RateLimiter(
            mutation_limit=Limit(2, 60.0), read_limit=Limit(10, 60.0), clock=lambda: now[0]
        )
        self.assertIsNone(limiter.check("10.0.0.1", "POST"))
        self.assertIsNone(limiter.check("10.0.0.1", "POST"))
        wait = limiter.check("10.0.0.1", "POST")
        self.assertIsNotNone(wait)
        self.assertGreaterEqual(int(wait), 1)
        self.assertLessEqual(int(wait), 60)
        # A different address is a different bucket.
        self.assertIsNone(limiter.check("10.0.0.2", "POST"))
        # Half the window refills one token of two.
        now[0] = 35.0
        self.assertIsNone(limiter.check("10.0.0.1", "POST"))


class BodyCeilingTests(unittest.TestCase):
    """A body larger than the ceiling is refused before anything reads it."""

    def test_60_an_oversized_body_is_413(self):
        policy = AccessPolicy(token=TOKEN, max_body_bytes=1024)
        client = TestClient(_app(policy))
        response = client.post(
            "/api/tasks/run",
            content=b"x" * 2048,
            headers={"X-Forge-Token": TOKEN, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        body = response.json()
        self.assertEqual(body["detail"]["error_code"], "payload_too_large")
        self.assertFalse(body["detail"]["executed"])

    def test_61_a_body_within_the_ceiling_reaches_the_route(self):
        policy = AccessPolicy(token=TOKEN, max_body_bytes=1024)
        client = TestClient(_app(policy))
        # 400 (no `description`) proves the request reached the route rather than the ceiling.
        response = client.post(
            "/api/tasks/run",
            json={"description": ""},
            headers={"X-Forge-Token": TOKEN},
        )
        self.assertEqual(response.status_code, 400)

    def test_62_the_default_ceiling_is_the_documented_eight_mebibytes(self):
        self.assertEqual(AccessPolicy().max_body_bytes, DEFAULT_MAX_BODY_BYTES)
        self.assertEqual(DEFAULT_MAX_BODY_BYTES, 8 * 1024 * 1024)


class TheTokenNeverLeavesTests(unittest.TestCase):
    """Only the fingerprint is publishable; the token itself is not."""

    def test_70_the_log_line_carries_the_fingerprint_and_never_the_token(self):
        line = access_log_line(host="0.0.0.0", port=8011, policy=AccessPolicy(token=TOKEN))
        self.assertNotIn(TOKEN, line)
        self.assertIn(token_fingerprint(TOKEN), line)
        self.assertIn("auth: on", line)
        self.assertIn("0.0.0.0:8011", line)
        self.assertIn("8388608", line)
        self.assertIn("30 mutations", line)

    def test_71_the_absent_case_says_so(self):
        line = access_log_line(host="127.0.0.1", port=8011, policy=AccessPolicy())
        self.assertIn("auth: OFF", line)
        self.assertIn("loopback only", line)

    def test_72_no_payload_names_it(self):
        app = _app(AccessPolicy(token=TOKEN))
        client = TestClient(app)
        bodies = [
            client.get("/api/status").text,
            client.get("/api/status", headers={"X-Forge-Token": "wrong"}).text,
            client.get("/health").text,
            client.get("/api/version").text,
            client.get("/api/models", headers={"X-Forge-Token": TOKEN}).text,
        ]
        for body in bodies:
            self.assertNotIn(TOKEN, body)
        self.assertNotIn(TOKEN, json.dumps(app.state.forge_access.describe()))
        self.assertIn(token_fingerprint(TOKEN), json.dumps(app.state.forge_access.describe()))

    def test_73_the_fingerprint_is_stable_and_short(self):
        self.assertEqual(token_fingerprint(TOKEN), token_fingerprint(TOKEN))
        self.assertEqual(len(token_fingerprint(TOKEN)), 6)
        self.assertNotEqual(token_fingerprint(TOKEN), token_fingerprint(TOKEN + "x"))
        self.assertEqual(token_fingerprint(""), "")
        self.assertEqual(token_fingerprint(None), "")


class HeaderParsingTests(unittest.TestCase):
    """Both spellings are read, and nothing else is a credential."""

    def test_80_both_headers_are_extracted(self):
        self.assertEqual(
            extract_token({"authorization": f"Bearer {TOKEN}"}), (TOKEN,)
        )
        self.assertEqual(extract_token({"x-forge-token": TOKEN}), (TOKEN,))
        self.assertEqual(
            extract_token({"authorization": f"Bearer {TOKEN}", "x-forge-token": TOKEN}),
            (TOKEN, TOKEN),
        )

    def test_81_anything_else_is_nothing(self):
        for headers in (
            {},
            {"authorization": TOKEN},
            {"authorization": "Basic abc"},
            {"authorization": "Bearer "},
            {"x-forge-token": "   "},
            {"authorization": "bearer"},
        ):
            self.assertEqual(extract_token(headers), (), headers)

    def test_82_the_scheme_is_case_insensitive_and_the_token_is_trimmed(self):
        self.assertEqual(
            extract_token({"authorization": f"bEaReR   {TOKEN}   "}), (TOKEN,)
        )


class BindSettingTests(unittest.TestCase):
    """The settings file decides the shape; the environment only overrides it."""

    def _settings(self, scratch: str, document: dict) -> AppSettingsStore:
        path = pathlib.Path(scratch) / "settings.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return AppSettingsStore(path)

    def test_90_settings_decide_the_bind(self):
        with tempfile.TemporaryDirectory() as scratch:
            settings = self._settings(
                scratch, {"api_bind_host": "0.0.0.0", "api_port": 9001}
            )
            self.assertEqual(api_main.resolve_bind(environ={}, settings=settings), ("0.0.0.0", 9001))

    def test_91_an_explicit_environment_variable_wins(self):
        with tempfile.TemporaryDirectory() as scratch:
            settings = self._settings(
                scratch, {"api_bind_host": "0.0.0.0", "api_port": 9001}
            )
            self.assertEqual(
                api_main.resolve_bind(
                    environ={"FORGE_API_HOST": "127.0.0.1", "FORGE_API_PORT": "8011"},
                    settings=settings,
                ),
                ("127.0.0.1", 8011),
            )

    def test_92_the_defaults_are_loopback(self):
        with tempfile.TemporaryDirectory() as scratch:
            settings = self._settings(scratch, {})
            self.assertEqual(settings.api_bind_host(), "127.0.0.1")
            self.assertEqual(settings.api_port(), 8011)
            self.assertEqual(settings.ui_bind_host(), "127.0.0.1")
            self.assertEqual(settings.ui_port(), 8020)
            self.assertEqual(api_main.resolve_bind(environ={}, settings=settings), ("127.0.0.1", 8011))
            # Without a settings store the documented launcher default is unchanged.
            self.assertEqual(api_main.resolve_bind(environ={}), ("127.0.0.1", 8000))

    def test_93_a_useless_port_falls_back_instead_of_spreading(self):
        with tempfile.TemporaryDirectory() as scratch:
            for value in ("not-a-port", 0, 70000, -1, True, None):
                settings = self._settings(scratch, {"api_port": value})
                self.assertEqual(settings.api_port(), 8011, value)

    def test_94_a_blank_host_is_loopback_and_never_every_interface(self):
        with tempfile.TemporaryDirectory() as scratch:
            for value in ("", "   ", 42, None):
                settings = self._settings(scratch, {"api_bind_host": value})
                self.assertEqual(settings.api_bind_host(), "127.0.0.1", value)


class ShellServerTests(unittest.TestCase):
    """The interface server applies the same rules as the API, by construction."""

    @classmethod
    def setUpClass(cls):
        path = REPOSITORY / "tools" / "ui_preview_server.py"
        spec = importlib.util.spec_from_file_location("forge_ui_preview_server", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.module = module

    def test_95_dynamic_routes_need_the_token_and_assets_do_not(self):
        needs = self.module.path_needs_token
        for path in ("/api/status", "/api/tasks/run", "/diag", "/diag?x=1", "/ui-defaults.json", "/docs/USER_MANUAL.md"):
            self.assertTrue(needs(path), path)
        for path in ("/", "/index.html", "/assets/index-abc123.js", "/favicon.ico"):
            self.assertFalse(needs(path), path)

    def test_96_the_shell_accepts_the_same_two_headers(self):
        module = self.module
        original = module.Handler.access
        try:
            module.Handler.access = AccessPolicy(token=TOKEN)

            def _authorized(headers: dict) -> bool:
                """One real Handler without opening a socket: only `headers` is read."""

                handler = module.Handler.__new__(module.Handler)
                handler.headers = headers
                return handler._authorized()

            self.assertTrue(_authorized({"X-Forge-Token": TOKEN}))
            self.assertTrue(_authorized({"Authorization": f"Bearer {TOKEN}"}))
            self.assertFalse(_authorized({}))
            self.assertFalse(_authorized({"X-Forge-Token": "nope"}))
        finally:
            module.Handler.access = original

    def test_97_a_non_loopback_bind_without_a_token_exits_with_the_reason(self):
        """A real process, a real refusal -- and no socket opened, because it refuses first."""

        with tempfile.TemporaryDirectory() as scratch:
            path = pathlib.Path(scratch) / "settings.json"
            path.write_text(
                json.dumps({"ui_bind_host": "0.0.0.0", "ui_port": 8029}), encoding="utf-8"
            )
            if resolve_access_token(secret_store=SecretStore(environ={}), settings=AppSettingsStore(path)):
                self.skipTest("a real access token is configured here; the refusal cannot be produced")
            environment = dict(os.environ)
            environment.pop(ACCESS_TOKEN_ENV_VAR, None)
            environment["FORGE_SETTINGS_FILE"] = str(path)
            completed = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(REPOSITORY / "tools" / "ui_preview_server.py")],
                cwd=str(REPOSITORY),
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            self.assertIn("refuses non-loopback binding", completed.stderr)
            self.assertIn(ACCESS_TOKEN_ENV_VAR, completed.stderr)


if __name__ == "__main__":
    unittest.main()
