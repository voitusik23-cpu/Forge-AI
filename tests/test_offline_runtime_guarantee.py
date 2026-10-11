"""The TEST-runtime guarantee: a test runtime cannot reach a live provider.

This module tests **observed behaviour**, not the presence of a class, a flag, or
a string. The guarantee is enforced in one place --
``app.runtime.bootstrap.create_runtime`` -- and has three parts:

1. ``test_mode=True``, or ``FORGE_ENV=test`` in the environment, marks the
   composition a TEST runtime. The default is ``False``, so production is
   unchanged.
2. A TEST runtime narrows ``RuntimeSettings`` so the offline provider is the only
   enabled one, the default provider, with no fallback chain and paid providers
   off.
3. A TEST runtime **constructs only the providers it enables**. This third part is
   what actually removes reachability: ``tests/test_dispatcher.py::
   test_explicit_provider_keeps_priority_over_auto_availability`` pins an
   explicitly named provider as reaching execution even when it is disabled by
   configuration, so a merely-disabled live provider would still have a reachable
   transport.

Every network assertion is made by intercepting the **real** transport seam
(``socket.socket.connect``, ``socket.create_connection``, ``socket.getaddrinfo``),
never by mocking Forge's own code. The interception is always installed and
restored in a ``finally`` block, and every test that relies on it also asserts
that the seam was genuinely live while it was installed -- otherwise a broken spy
would make the test pass for the wrong reason.

The spy window deliberately covers ``create_runtime`` as well, because that call
is what reads the developer's ambient ``.env`` through ``SecretStore``. Covering
it turns "the dispatch makes no call" into "nothing in this workflow, including
the ambient-secret path, opens a connection".

Nothing here reads or prints a real secret value. The credential-shaped strings
below are generated inside the test and exist only in this process.
"""

from __future__ import annotations

import logging
import os
import socket
import unittest
import uuid
from unittest.mock import patch

from app.api.models import TaskRunRequest
from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings
from app.orchestrator.models import Task
from app.runtime.bootstrap import create_runtime

#: Providers that would spend money or reach the network if they were reachable.
LIVE_PROVIDERS = (
    "openai",
    "anthropic",
    "deepseek",
    "google",
    "xai",
    "openrouter",
    "groq",
    "together",
)

#: Names of the environment variables a live provider would look for. Only the
#: *names* are used here; the values are fake and generated per test.
LIVE_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
)

#: Hosts a provider adapter would dial. Named so a failure message can say which
#: provider a leaked attempt belonged to.
LIVE_HOST_SUFFIXES = (
    "openrouter.ai",
    "api.deepseek.com",
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.x.ai",
    "api.groq.com",
    "api.together.xyz",
)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def fake_key(marker: str) -> str:
    """Return an artificial, obviously-synthetic credential-shaped value.

    The ``sk-`` prefix and the hex tail make the value match the shape a secret
    scanner looks for, so a leak is detectable, while the ``forge-fake`` infix
    makes it unmistakable that this is not a real credential.
    """

    return f"sk-forge-fake-{marker}-{uuid.uuid4().hex}"


def _host_of(address: object) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address) if address is not None else ""


class _NetworkSpy:
    """Intercept the real outbound-network seam and record every attempt.

    This is a genuine interception: the process's own ``socket`` module is
    patched, so any code path that tries to open a connection -- the OpenAI SDK,
    ``urllib``, a raw :mod:`socket` call, or Forge's own adapters -- is observed.

    It patches ``socket.socket.connect`` (the lowest seam every TCP connect goes
    through), ``socket.socket.connect_ex``, ``socket.create_connection``, and
    ``socket.getaddrinfo`` (DNS). Recording happens in the wrapper and the original
    is then called unchanged, so the interception never changes behaviour -- it
    only observes it. ``__exit__`` restores every original, including on the
    failure path.
    """

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []
        self._originals: dict[str, object] = {}
        #: Set by `_assert_seam_is_live` once the interception has been proven to
        #: observe a real connect. A test may assert on it.
        self.proven_live = False

    def _guard_connect(self, original):  # type: ignore[no-untyped-def]
        def guard(sock_self, address, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.attempts.append(("socket.socket.connect", _host_of(address)))
            return original(sock_self, address, *args, **kwargs)

        return guard

    def _guard_connect_ex(self, original):  # type: ignore[no-untyped-def]
        def guard(sock_self, address, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.attempts.append(("socket.socket.connect_ex", _host_of(address)))
            return original(sock_self, address, *args, **kwargs)

        return guard

    def _guard_create_connection(self, original):  # type: ignore[no-untyped-def]
        def guard(address, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.attempts.append(("socket.create_connection", _host_of(address)))
            return original(address, *args, **kwargs)

        return guard

    def _guard_getaddrinfo(self, original):  # type: ignore[no-untyped-def]
        def guard(host, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.attempts.append(("socket.getaddrinfo", _host_of(host)))
            return original(host, *args, **kwargs)

        return guard

    def __enter__(self) -> "_NetworkSpy":
        try:
            self._originals["connect"] = socket.socket.connect
            self._originals["connect_ex"] = socket.socket.connect_ex
            self._originals["create_connection"] = socket.create_connection
            self._originals["getaddrinfo"] = socket.getaddrinfo
            socket.socket.connect = self._guard_connect(self._originals["connect"])  # type: ignore[assignment]
            socket.socket.connect_ex = self._guard_connect_ex(self._originals["connect_ex"])  # type: ignore[assignment]
            socket.create_connection = self._guard_create_connection(self._originals["create_connection"])  # type: ignore[assignment]
            socket.getaddrinfo = self._guard_getaddrinfo(self._originals["getaddrinfo"])  # type: ignore[assignment]
        except Exception:
            # Never leave a half-installed interception behind.
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if "connect" in self._originals:
            socket.socket.connect = self._originals["connect"]  # type: ignore[assignment]
        if "connect_ex" in self._originals:
            socket.socket.connect_ex = self._originals["connect_ex"]  # type: ignore[assignment]
        if "create_connection" in self._originals:
            socket.create_connection = self._originals["create_connection"]  # type: ignore[assignment]
        if "getaddrinfo" in self._originals:
            socket.getaddrinfo = self._originals["getaddrinfo"]  # type: ignore[assignment]
        self._originals.clear()
        return False

    def assert_seam_is_live(self) -> None:
        """Prove the interception observes a real connect, not just that it exists.

        Without this a broken spy would report zero attempts and make every
        assertion below vacuous. The address is a loopback port that is not
        expected to be listening: nothing leaves the machine and the only fact
        relied on is that ``connect`` was called and seen.
        """

        before = len(self.attempts)
        probe = socket.socket()
        try:
            probe.connect(("127.0.0.1", 9))
        except OSError:
            pass
        finally:
            probe.close()
        if len(self.attempts) == before:
            raise AssertionError(
                "the network interception did not observe a real connect() call, "
                "so it cannot prove anything about outbound traffic"
            )
        # Drop the control observation so it cannot be mistaken for real traffic.
        del self.attempts[before:]
        self.proven_live = True

    @property
    def outbound(self) -> list[tuple[str, str]]:
        """Attempts that would leave the machine, with the provider named in the message."""

        return [item for item in self.attempts if item[1] not in _LOCAL_HOSTS]

    def describe_outbound(self) -> str:
        rendered = []
        for seam, host in self.outbound:
            provider = next(
                (suffix for suffix in LIVE_HOST_SUFFIXES if host.endswith(suffix)), "?"
            )
            rendered.append(f"{seam} -> {host} (provider: {provider})")
        return "; ".join(rendered) or "(none)"


class TestRuntimeGuaranteeTests(unittest.TestCase):
    """The four required observations, plus the seam's own self-checks."""

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _fake_environment(marker: str) -> dict[str, str]:
        """A process environment that looks like a developer machine with keys."""

        environ = {name: fake_key(marker) for name in LIVE_KEY_NAMES}
        environ["FORGE_ENV"] = "test"
        environ["FORGE_DEFAULT_PROVIDER"] = "openrouter"
        environ["FORGE_ENABLED_PROVIDERS"] = "mock,openrouter,openai,google"
        environ["FORGE_ALLOW_PAID_PROVIDERS"] = "true"
        return environ

    def assertNoOutbound(self, spy: _NetworkSpy) -> None:  # noqa: N802 - unittest style
        self.assertTrue(spy.proven_live, "the interception was never proven live")
        self.assertEqual(
            spy.outbound,
            [],
            "a test-mode workflow attempted to reach the network: "
            + spy.describe_outbound(),
        )

    # ------------------------------------------------- 1. no live provider on
    def test_01_fake_keys_present_do_not_enable_a_live_provider(self) -> None:
        """With fake keys in the environment, a TEST runtime has no live provider at all.

        The assertion is on the runtime a service actually dispatches with: which
        providers exist in the provider registry, which agents the dispatcher can
        reach, and what the runtime's own settings say. A live provider is not
        merely disabled -- it is never constructed. That is the stronger fact and
        the one that matters, because ``tests/test_dispatcher.py`` pins explicit
        provider selection as reaching execution even for a disabled provider.
        """

        marker = uuid.uuid4().hex[:8]
        environ = self._fake_environment(marker)

        with patch.dict(os.environ, environ, clear=False):
            spy = _NetworkSpy()
            with spy:
                spy.assert_seam_is_live()

                # The settings a bare `create_runtime()` would read really do ask
                # for live providers and really can see the fake keys. This is the
                # dangerous input, and it is asserted rather than assumed.
                from app.config.settings import load_settings

                ambient = load_settings()
                self.assertIn("openrouter", ambient.enabled_providers)
                self.assertEqual(ambient.default_provider, "openrouter")
                self.assertTrue(ambient.allow_paid_providers)
                from app.config.secrets import SecretStore

                self.assertTrue(SecretStore().get_secret("OPENROUTER_API_KEY"))

                # ... and the TEST runtime removes every bit of that reachability.
                runtime = create_runtime()

                result = runtime.orchestrator.dispatch(
                    Task(id="guarantee-probe", description="offline only")
                )
            # The spy restored the real socket functions here, including on failure.

        self.assertNoOutbound(spy)
        self.assertEqual(runtime.settings.default_provider, OFFLINE_PROVIDER_NAME)
        self.assertEqual(runtime.settings.enabled_providers, (OFFLINE_PROVIDER_NAME,))
        self.assertEqual(runtime.settings.provider_fallback_chain, ())
        self.assertFalse(runtime.settings.allow_paid_providers)

        self.assertEqual(
            runtime.provider_registry.list_providers(),
            [OFFLINE_PROVIDER_NAME],
            "a TEST runtime must construct exactly the offline provider",
        )
        self.assertEqual(runtime.agent_registry.list_agents(), [OFFLINE_PROVIDER_NAME])
        for name in LIVE_PROVIDERS:
            with self.assertRaises(LookupError):
                runtime.provider_registry.get(name)
            with self.assertRaises(LookupError):
                runtime.provider_capabilities.get(name)

        # The dispatcher's own view: the configured default is offline.
        dispatcher = runtime.orchestrator._dispatcher
        self.assertEqual(dispatcher._default_provider, OFFLINE_PROVIDER_NAME)
        self.assertEqual(dispatcher._fallback_chain, ())
        self.assertFalse(dispatcher._allow_paid_providers)

        # And the only agent that can answer is the offline one.
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.provider, OFFLINE_PROVIDER_NAME)
        self.assertIn("MockProvider response:", result.output or "")

    def test_01b_test_mode_argument_narrows_even_explicit_settings(self) -> None:
        """``test_mode=True`` narrows settings the caller supplied itself.

        The narrowing is unconditional. A caller-supplied ``settings`` object keeps
        the historical *registry shape* -- every provider constructed, only the
        enabled ones switched on -- because that caller has stated the runtime's
        shape explicitly. What it cannot keep is a live provider in
        ``enabled_providers``: that is removed here, so no live provider is enabled
        and no reachable one can be selected automatically.
        """

        permissive = RuntimeSettings(
            environment="test",
            default_provider="openrouter",
            enabled_providers=("mock", "openrouter", "openai"),
            provider_fallback_chain=("openai",),
            allow_paid_providers=True,
        )
        runtime = create_runtime(settings=permissive, test_mode=True)

        self.assertEqual(runtime.settings.default_provider, OFFLINE_PROVIDER_NAME)
        self.assertEqual(runtime.settings.enabled_providers, (OFFLINE_PROVIDER_NAME,))
        self.assertEqual(runtime.settings.provider_fallback_chain, ())
        self.assertFalse(runtime.settings.allow_paid_providers)
        for name in ("openrouter", "openai"):
            self.assertFalse(
                runtime.provider_registry.get(name).config.enabled,
                f"{name} must not be enabled when test_mode=True",
            )

    def test_01c_production_composition_is_unchanged(self) -> None:
        """Without a test signal, the previous behaviour is exactly preserved.

        This is the compatibility half of the guarantee: the change may only
        narrow reachability when a TEST runtime is requested.
        """

        settings = RuntimeSettings(
            environment="production",
            default_provider="mock",
            enabled_providers=("mock", "openrouter"),
            provider_fallback_chain=(),
        )
        with patch.dict(os.environ, {"FORGE_ENV": "production"}, clear=False):
            runtime = create_runtime(settings=settings)

            # The full provider set is still registered, exactly as before.
            self.assertEqual(len(runtime.provider_registry.list_providers()), 9)
            self.assertIn("openrouter", runtime.provider_registry.list_providers())
            self.assertEqual(
                runtime.settings.enabled_providers, ("mock", "openrouter")
            )
            self.assertTrue(runtime.provider_registry.get("openrouter").config.enabled)
            self.assertTrue(
                runtime.provider_capabilities.get("openrouter").enabled_by_config
            )

    # ------------------------------------- 2. forbidden selection fails safely
    def test_02_explicit_forbidden_provider_is_refused_without_fallback(self) -> None:
        """Selecting a live provider in a TEST runtime fails; it does not fall back.

        **Which outcome, and why that is the safe one.** The refusal is chosen over
        a fallback. A fallback would let a test that believes it is exercising a
        specific provider quietly succeed on the offline double instead, so the
        test would pass while proving nothing about the provider it named. A
        refusal is loud and cannot be mistaken for success.

        The refusal happens *before* any transport is reached, because the live
        provider is not registered in a TEST runtime at all. Both facts are
        asserted, with the real ``socket`` seam intercepted underneath and a fake
        ``OPENROUTER_API_KEY`` in the environment so that a missing credential
        could not be the reason nothing happened.
        """

        with patch.dict(os.environ, {"FORGE_ENV": "test"}, clear=False):
            spy = _NetworkSpy()
            with spy:
                spy.assert_seam_is_live()
                runtime = create_runtime()
            self.assertNoOutbound(spy)

        task = Task(id="forbidden-probe", description="must not leave the machine")

        with patch.dict(
            os.environ, {"OPENROUTER_API_KEY": fake_key("probe")}, clear=False
        ):
            spy = _NetworkSpy()
            with spy:
                spy.assert_seam_is_live()
                result = runtime.orchestrator.dispatch(task, provider_name="openrouter")
            self.assertNoOutbound(spy)

        self.assertFalse(result.success)
        self.assertIn("unavailable or not registered", result.error or "")
        # The exhaustion result names the provider that was *asked for*; what it
        # must not carry is any output, because nothing was ever generated.
        self.assertFalse(result.output)
        # The offline provider was NOT silently substituted: no fallback happened.
        self.assertNotEqual(result.provider, OFFLINE_PROVIDER_NAME)

    def test_02b_forbidden_selection_is_refused_by_the_runs_own_executor(self) -> None:
        """The same refusal via ``RunExecutor``, the path a service actually uses."""

        with patch.dict(os.environ, {"FORGE_ENV": "test"}, clear=False):
            runtime = create_runtime()

        with patch.dict(
            os.environ, {"OPENROUTER_API_KEY": fake_key("executor")}, clear=False
        ):
            spy = _NetworkSpy()
            with spy:
                spy.assert_seam_is_live()
                run = runtime.run_executor.execute(
                    Task(id="executor-probe", description="must not leave the machine"),
                    provider_name="openrouter",
                )
            self.assertNoOutbound(spy)

        self.assertEqual(run.state.value, "FAILED")
        # No host execution happened, so there are no execution results at all.
        self.assertFalse(run.execution_results)

    # ------------------------------------ 3. no outbound connection in a run
    def test_03_a_representative_test_workflow_makes_zero_outbound_attempts(self) -> None:
        """A representative TEST-mode workflow is observed to make no connection.

        The workflow is a real one: a task is dispatched through the runtime and
        then through the API service the desktop shell drives, in the shape that
        previously spent 586 real tokens on OpenRouter. ``create_runtime`` is inside
        the spy window too, so the ambient ``.env``/``SecretStore`` path is covered
        and not merely assumed to be harmless.
        """

        environ = self._fake_environment(uuid.uuid4().hex[:8])

        with patch.dict(os.environ, environ, clear=False):
            spy = _NetworkSpy()
            with spy:
                spy.assert_seam_is_live()

                runtime = create_runtime()
                direct = runtime.orchestrator.dispatch(
                    Task(id="net-probe-1", description="offline dispatch")
                )

                from app.api.service import ForgeApiService

                service = ForgeApiService(runtime=runtime)
                response = service.run_task(
                    TaskRunRequest(description="offline service dispatch")
                )
            # `with spy` restored the originals on the way out, including on the
            # failure path, so a patched socket can never leak into later tests.

        self.assertNoOutbound(spy)
        self.assertTrue(direct.success, direct.error)
        self.assertTrue(response.success, response.error)
        self.assertEqual(direct.provider, OFFLINE_PROVIDER_NAME)
        self.assertEqual(response.provider_used, OFFLINE_PROVIDER_NAME)

    def test_03b_the_interception_is_fully_restored(self) -> None:
        """The interception is undone in ``finally`` and leaves no residue."""

        original_connect = socket.socket.connect
        original_create = socket.create_connection
        original_getaddrinfo = socket.getaddrinfo
        with _NetworkSpy() as spy:
            self.assertIsNot(socket.socket.connect, original_connect)
            self.assertIsNot(socket.create_connection, original_create)
            self.assertIsNot(socket.getaddrinfo, original_getaddrinfo)
            probe = socket.socket()
            try:
                probe.connect(("127.0.0.1", 9))
            except OSError:
                pass
            finally:
                probe.close()
            self.assertTrue(spy.attempts)

        self.assertIs(socket.socket.connect, original_connect)
        self.assertIs(socket.create_connection, original_create)
        self.assertIs(socket.getaddrinfo, original_getaddrinfo)

    def test_03c_the_interception_records_an_outbound_attempt(self) -> None:
        """A negative control: an outbound address IS observed and classified.

        The spy must not be vacuously silent, or every "zero attempts" assertion in
        this module would be meaningless. The probe resolves nothing: the guard
        records the address before the original call, and the original is expected
        to fail because nothing is listening on that reserved address.
        """

        with _NetworkSpy() as spy:
            try:
                socket.create_connection(("198.51.100.1", 9), timeout=0.05)
            except OSError:
                pass

        self.assertTrue(
            spy.attempts, "the network spy recorded nothing for an outbound create_connection"
        )
        self.assertTrue(spy.outbound)
        self.assertIn("198.51.100.1", spy.describe_outbound())

    # ------------------------------------------------------- 4. secrets in logs
    def test_04_no_secret_value_appears_in_captured_log_output(self) -> None:
        """No fake key value or key-shaped string reaches any log record.

        All log records emitted by the process during the workflow are captured at
        ``DEBUG`` on the root logger, so a record from any logger -- not only
        ``forge_ai`` -- is inspected. Each record is rendered with the same
        formatter the application configures, because a value that is safe in
        ``record.msg`` but interpolated into the formatted line would still be a
        leak. The record arguments are inspected as well, since a value passed as an
        argument and never formatted is still present in the record.
        """

        marker = uuid.uuid4().hex[:8]
        environ = self._fake_environment(marker)
        secrets = {name: environ[name] for name in LIVE_KEY_NAMES}

        records: list[logging.LogRecord] = []
        emitted_by: set[str] = set()

        class _Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)
                emitted_by.add(record.name)

        collector = _Collector(level=logging.DEBUG)
        root = logging.getLogger()
        previous_level = root.level
        previous_handlers = list(root.handlers)
        root.addHandler(collector)
        root.setLevel(logging.DEBUG)

        # A logger the workflow definitely touches, so `records` cannot be empty
        # merely because nothing happened to log.
        forge_logger = logging.getLogger("forge_ai")

        try:
            with patch.dict(os.environ, environ, clear=False):
                spy = _NetworkSpy()
                with spy:
                    spy.assert_seam_is_live()

                    from app.api.service import ForgeApiService

                    runtime = create_runtime()
                    service = ForgeApiService(runtime=runtime)
                    response = service.run_task(
                        TaskRunRequest(description="log hygiene probe")
                    )
                    self.assertTrue(response.success, response.error)

                    # Exercise the refusal path too: it is the one most likely to
                    # put a credential name or a provider message into a record.
                    refused = runtime.orchestrator.dispatch(
                        Task(id="log-probe-2", description="refusal probe"),
                        provider_name="openrouter",
                    )
                    self.assertFalse(refused.success)
                self.assertNoOutbound(spy)
        finally:
            root.removeHandler(collector)
            root.setLevel(previous_level)
            self.assertEqual(root.handlers, previous_handlers)

        # A forced record guarantees the collector is wired up, so an empty list
        # cannot be mistaken for "nothing was logged".
        forced = logging.LogRecord(
            name="forge_ai", level=logging.DEBUG, pathname=__file__, lineno=0,
            msg="collector self-check", args=(), exc_info=None,
        )
        collector.emit(forced)
        self.assertIn("forge_ai", emitted_by)
        self.assertIn(
            "collector self-check", "\n".join(r.getMessage() for r in records)
        )

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        rendered = "\n".join(
            f"{formatter.format(record)}\n{record.getMessage()}\n{record.args!r}"
            for record in records
        )
        for name, value in secrets.items():
            self.assertNotIn(
                value, rendered, f"the value of {name} appeared in captured log output"
            )
        # The variable *names* are configuration identifiers, not secrets, and a
        # diagnostic is allowed to name the variable a provider needs. What must
        # never appear is the value, so those are checked above and the key shape
        # is checked here -- even truncated, and even inside an argument tuple.
        self.assertNotIn("sk-forge-fake", rendered)
        self.assertNotIn(f"forge-fake-{marker}", rendered)

    def test_04b_a_leaked_secret_would_have_been_detected(self) -> None:
        """A positive control for the log check: the detector is not vacuous."""

        value = fake_key("control")
        records: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        collector = _Collector(level=logging.DEBUG)
        logger = logging.getLogger("forge_probe")
        logger.addHandler(collector)
        previous = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug("value is %s", value)
        finally:
            logger.removeHandler(collector)
            logger.setLevel(previous)

        rendered = "\n".join(record.getMessage() for record in records)
        self.assertIn(value, rendered)


if __name__ == "__main__":
    unittest.main()
