"""Provider balance transparency: sources, semantics, honesty and no leaks.

Nothing here touches the network. The official endpoints are exercised through parsers directly
and through a patched ``urlopen``, so the suite stays offline and deterministic while still
proving what the layer does with a real provider payload.

The central claims:

1. ``granted_balance``, ``topped_up_balance`` and ``total_balance`` are **three different things**
   (measured against DeepSeek's real answer for the owner's account) and are never merged.
2. an absent figure is ``None``, never zero; a provider without a balance endpoint reports that,
   with a reason and no money at all;
3. money is integer minor units, never a binary float;
4. the API key is used for the request and never appears in a report;
5. a failed refresh keeps the last confirmed value, marked stale with its age;
6. a TEST composition performs no outbound attempt.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import unittest
from unittest.mock import patch

from app.config.secrets import SecretStore
from app.dashboard.provider_balance import (
    BALANCE_PAYLOAD_KEYS,
    STATUS_NOT_CHECKED,
    STATUS_OBSERVED,
    STATUS_UNAUTHORIZED,
    STATUS_UNREACHABLE,
    STATUS_UNSUPPORTED,
    ProviderBalanceProbe,
    parse_deepseek_balance,
    parse_openrouter_key,
    unsupported_report,
)

#: The shape DeepSeek's documented "Query Balance" endpoint returns, with the values that were
#: actually observed for the owner's account (granted credit and topped-up credit differ).
DEEPSEEK_REAL = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "USD",
            "total_balance": "19.29",
            "granted_balance": "18.00",
            "topped_up_balance": "1.29",
        }
    ],
}

FAKE_KEY = "test-key-not-a-real-secret"


class _FakeSecretStore:
    """Just enough of the secret store: the probe only ever calls ``get_secret``."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def get_secret(self, name: str):  # noqa: D102 - test double
        return self._values.get(name)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _SocketSpy:
    """Record and refuse outbound connects, so "no attempt" is measurable."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []

    def __enter__(self) -> "_SocketSpy":
        self._original = socket.socket.connect

        def refuse(sock_self, address, *args, **kwargs):  # noqa: ANN001
            host = address[0] if isinstance(address, tuple) else str(address)
            self.attempts.append(("connect", str(host)))
            raise OSError("refused by the test spy")

        socket.socket.connect = refuse  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> bool:
        socket.socket.connect = self._original  # type: ignore[assignment]
        return False

    def outbound(self) -> list:
        return [a for a in self.attempts if a[1] not in {"127.0.0.1", "::1", "localhost", ""}]


class DeepSeekSemanticsTests(unittest.TestCase):
    def test_01_the_three_amounts_stay_separate(self) -> None:
        report = parse_deepseek_balance(DEEPSEEK_REAL, observed_at="2026-10-15T13:15:33+00:00")
        self.assertEqual(report.available_minor, 1929)
        self.assertEqual(report.granted_minor, 1800)
        self.assertEqual(report.topped_up_minor, 129)
        # Not interchangeable: granted credit is promotional, topped-up credit was paid in.
        self.assertNotEqual(report.granted_minor, report.topped_up_minor)
        self.assertEqual(
            report.available_minor, (report.granted_minor or 0) + (report.topped_up_minor or 0)
        )
        self.assertEqual(report.currency, "USD")
        self.assertEqual(report.connection_status, STATUS_OBSERVED)
        self.assertEqual(report.source, "GET https://api.deepseek.com/user/balance")
        self.assertFalse(report.stale)

    def test_02_money_is_integer_minor_units_not_float(self) -> None:
        report = parse_deepseek_balance(DEEPSEEK_REAL, observed_at="t")
        for value in (report.available_minor, report.granted_minor, report.topped_up_minor):
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, float)

    def test_03_a_missing_amount_is_unknown_not_zero(self) -> None:
        payload = {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "5.00"}],
        }
        report = parse_deepseek_balance(payload, observed_at="t")
        self.assertEqual(report.available_minor, 500)
        self.assertIsNone(report.granted_minor, "an absent granted credit must not become zero")
        self.assertIsNone(report.topped_up_minor)
        self.assertIsNone(report.usage_minor)

    def test_04_no_balance_infos_is_not_zero(self) -> None:
        report = parse_deepseek_balance({"is_available": False, "balance_infos": []}, observed_at="t")
        self.assertIsNone(report.available_minor)
        self.assertTrue(report.error, "an unavailable account must carry a reason")

    def test_05_openrouter_keeps_usage_and_does_not_invent_granted_credit(self) -> None:
        report = parse_openrouter_key(
            {"data": {"limit": 10.0, "limit_remaining": 7.5, "usage": 2.5, "is_free_tier": False}},
            observed_at="t",
        )
        self.assertEqual(report.available_minor, 750)
        self.assertEqual(report.usage_minor, 250)
        self.assertIsNone(report.granted_minor, "the limit must not be relabelled as granted credit")
        self.assertEqual(report.currency, "USD")

    def test_06_openrouter_derives_remaining_from_limit_and_usage(self) -> None:
        report = parse_openrouter_key(
            {"data": {"limit": 10.0, "usage": 4.0}}, observed_at="t"
        )
        self.assertEqual(report.available_minor, 600)


class UnsupportedProviderTests(unittest.TestCase):
    def test_07_a_provider_without_an_endpoint_reports_that_and_no_money(self) -> None:
        for name in ("openai", "anthropic", "gemini", "xai", "groq"):
            report = unsupported_report(name)
            self.assertFalse(report.supported, name)
            self.assertEqual(report.connection_status, STATUS_UNSUPPORTED, name)
            self.assertTrue(report.error, name)
            self.assertIn(name, report.error.lower(), name)
            for field in ("available_minor", "granted_minor", "topped_up_minor", "usage_minor"):
                self.assertIsNone(getattr(report, field), f"{name}.{field}")

    def test_08_the_probe_itself_returns_unsupported_without_a_key(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({}))
        report = probe.report("openai")
        self.assertFalse(report.supported)
        self.assertIsNone(report.available_minor)


class ReportContractTests(unittest.TestCase):
    def test_09_the_payload_key_set_is_exactly_the_declared_one(self) -> None:
        payload = parse_deepseek_balance(DEEPSEEK_REAL, observed_at="t").to_dict()
        self.assertEqual(set(payload), set(BALANCE_PAYLOAD_KEYS))

    def test_10_a_report_never_contains_the_api_key(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))
        seen_headers: dict = {}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            seen_headers.update(dict(request.headers))
            return _FakeResponse(DEEPSEEK_REAL)

        with patch("urllib.request.urlopen", fake_urlopen):
            report = probe.report("deepseek")

        serialized = json.dumps(report.to_dict())
        self.assertNotIn(FAKE_KEY, serialized, "the key must never be serialized into a report")
        self.assertEqual(report.available_minor, 1929)
        # It was used, for the request only.
        authorization = seen_headers.get("Authorization") or seen_headers.get("authorization") or ""
        self.assertIn(FAKE_KEY, authorization)

    def test_11_a_failed_refresh_keeps_the_last_confirmed_value_with_its_age(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))

        with patch("urllib.request.urlopen", lambda *a, **k: _FakeResponse(DEEPSEEK_REAL)):
            first = probe.report("deepseek")
        self.assertEqual(first.available_minor, 1929)
        self.assertFalse(first.stale)

        def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
            raise OSError("network down")

        with patch("urllib.request.urlopen", refuse):
            second = probe.report("deepseek")

        self.assertTrue(second.stale, "a failed refresh must be marked stale")
        self.assertEqual(second.available_minor, 1929, "the last confirmed value must survive")
        self.assertEqual(second.connection_status, STATUS_UNREACHABLE)
        self.assertTrue(second.error)
        self.assertIsNotNone(second.age_seconds, "a stale value must carry its age")
        self.assertEqual(second.last_updated, first.last_updated)

    def test_12_an_unauthorized_answer_is_reported_without_a_body_or_a_key(self) -> None:
        import urllib.error

        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))

        def unauthorized(*args, **kwargs):  # noqa: ANN002, ANN003
            raise urllib.error.HTTPError(
                "https://api.deepseek.com/user/balance", 401, "Unauthorized", {}, None
            )

        with patch("urllib.request.urlopen", unauthorized):
            report = probe.report("deepseek")

        self.assertEqual(report.connection_status, STATUS_UNAUTHORIZED)
        self.assertIn("401", report.error)
        self.assertNotIn(FAKE_KEY, json.dumps(report.to_dict()))

    def test_13_history_records_each_attempt_in_this_process(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))
        with patch("urllib.request.urlopen", lambda *a, **k: _FakeResponse(DEEPSEEK_REAL)):
            probe.report("deepseek")
        probe.report("openai")
        history = probe.history("deepseek")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], STATUS_OBSERVED)
        self.assertEqual(history[0]["available_minor"], 1929)
        unsupported_history = probe.history("openai")
        self.assertEqual(unsupported_history[0]["status"], STATUS_UNSUPPORTED)


class TestCompositionTests(unittest.TestCase):
    def test_14_a_test_composition_makes_no_outbound_attempt(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))
        with patch.dict(os.environ, {"FORGE_ENV": "test"}, clear=False):
            probe = ProviderBalanceProbe(
                secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY})
            )
            with _SocketSpy() as spy:
                report = probe.report("deepseek")

        self.assertFalse(probe.checks_enabled)
        self.assertEqual(spy.outbound(), [], "a test composition must not dial a provider")
        self.assertEqual(report.connection_status, STATUS_NOT_CHECKED)
        self.assertIsNone(report.available_minor)
        self.assertTrue(report.error)

    def test_15_the_route_and_the_service_method_exist(self) -> None:
        server = pathlib.Path("app/api/server.py").read_text(encoding="utf-8")
        self.assertIn('/api/providers/balance', server)
        service = pathlib.Path("app/api/service.py").read_text(encoding="utf-8")
        self.assertIn("def provider_balance(", service)

    def test_16_the_service_returns_unsupported_without_network(self) -> None:
        from app.api.service import ForgeApiService

        payload = ForgeApiService().provider_balance("openai")
        entry = payload["providers"]["openai"]
        self.assertFalse(entry["supported"])
        self.assertIsNone(entry["available_minor"])
        self.assertEqual(entry["connection_status"], STATUS_UNSUPPORTED)
        self.assertIn("history_scope", payload)
        self.assertEqual(set(entry), set(BALANCE_PAYLOAD_KEYS))


class FailureStateTests(unittest.TestCase):
    """The matrix the owner asked for: every way a refresh can fail, each with its own state."""

    def _probe(self) -> ProviderBalanceProbe:
        return ProviderBalanceProbe(secret_store=_FakeSecretStore({"DEEPSEEK_API_KEY": FAKE_KEY}))

    def test_17_no_key_makes_no_request(self) -> None:
        probe = ProviderBalanceProbe(secret_store=_FakeSecretStore({}))
        calls: list = []

        def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
            calls.append(args)
            raise AssertionError("no request may be made without a key")

        with patch("urllib.request.urlopen", refuse):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, STATUS_UNAUTHORIZED)
        self.assertEqual(calls, [])
        self.assertIn("no API key", report.error)

    def test_18_http_403_is_unauthorized(self) -> None:
        import urllib.error

        probe = self._probe()

        def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
            raise urllib.error.HTTPError("https://api.deepseek.com/user/balance", 403, "Forbidden", {}, None)

        with patch("urllib.request.urlopen", forbidden):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, STATUS_UNAUTHORIZED)
        self.assertIn("403", report.error)
        self.assertIsNone(report.available_minor)

    def test_19_http_5xx_is_an_error_not_unauthorized(self) -> None:
        import urllib.error

        probe = self._probe()

        def broken(*args, **kwargs):  # noqa: ANN002, ANN003
            raise urllib.error.HTTPError("https://api.deepseek.com/user/balance", 503, "Unavailable", {}, None)

        with patch("urllib.request.urlopen", broken):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, "ERROR")
        self.assertIn("503", report.error)

    def test_20_a_timeout_is_unreachable(self) -> None:
        probe = self._probe()
        with patch("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError())):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, STATUS_UNREACHABLE)
        self.assertIn("TimeoutError", report.error)

    def test_21_a_non_json_body_is_malformed_not_a_network_failure(self) -> None:
        class _NotJson:
            def read(self) -> bytes:
                return b"<html>gateway error: some sensitive detail</html>"

            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        probe = self._probe()
        with patch("urllib.request.urlopen", lambda *a, **k: _NotJson()):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, "MALFORMED")
        # The body is never quoted or logged.
        self.assertNotIn("sensitive", report.error)
        self.assertNotIn("gateway error", report.error)

    def test_22_a_json_body_of_the_wrong_shape_is_malformed(self) -> None:
        probe = self._probe()
        with patch("urllib.request.urlopen", lambda *a, **k: _FakeResponse(["not", "an", "object"])):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, "MALFORMED")
        self.assertTrue(report.error)

    def test_23_a_json_object_without_documented_fields_is_malformed(self) -> None:
        probe = self._probe()
        with patch("urllib.request.urlopen", lambda *a, **k: _FakeResponse({"unexpected": 1})):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, "MALFORMED")
        self.assertIsNone(report.available_minor)

    def test_24_an_answer_without_any_amount_is_incomplete(self) -> None:
        probe = self._probe()
        with patch(
            "urllib.request.urlopen",
            lambda *a, **k: _FakeResponse({"is_available": True, "balance_infos": []}),
        ):
            report = probe.report("deepseek")
        self.assertEqual(report.connection_status, "MALFORMED")
        self.assertIn("no amount", report.error)

    def test_25_tls_verification_is_never_disabled(self) -> None:
        source = pathlib.Path("app/dashboard/provider_balance.py").read_text(encoding="utf-8")
        for forbidden in (
            "_create_unverified_context",
            "verify=False",
            "CERT_NONE",
            "check_hostname = False",
            "ssl._create_default_https_context =",
        ):
            self.assertNotIn(forbidden, source, f"{forbidden} must never appear in the balance probe")
        # Every endpoint is https.
        from app.dashboard.provider_balance import SUPPORTED_SOURCES

        for url in SUPPORTED_SOURCES.values():
            self.assertTrue(url.endswith("/") is False)
            self.assertIn("https://", url)

    def test_26_a_stale_report_explains_what_is_known_and_when(self) -> None:
        probe = self._probe()
        with patch("urllib.request.urlopen", lambda *a, **k: _FakeResponse(DEEPSEEK_REAL)):
            probe.report("deepseek")
        with patch("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))):
            stale = probe.report("deepseek")
        self.assertTrue(stale.stale)
        payload = stale.to_dict()
        self.assertIsNotNone(payload["last_updated"], "a stale value must carry when it was obtained")
        self.assertIsNotNone(payload["age_seconds"])
        self.assertTrue(payload["error"])
        self.assertEqual(set(payload), set(BALANCE_PAYLOAD_KEYS))


if __name__ == "__main__":
    unittest.main()
