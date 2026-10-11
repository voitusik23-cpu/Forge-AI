"""The dashboard health monitor makes no outbound attempt in test mode.

The residual recorded in ``docs/SOURCE_OF_TRUTH.md`` §4.17 was measured, not
inferred: ``app/dashboard/provider_health.py`` opened real TCP connections to
``api.deepseek.com:443`` and ``openrouter.ai:443`` during a test run, because the
monitor is not part of the provider registry that ``create_runtime`` narrows.
This module tests the fix for that, and it tests **observed behaviour**: every
network claim is made by intercepting the process's own ``socket`` module, never
by mocking Forge's own code.

Five claims are pinned here:

1. ``test_01``: with a live socket spy installed, a test-mode monitor run
   produces **zero** connect attempts -- and the spy is proven armed in the same
   test, so the zero cannot be a broken interception.
2. ``test_02``: the same spy on a production-mode monitor **does** observe a
   probe attempt, so the zero in ``test_01`` means something. The spy refuses
   the connection instead of letting it reach the internet.
3. ``test_03``: a test-mode result reports ``NOT_CHECKED`` for every provider;
   it never reports a provider as ready or blocked, because nothing was checked.
4. ``test_04``: existing production-mode consumers still get the same result
   shape and the same semantics.
5. ``test_05``: the guarantee is reached through the real composition signal
   (``FORGE_ENV=test``), not only through a settings object this test injects.

Nothing here reads, prints, or requires a real credential. The production tests
install a synthetic, obviously-fake ``SecretStore`` so the probe attempt is
deterministic and independent of whether a developer's ``.env`` exists.
"""

from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from app.config.secrets import SecretStore
from app.config.settings import (
    OFFLINE_PROVIDER_NAME,
    ApplicationEnvironment,
    RuntimeSettings,
    load_settings,
)
from app.dashboard.models import AlertLevel, ProviderStatus
from app.dashboard.provider_health import ProviderHealthMonitor
from app.dashboard.service import CostDashboardService

#: Hosts the monitor would dial in production. Named so a failure message can
#: say which provider leaked an attempt.
_LIVE_HOSTS = ("api.deepseek.com", "openrouter.ai")

#: Addresses that are local by definition, so the "spy is armed" probe below can
#: never leave the machine even if the refusal wrapper were broken.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

#: The exact keys ``ProviderAccountStatus.to_dict`` produced before this change.
#: ``NOT_CHECKED`` is a new *value* of an existing key, never a new key, so this
#: set is both the pre-change and post-change contract.
_STATUS_PAYLOAD_KEYS = {
    "provider",
    "status",
    "balance_display",
    "balance_numeric",
    "quota_limit",
    "quota_usage",
    "alert",
    "last_checked",
    "details",
}

#: The exact keys ``DashboardReport.to_dict`` produced before this change.
_REPORT_PAYLOAD_KEYS = {
    "providers",
    "models",
    "agents",
    "projects",
    "runs",
    "today_totals",
    "all_time_totals",
    "generated_at",
}


class _BlockingSocketSpy:
    """Observe every outbound attempt and refuse it before it leaves the process.

    Installed as a context manager and restored in ``__exit__``, so it is
    restored on the failure path too. It patches the lowest seams a connect can
    go through:

    * ``socket.socket.connect`` and ``socket.socket.connect_ex`` -- refused, so
      the connect never completes;
    * ``socket.create_connection`` -- refused before it can call the above;
    * ``socket.getaddrinfo`` -- DNS is *not* performed; a synthetic reserved
      address is returned so a name lookup cannot itself be the network egress.

    Every wrapper appends to :attr:`attempts` **before** it refuses, so an
    attempt is recorded even though it can never succeed. That is what makes
    "the production monitor tries" and "the test monitor does not" measurable on
    the same seam.
    """

    #: A documentation-range address ``getaddrinfo`` hands back. Nothing ever
    #: connects to it: the connect seam refuses first.
    SYNTHETIC_ADDRESS = "203.0.113.1"

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []
        self._originals: list[tuple[object, str, object]] = []

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _host_of(address: object) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address) if address is not None else ""

    def _record(self, kind: str, address: object) -> None:
        self.attempts.append((kind, self._host_of(address)))

    @property
    def connect_attempts(self) -> list[tuple[str, str]]:
        """Attempts that were actual connects, not name lookups."""

        return [a for a in self.attempts if a[0] != "getaddrinfo"]

    def outbound_hosts(self) -> set:
        """Recorded hosts that are not local, i.e. would have left the machine."""

        return {host for _, host in self.attempts if host not in _LOCAL_HOSTS}

    def prove_armed(self) -> None:
        """Assert the seam is live by making a deliberate local connect attempt.

        Without this, a spy that was never installed -- or one whose patch was
        undone by an earlier test -- would report zero attempts and every
        assertion below would pass for the wrong reason. The address is local and
        the connection is refused by the wrapper, so no traffic leaves the
        machine.
        """

        before = len(self.attempts)
        probe = socket.socket()
        try:
            probe.settimeout(0.05)
            try:
                probe.connect(("127.0.0.1", 9))
            except OSError:
                pass
        finally:
            probe.close()
        observed = self.attempts[before:]
        if len(observed) != 1 or observed[0] != ("connect", "127.0.0.1"):
            raise AssertionError(
                "socket spy is not armed: a deliberate local connect attempt was "
                f"not recorded (observed {observed!r})"
            )

    # -- patched seams ---------------------------------------------------

    def _connect(self, sock: socket.socket, address: object):
        self._record("connect", address)
        raise ConnectionRefusedError(f"refused by test spy: {self._host_of(address)}")

    def _connect_ex(self, sock: socket.socket, address: object) -> int:
        self._record("connect_ex", address)
        return 111  # ECONNREFUSED, as connect_ex would return

    def _create_connection(self, address: object, *args: object, **kwargs: object):
        self._record("create_connection", address)
        raise ConnectionRefusedError(f"refused by test spy: {self._host_of(address)}")

    def _getaddrinfo(self, host: object, *args: object, **kwargs: object):
        self._record("getaddrinfo", host)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (self.SYNTHETIC_ADDRESS, 443),
            )
        ]

    # -- context manager -------------------------------------------------

    def __enter__(self) -> "_BlockingSocketSpy":
        # Plain functions, not bound methods. ``socket.socket`` is
        # ``_socket.socket``, a C type: assigning a *bound method* to it makes
        # attribute lookup return a builtin-style wrapper that does NOT bind the
        # instance, so the patched seam would be called without ``self`` and
        # raise TypeError. Each closure below supplies the instance explicitly,
        # which keeps this a genuine interception of the real seam.
        spy = self

        def connect(sock, address):
            return spy._connect(sock, address)

        def connect_ex(sock, address):
            return spy._connect_ex(sock, address)

        def create_connection(address, *args, **kwargs):
            return spy._create_connection(address, *args, **kwargs)

        def getaddrinfo(host, *args, **kwargs):
            return spy._getaddrinfo(host, *args, **kwargs)

        self._patch(socket.socket, "connect", connect)
        self._patch(socket.socket, "connect_ex", connect_ex)
        self._patch(socket, "create_connection", create_connection)
        self._patch(socket, "getaddrinfo", getaddrinfo)
        return self

    def _patch(self, target: object, name: str, replacement: object) -> None:
        self._originals.append((target, name, getattr(target, name)))
        setattr(target, name, replacement)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        while self._originals:
            target, name, original = self._originals.pop()
            setattr(target, name, original)
        return False


class _FakeSecretStore(SecretStore):
    """A store that answers with synthetic values and never touches disk.

    Used only by the production-mode tests so the probe attempt does not depend
    on whether the developer has a real ``.env``. The value is obviously fake
    and is never sent anywhere: the socket spy refuses the connection.
    """

    def __init__(self) -> None:
        super().__init__(environ={})

    def get_secret(self, name: str):  # type: ignore[override]
        if name.endswith("_API_KEY"):
            return "sk-forge-fake-not-a-real-credential"
        return None


def _test_settings() -> RuntimeSettings:
    """The settings a TEST composition resolves to.

    ``create_runtime(test_mode=True)`` -- and the ambient ``FORGE_ENV=test`` path
    -- narrows settings to exactly this shape: environment ``test``, the offline
    provider as the only enabled provider, no fallback chain, paid providers off.
    """

    return RuntimeSettings(
        environment=ApplicationEnvironment.TEST,
        default_provider=OFFLINE_PROVIDER_NAME,
        enabled_providers=(OFFLINE_PROVIDER_NAME,),
        provider_fallback_chain=(),
        allow_paid_providers=False,
    )


def _production_settings() -> RuntimeSettings:
    """The settings an unset ``FORGE_ENV`` resolves to: live probing enabled.

    This is literally ``RuntimeSettings()``, the default a bare
    ``load_settings()`` produces when ``FORGE_ENV`` is unset, so the test cannot
    accidentally describe a fictional production.
    """

    return RuntimeSettings()


class _SpyCase(unittest.TestCase):
    """Shared arrangement: install the spy, run a callable, prove it was armed.

    The spy must be live *while the callable runs*, and the originals must be
    restored afterwards -- asserted by object identity, not assumed.
    """

    def run_under_spy(self, action) -> _BlockingSocketSpy:
        original_connect = socket.socket.connect
        original_create_connection = socket.create_connection
        spy = _BlockingSocketSpy()
        try:
            with spy:
                spy.prove_armed()
                self.assertIsNot(socket.socket.connect, original_connect)
                self.assertIsNot(socket.create_connection, original_create_connection)
                self.assertTrue(spy.connect_attempts)
                self.assertEqual(len(spy.connect_attempts), 1)
                action()
        finally:
            self.assertIs(socket.socket.connect, original_connect)
            self.assertIs(socket.create_connection, original_create_connection)
        return spy


class HealthMonitorTestModeIsOfflineTests(_SpyCase):
    """Observed behaviour of the health monitor under a live socket spy."""

    # -- 1. test mode: zero attempts -------------------------------------

    def test_01_test_mode_monitor_makes_zero_connect_attempts(self) -> None:
        monitor = ProviderHealthMonitor(
            secret_store=_FakeSecretStore(), settings=_test_settings()
        )
        # Ask the monitor the question directly, so the zero is not merely "a
        # monitor that never had a reason to try".
        self.assertFalse(monitor.checks_enabled)

        captured: dict = {}

        def action() -> None:
            captured["statuses"] = monitor.check_all(force=True)
            captured["report"] = CostDashboardService(
                health_monitor=monitor
            ).get_report(force_refresh_health=True)

        spy = self.run_under_spy(action)

        # Only the deliberate armed-probe attempt exists; nothing else.
        self.assertEqual(spy.connect_attempts, [("connect", "127.0.0.1")])
        self.assertEqual(spy.outbound_hosts(), set())
        self.assertEqual(len(captured["statuses"]), 7)
        self.assertIsNotNone(captured["report"].generated_at)

    # -- 2. production mode: the same spy sees the attempt ---------------

    def test_02_production_mode_monitor_does_attempt_a_probe(self) -> None:
        monitor = ProviderHealthMonitor(
            secret_store=_FakeSecretStore(), settings=_production_settings()
        )
        self.assertTrue(monitor.checks_enabled)

        captured: dict = {}
        spy = self.run_under_spy(
            lambda: captured.update(statuses=monitor.check_all(force=True))
        )

        attempted_hosts = {
            host for _, host in spy.connect_attempts if host in _LIVE_HOSTS
        }
        self.assertEqual(
            attempted_hosts,
            set(_LIVE_HOSTS),
            msg=(
                "production mode must still probe live endpoints; recorded "
                f"connect attempts were {spy.connect_attempts!r}"
            ),
        )
        # The spy refused every attempt, so the monitor saw a connection error.
        # That is the honest production verdict for a refused connection.
        statuses = captured["statuses"]
        self.assertEqual(statuses["deepseek"].status, ProviderStatus.NETWORK_BLOCKED)
        self.assertEqual(statuses["openrouter"].status, ProviderStatus.NETWORK_BLOCKED)

    def test_02b_production_mode_is_the_default_when_forge_env_is_unset(self) -> None:
        """The default composition, not a hand-built one, still probes.

        ``ProviderHealthMonitor`` with no settings resolves the same settings a
        bare ``create_runtime()`` resolves. With ``FORGE_ENV`` unset those are
        ``development`` plus the default (live-provider-bearing) enabled set, so
        probing is on. This pins "production is unchanged" to the real default
        rather than to a settings object this test invented.
        """

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_ENV", None)
            resolved = load_settings()
            self.assertIsNot(resolved.environment, ApplicationEnvironment.TEST)
            self.assertTrue(
                any(name != OFFLINE_PROVIDER_NAME for name in resolved.enabled_providers),
                msg=f"unexpected default enabled_providers: {resolved.enabled_providers!r}",
            )
            self.assertTrue(
                ProviderHealthMonitor(secret_store=_FakeSecretStore()).checks_enabled
            )

    # -- 3. the unchecked state is explicit ------------------------------

    def test_03_test_mode_never_reports_a_provider_as_verified(self) -> None:
        monitor = ProviderHealthMonitor(
            secret_store=_FakeSecretStore(), settings=_test_settings()
        )
        statuses = monitor.check_all(force=True)

        fabricated = {
            ProviderStatus.READY,
            ProviderStatus.READY_GATEWAY,
            ProviderStatus.FREE_TIER,
            ProviderStatus.NO_CREDITS,
            ProviderStatus.NETWORK_BLOCKED,
            ProviderStatus.AUTH_FAILED,
            ProviderStatus.UNAVAILABLE,
        }
        for name, status in statuses.items():
            self.assertEqual(
                status.status,
                ProviderStatus.NOT_CHECKED,
                msg=f"{name} reported {status.status.value} without being checked",
            )
            self.assertNotIn(status.status, fabricated)
            self.assertEqual(status.balance_display, "NOT CHECKED")
            self.assertIsNone(status.balance_numeric)
            self.assertEqual(status.alert, AlertLevel.INFO)
            # No observation happened, so no observation time is claimed.
            self.assertEqual(status.last_checked, "")
            self.assertEqual(status.details["reason"], "test_mode_no_network_probe")
            # The serialized form carries the distinction, and no extra keys, so
            # an existing JSON consumer keeps working.
            payload = status.to_dict()
            self.assertEqual(payload["status"], "NOT_CHECKED")
            self.assertEqual(set(payload), _STATUS_PAYLOAD_KEYS)

    # -- 4. production consumers keep the same shape and semantics -------

    def test_04_production_result_shape_and_semantics_are_unchanged(self) -> None:
        monitor = ProviderHealthMonitor(
            secret_store=_FakeSecretStore(), settings=_production_settings()
        )

        captured: dict = {}
        spy = self.run_under_spy(
            lambda: captured.update(statuses=monitor.check_all(force=True))
        )
        self.assertIn("api.deepseek.com", {h for _, h in spy.connect_attempts})

        statuses = captured["statuses"]
        # Same key set as before the change.
        self.assertEqual(
            set(statuses),
            {
                "deepseek",
                "openrouter",
                "gemini",
                "openai",
                "xai",
                "groq",
                "anthropic",
            },
        )
        # A refused connection is reported exactly as the pre-change code
        # reported it: NETWORK_BLOCKED / UNKNOWN / WARNING, with the exception
        # type recorded and a real observation timestamp. `urllib` wraps the
        # refusal in `URLError`, which is the value the pre-change code
        # recorded for a refused connection too.
        deepseek = statuses["deepseek"]
        self.assertEqual(deepseek.status, ProviderStatus.NETWORK_BLOCKED)
        self.assertEqual(deepseek.balance_display, "UNKNOWN")
        self.assertEqual(deepseek.alert, AlertLevel.WARNING)
        self.assertEqual(deepseek.details["error"], "URLError")
        self.assertTrue(deepseek.last_checked)

        payload = deepseek.to_dict()
        self.assertEqual(set(payload), _STATUS_PAYLOAD_KEYS)
        # The pre-change serialized contract, for this case.
        self.assertEqual(payload["status"], "NETWORK_BLOCKED")
        self.assertEqual(payload["alert"], "YELLOW")
        self.assertIsNone(payload["balance_numeric"])

        # Providers that do not probe still report their historical verdicts.
        self.assertEqual(statuses["gemini"].status, ProviderStatus.FREE_TIER)
        self.assertEqual(statuses["openai"].status, ProviderStatus.NO_CREDITS)
        self.assertEqual(statuses["groq"].status, ProviderStatus.NETWORK_BLOCKED)

        # The dashboard service over these statuses produces the same read-model
        # shape it always did.
        report = CostDashboardService(health_monitor=monitor).get_report(
            force_refresh_health=True
        )
        report_payload = report.to_dict()
        self.assertEqual(set(report_payload), _REPORT_PAYLOAD_KEYS)
        account_statuses = {
            entry["account_status"]["provider"]: entry["account_status"]["status"]
            for entry in report_payload["providers"]
        }
        self.assertNotIn("NOT_CHECKED", set(account_statuses.values()))

    # -- 5. the state is reachable through the real composition signal ----

    def test_05_env_selected_test_mode_suppresses_probes_without_settings_argument(
        self,
    ) -> None:
        """The resolution path, not just an injected settings object.

        ``FORGE_ENV=test`` is the pre-existing switch. A monitor built with no
        settings must resolve it and stop probing, so the guarantee does not
        depend on a caller remembering to pass ``settings=``.
        """

        captured: dict = {}

        def action() -> None:
            monitor = ProviderHealthMonitor(secret_store=_FakeSecretStore())
            captured["monitor"] = monitor
            captured["statuses"] = monitor.check_all(force=True)

        with patch.dict(os.environ, {"FORGE_ENV": "test"}, clear=False):
            spy = self.run_under_spy(action)
            self.assertFalse(captured["monitor"].checks_enabled)

        self.assertEqual(spy.connect_attempts, [("connect", "127.0.0.1")])
        self.assertEqual(spy.outbound_hosts(), set())
        self.assertTrue(
            all(s.status is ProviderStatus.NOT_CHECKED for s in captured["statuses"].values())
        )

    # -- 6. the behavioural regression test -------------------------------

    def test_06_regression_env_selected_test_mode_makes_zero_outbound_attempts(
        self,
    ) -> None:
        """The fails-before / passes-after test.

        This method deliberately touches **only** the pre-existing public
        surface: ``ProviderHealthMonitor()`` with no new keyword, and
        ``check_provider()``. It does not read ``checks_enabled`` and does not
        assert ``NOT_CHECKED``, so on the pre-fix source it does not error out
        with a missing attribute -- it fails for the real reason, because the
        monitor dialled ``api.deepseek.com`` under ``FORGE_ENV=test``.

        The host does not resolve: the spy intercepts ``getaddrinfo``, so no name
        lookup or connection can leave the machine whether the source is pre-fix
        or post-fix.
        """

        captured: dict = {}

        def action() -> None:
            monitor = ProviderHealthMonitor(secret_store=_FakeSecretStore())
            captured["status"] = monitor.check_provider("deepseek")

        with patch.dict(os.environ, {"FORGE_ENV": "test"}, clear=False):
            spy = self.run_under_spy(action)

        outbound = [a for a in spy.attempts if a[1] not in _LOCAL_HOSTS]
        self.assertEqual(
            outbound,
            [],
            msg=(
                "a test-mode health monitor opened an outbound connection: "
                f"{outbound!r}; the unchecked verdict was "
                f"{getattr(captured.get('status'), 'status', None)!r}"
            ),
        )
        self.assertEqual(spy.connect_attempts, [("connect", "127.0.0.1")])


if __name__ == "__main__":
    unittest.main()
