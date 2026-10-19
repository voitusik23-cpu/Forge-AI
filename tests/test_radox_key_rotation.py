"""Offline tests for the Radox multi-key pool, rotation, failover and ledger.

No test in this file opens a socket or reads a real ``.env``: every key is a literal
placeholder that exists only inside a ``SecretStore`` built over an explicit mapping,
and every ledger file is written to a temporary directory. The transport is a fake SDK
whose client records the key it was constructed with, which is how failover is observed
without ever contacting the provider.
"""

import hashlib
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.providers.base import (
    ProviderNotConfiguredError,
    ProviderRequest,
)
from app.agents.providers.config import ProviderConfig
from app.agents.providers.radox import RadoxProvider
from app.agents.providers.radox_keys import (
    DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    RADOX_KEY_ENV_VAR,
    RadoxKeyLedger,
    collect_keys,
    retry_after_seconds,
)
from app.config.secrets import SecretStore

RADOX_PROVIDER_NAME = "radox"

#: A path that cannot exist, so ``SecretStore`` never falls back to a real file.
MISSING_ENV_FILE = "missing-radox-rotation-env"

#: The exact message the adapter has always used for an unconfigured pool.
MISSING_KEY_MESSAGE = (
    f"Set {RADOX_KEY_ENV_VAR} in the local environment to use {RADOX_PROVIDER_NAME}"
)


def _store(environ):
    """A ``SecretStore`` over an explicit mapping, never over the real ``.env``."""

    return SecretStore(env_file=MISSING_ENV_FILE, environ=environ)


def _response(tokens_in=9, tokens_out=4, content="Radox reply", model="claude-sonnet-5"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out),
    )


class FakeStatusError(Exception):
    """An SDK error carrying the HTTP status and headers the adapter classifies."""

    def __init__(self, message="", status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers={} if retry_after is None else {"Retry-After": str(retry_after)},
        )


class FakeRateLimitError(FakeStatusError):
    """Stand-in for the SDK's 429 class (matched by name for error translation)."""

    def __init__(self, message="", retry_after=None):
        super().__init__(message, status_code=429, retry_after=retry_after)


class FakeAuthenticationError(FakeStatusError):
    """Stand-in for the SDK's 401 class (matched by name for error translation)."""

    def __init__(self, message=""):
        super().__init__(message, status_code=401)


class FakePermissionDeniedError(FakeStatusError):
    """Stand-in for the SDK's 403 class."""

    def __init__(self, message=""):
        super().__init__(message, status_code=403)


class FakeConnectionError(Exception):
    """A transport failure: no HTTP status at all."""


class FakeSDK:
    """A stand-in SDK module whose client records the key it was built with."""

    AuthenticationError = FakeAuthenticationError
    RateLimitError = FakeRateLimitError
    PermissionDeniedError = FakePermissionDeniedError
    APIConnectionError = FakeConnectionError
    APITimeoutError = FakeConnectionError

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.keys_used = []

    def OpenAI(self, **options):
        key = options.get("api_key")
        self.keys_used.append(key)

        def create(**kwargs):
            return self.behaviour(key, **kwargs)

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )


class RadoxKeyPoolTests(unittest.TestCase):
    """Which keys exist, in which order, and what happens when none exists."""

    def test_01_pool_order_dedupe_and_gaps(self) -> None:
        environ = {
            RADOX_KEY_ENV_VAR: "placeholder-base-000000",
            f"{RADOX_KEY_ENV_VAR}_1": "placeholder-one-111111",
            f"{RADOX_KEY_ENV_VAR}_2": "placeholder-one-111111",  # duplicate of _1
            f"{RADOX_KEY_ENV_VAR}_3": "placeholder-three-333333",
            f"{RADOX_KEY_ENV_VAR}_7": "placeholder-seven-777777",
        }
        self.assertEqual(
            collect_keys(_store(environ)),
            [
                "placeholder-base-000000",
                "placeholder-one-111111",
                "placeholder-three-333333",
                "placeholder-seven-777777",
            ],
        )

    def test_02_numbered_keys_without_the_base_key_are_a_pool(self) -> None:
        environ = {
            f"{RADOX_KEY_ENV_VAR}_2": "placeholder-two-222222",
            f"{RADOX_KEY_ENV_VAR}_1": "placeholder-one-111111",
        }
        self.assertEqual(
            collect_keys(_store(environ)),
            ["placeholder-one-111111", "placeholder-two-222222"],
        )

    def test_03_blank_values_are_not_keys(self) -> None:
        environ = {
            RADOX_KEY_ENV_VAR: "   ",
            f"{RADOX_KEY_ENV_VAR}_1": "",
            f"{RADOX_KEY_ENV_VAR}_2": "placeholder-two-222222",
        }
        self.assertEqual(collect_keys(_store(environ)), ["placeholder-two-222222"])

    def test_04_empty_pool_names_the_variable_and_never_a_value(self) -> None:
        sdk = FakeSDK(lambda key, **kwargs: _response())
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="claude-sonnet-5",
                api_key_env_var=RADOX_KEY_ENV_VAR,
            ),
            sdk_module=sdk,
            secret_store=_store({}),
            keys_state_path=self._state_path(),
        )

        with self.assertRaises(ProviderNotConfiguredError) as raised:
            provider.generate(ProviderRequest(prompt="offline"))

        self.assertEqual(str(raised.exception), MISSING_KEY_MESSAGE)
        self.assertIn(RADOX_KEY_ENV_VAR, str(raised.exception))
        # No transport was constructed for a pool that does not exist.
        self.assertEqual(sdk.keys_used, [])

    def _state_path(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / "radox-keys.json"


class RadoxRotationTests(unittest.TestCase):
    """Round-robin order, its persistence, and re-reading the pool each request."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.state_path = Path(self._temporary.name) / "radox-keys.json"
        self.keys = ("placeholder-a-aaaaaa", "placeholder-b-bbbbbb", "placeholder-c-cccccc")

    def _environ(self, keys=None) -> dict:
        values = {}
        for position, key in enumerate(self.keys if keys is None else keys, start=1):
            name = RADOX_KEY_ENV_VAR if position == 1 else f"{RADOX_KEY_ENV_VAR}_{position - 1}"
            values[name] = key
        return values

    def _provider(self, environ, behaviour=None, state_path=None):
        sdk = FakeSDK(behaviour or (lambda key, **kwargs: _response()))
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="claude-sonnet-5",
                api_key_env_var=RADOX_KEY_ENV_VAR,
            ),
            sdk_module=sdk,
            secret_store=_store(environ),
            keys_state_path=self.state_path if state_path is None else state_path,
        )
        return provider, sdk

    def test_05_round_robin_advances_and_the_cursor_persists(self) -> None:
        provider, sdk = self._provider(self._environ())

        served = []
        for _ in range(4):
            provider.generate(ProviderRequest(prompt="hello", context={"run_id": "run-1"}))
            served.append(provider.last_key_index)

        self.assertEqual(served, [0, 1, 2, 0])
        self.assertEqual(sdk.keys_used, [self.keys[0], self.keys[1], self.keys[2], self.keys[0]])

        ledger = json.loads(self.state_path.read_text(encoding="utf-8"))
        # Four reservations from 0 advance the cursor to 1, and the file says so.
        self.assertEqual(ledger["next_index"], 1)
        self.assertEqual([ledger["keys"][str(i)]["requests"] for i in range(3)], [2, 1, 1])

        # A *new* provider instance continues where the file left off: the advance is
        # durable, not an in-memory counter.
        second_provider, second_sdk = self._provider(self._environ())
        second_provider.generate(ProviderRequest(prompt="hello"))
        self.assertEqual(second_sdk.keys_used, [self.keys[1]])
        self.assertEqual(second_provider.last_key_index, 1)

    def test_06_the_pool_is_re_read_on_every_request(self) -> None:
        environ = self._environ()
        provider, sdk = self._provider(environ)

        provider.generate(ProviderRequest(prompt="first"))
        self.assertEqual(provider.last_key_index, 0)

        # The owner pastes another account into the same environment: the next
        # request sees four positions, without a restart and without a migration.
        environ[f"{RADOX_KEY_ENV_VAR}_3"] = "placeholder-d-dddddd"
        provider.generate(ProviderRequest(prompt="second"))
        self.assertEqual(provider.last_key_index, 1)
        self.assertEqual(collect_keys(_store(environ))[-1], "placeholder-d-dddddd")

    def test_07_single_key_keeps_the_single_call_path(self) -> None:
        provider, sdk = self._provider({RADOX_KEY_ENV_VAR: "placeholder-only-000000"})

        result = provider.generate(ProviderRequest(prompt="hello"))

        self.assertEqual(sdk.keys_used, ["placeholder-only-000000"])
        self.assertEqual(provider.last_key_index, 0)
        self.assertEqual(result.output, "Radox reply")
        self.assertEqual(result.usage.input_tokens, 9)
        self.assertEqual(result.usage.output_tokens, 4)

    def test_08_the_serving_index_is_tagged_with_the_run_it_belongs_to(self) -> None:
        provider, _ = self._provider(self._environ())

        provider.generate(ProviderRequest(prompt="hello", context={"run_id": "run-abc"}))
        self.assertEqual(provider.last_key_run_id, "run-abc")

        # A new request that never reaches a provider must not inherit the index.
        failing, _ = self._provider({}, state_path=self.state_path)
        with self.assertRaises(ProviderNotConfiguredError):
            failing.generate(ProviderRequest(prompt="hello", context={"run_id": "run-def"}))
        self.assertIsNone(failing.last_key_index)
        self.assertEqual(failing.last_key_run_id, "")


class RadoxFailoverTests(unittest.TestCase):
    """429/402/401/403 and transport failures move the request to the next key."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.state_path = Path(self._temporary.name) / "radox-keys.json"
        self.first = "placeholder-first-111111"
        self.second = "placeholder-second-222222"
        self.environ = {
            RADOX_KEY_ENV_VAR: self.first,
            f"{RADOX_KEY_ENV_VAR}_1": self.second,
        }

    def _provider(self, behaviour):
        sdk = FakeSDK(behaviour)
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="claude-sonnet-5",
                api_key_env_var=RADOX_KEY_ENV_VAR,
            ),
            sdk_module=sdk,
            secret_store=_store(self.environ),
            keys_state_path=self.state_path,
        )
        return provider, sdk

    def _ledger(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_09_rate_limit_moves_to_the_next_key_and_disables_the_first(self) -> None:
        def behaviour(key, **kwargs):
            if key == self.first:
                raise FakeRateLimitError("slow down", retry_after=120)
            return _response()

        provider, sdk = self._provider(behaviour)
        result = provider.generate(ProviderRequest(prompt="hello"))

        self.assertEqual(result.output, "Radox reply")
        self.assertEqual(provider.last_key_index, 1)
        self.assertEqual(sdk.keys_used, [self.first, self.second])

        ledger = self._ledger()
        first_entry = ledger["keys"]["0"]
        self.assertEqual(first_entry["last_error_code"], 429)
        self.assertEqual(first_entry["requests"], 1)
        self.assertIsNotNone(first_entry["disabled_until"])
        remaining = (
            datetime.fromisoformat(first_entry["disabled_until"]).timestamp()
            - datetime.now(timezone.utc).timestamp()
        )
        # ``Retry-After: 120`` is honoured rather than replaced by the default.
        self.assertGreater(remaining, 100)
        self.assertLess(remaining, 130)
        # The serving account was charged the tokens it reported.
        self.assertEqual(ledger["keys"]["1"]["requests"], 1)
        self.assertEqual(ledger["keys"]["1"]["tokens_in"], 9)
        self.assertEqual(ledger["keys"]["1"]["tokens_out"], 4)
        self.assertEqual(ledger["keys"]["1"]["last_error_code"], None)

    def test_10_a_disabled_key_is_skipped_while_a_live_one_can_serve(self) -> None:
        def behaviour(key, **kwargs):
            if key == self.first:
                raise FakeRateLimitError("slow down")
            return _response()

        provider, sdk = self._provider(behaviour)
        provider.generate(ProviderRequest(prompt="first"))  # 0 fails, 1 serves
        provider.generate(ProviderRequest(prompt="second"))  # cursor is 1
        provider.generate(ProviderRequest(prompt="third"))  # cursor wraps to 0: skipped

        self.assertEqual(
            sdk.keys_used, [self.first, self.second, self.second, self.second]
        )
        self.assertEqual(self._ledger()["keys"]["0"]["requests"], 1)

    def test_11_default_backoff_applies_without_a_retry_after_header(self) -> None:
        def behaviour(key, **kwargs):
            if key == self.first:
                raise FakeRateLimitError("slow down")  # no Retry-After
            return _response()

        provider, _ = self._provider(behaviour)
        provider.generate(ProviderRequest(prompt="hello"))

        entry = self._ledger()["keys"]["0"]
        remaining = (
            datetime.fromisoformat(entry["disabled_until"]).timestamp()
            - datetime.now(timezone.utc).timestamp()
        )
        self.assertGreater(remaining, DEFAULT_RATE_LIMIT_BACKOFF_SECONDS - 10)
        self.assertLess(remaining, DEFAULT_RATE_LIMIT_BACKOFF_SECONDS + 10)

    def test_12_insufficient_balance_and_bad_key_move_to_the_next_key(self) -> None:
        cases = (
            (FakeStatusError("insufficient balance", status_code=402), 402),
            (FakeAuthenticationError("bad key"), 401),
            (FakePermissionDeniedError("forbidden"), 403),
            (FakeConnectionError("connection refused"), None),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                self.state_path.unlink(missing_ok=True)

                def behaviour(key, _error=error, **kwargs):
                    if key == self.first:
                        raise _error
                    return _response()

                provider, sdk = self._provider(behaviour)
                result = provider.generate(ProviderRequest(prompt="hello"))
                self.assertEqual(result.output, "Radox reply")
                self.assertEqual(provider.last_key_index, 1)
                self.assertEqual(sdk.keys_used, [self.first, self.second])
                self.assertEqual(
                    self._ledger()["keys"]["0"]["last_error_code"], expected_code
                )

    def test_13_a_non_account_error_is_not_retried(self) -> None:
        def behaviour(key, **kwargs):
            raise FakeStatusError("server exploded", status_code=500)

        provider, sdk = self._provider(behaviour)
        with self.assertRaises(FakeStatusError):
            provider.generate(ProviderRequest(prompt="hello"))
        # One attempt only: a 500 is not a reason to spend another account.
        self.assertEqual(sdk.keys_used, [self.first])

    def test_14_all_keys_failing_raises_the_last_error_with_the_key_redacted(self) -> None:
        def behaviour(key, **kwargs):
            raise FakeStatusError(f"insufficient balance for key {key}", status_code=402)

        provider, sdk = self._provider(behaviour)
        with self.assertRaises(FakeStatusError) as raised:
            provider.generate(ProviderRequest(prompt="hello"))

        text = str(raised.exception)
        self.assertNotIn(self.first, text)
        self.assertNotIn(self.second, text)
        self.assertIn("[redacted]", text)
        # Verbatim apart from the credential: the last account's message is kept.
        self.assertIn("insufficient balance for key", text)
        # Both accounts were tried -- min(len(keys), 3) is two here.
        self.assertEqual(sdk.keys_used, [self.first, self.second])

    def test_15_attempts_are_capped_at_three(self) -> None:
        environ = {
            RADOX_KEY_ENV_VAR: "placeholder-k0-000000",
            f"{RADOX_KEY_ENV_VAR}_1": "placeholder-k1-111111",
            f"{RADOX_KEY_ENV_VAR}_2": "placeholder-k2-222222",
            f"{RADOX_KEY_ENV_VAR}_3": "placeholder-k3-333333",
            f"{RADOX_KEY_ENV_VAR}_4": "placeholder-k4-444444",
        }

        def behaviour(key, **kwargs):
            raise FakeStatusError("insufficient balance", status_code=402)

        sdk = FakeSDK(behaviour)
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="claude-sonnet-5",
                api_key_env_var=RADOX_KEY_ENV_VAR,
            ),
            sdk_module=sdk,
            secret_store=_store(environ),
            keys_state_path=self.state_path,
        )
        with self.assertRaises(FakeStatusError):
            provider.generate(ProviderRequest(prompt="hello"))
        self.assertEqual(len(sdk.keys_used), 3)


class RadoxLedgerTests(unittest.TestCase):
    """The ledger is index-only, concurrency-safe and free of key material."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.state_path = Path(self._temporary.name) / "radox-keys.json"

    def test_16_the_ledger_never_contains_a_key_or_its_hash(self) -> None:
        keys = ["placeholder-alpha-111", "placeholder-beta-222", "placeholder-gamma-333"]
        environ = {RADOX_KEY_ENV_VAR: keys[0]}
        for position, key in enumerate(keys[1:], start=1):
            environ[f"{RADOX_KEY_ENV_VAR}_{position}"] = key

        def behaviour(key, **kwargs):
            if key == keys[0]:
                raise FakeRateLimitError("slow down", retry_after=5)
            return _response()

        sdk = FakeSDK(behaviour)
        provider = RadoxProvider(
            ProviderConfig(
                provider_name=RADOX_PROVIDER_NAME,
                model_name="claude-sonnet-5",
                api_key_env_var=RADOX_KEY_ENV_VAR,
            ),
            sdk_module=sdk,
            secret_store=_store(environ),
            keys_state_path=self.state_path,
        )
        provider.generate(ProviderRequest(prompt="hello", context={"run_id": "run-1"}))

        raw = self.state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        for key in keys:
            self.assertNotIn(key, raw)
            self.assertNotIn(hashlib.sha256(key.encode("utf-8")).hexdigest(), raw)
            self.assertNotIn(key[:6], raw)
        self.assertEqual(
            set(data["keys"]["0"]),
            {
                "requests",
                "tokens_in",
                "tokens_out",
                "last_error_code",
                "last_used_at",
                "disabled_until",
            },
        )

    def test_17_two_threads_are_never_handed_the_same_rotation_slot(self) -> None:
        ledger = RadoxKeyLedger(state_path=self.state_path)
        rounds = 20
        for _ in range(rounds):
            results = []
            barrier = threading.Barrier(2)

            def worker() -> None:
                barrier.wait()
                results.append(ledger.reserve(3))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(len(results), 2)
            self.assertNotEqual(results[0], results[1])

        # 40 reservations from 0 leave the cursor at 40 % 3: no advance was lost.
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["next_index"], (rounds * 2) % 3)

    def test_18_report_exposes_counts_and_no_key_material(self) -> None:
        ledger = RadoxKeyLedger(state_path=self.state_path)
        ledger.reserve(2)  # cursor 0 -> 1
        ledger.record_success(0, 120, 45)
        ledger.record_failure(1, 429, 60)

        payload = ledger.report(2)

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["active_index"], 1)
        self.assertEqual([item["index"] for item in payload["keys"]], [0, 1])
        self.assertEqual(
            payload["keys"][0],
            {
                "index": 0,
                "requests": 1,
                "tokens_in": 120,
                "tokens_out": 45,
                "last_error_code": None,
                "disabled": False,
            },
        )
        self.assertTrue(payload["keys"][1]["disabled"])
        self.assertEqual(payload["keys"][1]["last_error_code"], 429)
        # An empty pool is still an answer, not an error.
        self.assertEqual(
            ledger.report(0), {"count": 0, "active_index": 0, "keys": []}
        )

    def test_19_a_corrupt_ledger_degrades_to_an_empty_one(self) -> None:
        self.state_path.write_text("{not json", encoding="utf-8")
        ledger = RadoxKeyLedger(state_path=self.state_path)

        self.assertEqual(ledger.reserve(2), 0)
        self.assertEqual(ledger.report(1)["count"], 1)
        # The next write replaces the corrupt document with a valid one.
        self.assertEqual(
            json.loads(self.state_path.read_text(encoding="utf-8"))["next_index"], 1
        )

    def test_20_retry_after_accepts_seconds_and_http_dates(self) -> None:
        self.assertEqual(retry_after_seconds("120"), 120.0)
        self.assertIsNone(retry_after_seconds(""))
        self.assertIsNone(retry_after_seconds("soon"))
        self.assertIsNone(retry_after_seconds(None))
        # A date in the past cannot disable an account: the floor is zero.
        self.assertEqual(retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)


class RadoxKeysEndpointTests(unittest.TestCase):
    """``GET /api/providers/radox/keys`` reports counters and no key material."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.state_path = Path(self._temporary.name) / "radox-keys.json"
        self.first = "placeholder-endpoint-alpha-111111"
        self.second = "placeholder-endpoint-beta-222222"

    def _payload(self):
        from fastapi.testclient import TestClient

        from app.api.server import create_api_app
        from app.api.service import ForgeApiService

        environ = {
            RADOX_KEY_ENV_VAR: self.first,
            f"{RADOX_KEY_ENV_VAR}_1": self.second,
        }
        ledger = RadoxKeyLedger(state_path=self.state_path)
        ledger.reserve(2)  # cursor 0 -> 1
        ledger.record_success(0, 120, 45)

        service = ForgeApiService.__new__(ForgeApiService)
        with patch.dict(
            os.environ, {"FORGE_RADOX_KEYS_PATH": str(self.state_path)}, clear=False
        ):
            with patch("app.config.secrets.SecretStore", lambda: _store(environ)):
                client = TestClient(create_api_app(service=service))
                response = client.get("/api/providers/radox/keys")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_21_payload_shape_counts_and_ledger(self) -> None:
        payload = self._payload()

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["active_index"], 1)
        self.assertEqual([item["index"] for item in payload["keys"]], [0, 1])
        self.assertEqual(
            set(payload["keys"][0]),
            {
                "index",
                "requests",
                "tokens_in",
                "tokens_out",
                "last_error_code",
                "disabled",
            },
        )
        self.assertEqual(payload["keys"][0]["requests"], 1)
        self.assertEqual(payload["keys"][0]["tokens_in"], 120)
        self.assertEqual(payload["keys"][0]["tokens_out"], 45)
        self.assertFalse(payload["keys"][0]["disabled"])
        self.assertEqual(payload["keys"][1]["requests"], 0)

    def test_22_no_key_value_prefix_or_hash_is_returned(self) -> None:
        payload = self._payload()
        blob = json.dumps(payload, ensure_ascii=False)

        for key in (self.first, self.second):
            self.assertNotIn(key, blob)
            self.assertNotIn(key[:8], blob)
            self.assertNotIn(hashlib.sha256(key.encode("utf-8")).hexdigest(), blob)
        for forbidden in ("api_key", "secret", "token_value", "key_prefix"):
            self.assertNotIn(forbidden, blob.lower())


class RadoxRunAttributionTests(unittest.TestCase):
    """The run record learns which pool position served it -- and nothing else."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def _service(self):
        from app.api.service import ForgeApiService

        provider = SimpleNamespace(last_key_index=2, last_key_run_id="run-api-served")
        registry = SimpleNamespace(
            get=lambda name: {"radox": provider}[name] if name == "radox" else None
        )
        service = ForgeApiService.__new__(ForgeApiService)
        service._runtime = SimpleNamespace(provider_registry=registry)
        return service

    def test_23_the_index_is_read_only_for_the_run_that_used_it(self) -> None:
        service = self._service()

        self.assertEqual(service._provider_key_index("radox", "run-api-served"), 2)
        self.assertIsNone(service._provider_key_index("radox", "run-api-other"))
        self.assertIsNone(service._provider_key_index("openrouter", "run-api-served"))
        self.assertIsNone(service._provider_key_index("", "run-api-served"))

    def test_24_the_index_is_merged_into_project_state_without_touching_other_keys(
        self,
    ) -> None:
        from app.runtime.run_store import RunStateSnapshot, RunStore

        store = RunStore(root=str(self.root))
        run_id = "run-api-abcdef12"
        store.write_state(
            RunStateSnapshot(
                run_id=run_id,
                task_id="task-1",
                state="COMPLETED",
                status="SUCCESS",
                attempt_number=1,
                completed_attempt_indexes=(0,),
                project_state={"code_write": {"status": "applied"}},
                event_count=0,
                updated_at="",
            )
        )

        service = self._service()
        service._record_provider_key_index(store, run_id, "task-1", "radox", 2)

        record = store.load(run_id)
        project_state = record.snapshot.project_state
        self.assertEqual(project_state["radox_key_index"], 2)
        # The neighbour record survives the merge.
        self.assertEqual(project_state["code_write"], {"status": "applied"})
        self.assertEqual(len(record.events), 1)
        self.assertIn("radox_key_index", json.dumps(record.summary(), default=str))


if __name__ == "__main__":
    unittest.main()
