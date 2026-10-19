"""Regression tests for a hard Radox account failure (D1: a 402 did not disable the key).

Measured defect: all five accounts returned ``HTTP 402 {"code":"insufficient_quota"}``
while ``.forge/radox-keys.json`` kept ``last_error_code: 402`` and ``disabled_until:
null``, so the same dead account was reserved again on every rotation (``idx 0..4 err
402``). A 402/401/403 is not a temporary rate limit: the account itself cannot serve, so
it must leave the rotation for a cooldown, exactly as a 429 does.

No test here opens a socket: every key is a literal placeholder in an explicit
``SecretStore`` mapping and every ledger lives in a temporary directory.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.agents.providers.base import ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.radox import RadoxProvider
from app.agents.providers.radox_keys import (
    HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS,
    RADOX_KEY_ENV_VAR,
    RadoxKeyLedger,
)
from app.config.secrets import SecretStore

RADOX_PROVIDER_NAME = "radox"
MISSING_ENV_FILE = "missing-radox-hard-failure-env"


def _store(environ):
    return SecretStore(env_file=MISSING_ENV_FILE, environ=environ)


def _response(content="Radox reply", tokens_in=7, tokens_out=3):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model="claude-sonnet-5",
        usage=SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out),
    )


class FakeStatusError(Exception):
    """An SDK error carrying an HTTP status, as the real SDK does."""

    def __init__(self, message="", status_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers={})


class FakeSDK:
    """A stand-in SDK module: no class the adapter translates, so status is used."""

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


class RadoxHardAccountFailureTests(unittest.TestCase):
    """A 402/401/403 disables the account for an hour and the pool still works."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.state_path = Path(self._temporary.name) / "radox-keys.json"
        self.first = "placeholder-hard-first-111111"
        self.second = "placeholder-hard-second-222222"
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

    def _remaining_seconds(self, entry: dict) -> float:
        return (
            datetime.fromisoformat(entry["disabled_until"]).timestamp()
            - datetime.now(timezone.utc).timestamp()
        )

    def test_01_a_402_disables_the_account_in_the_ledger(self) -> None:
        def behaviour(key, **kwargs):
            if key == self.first:
                raise FakeStatusError(
                    '{"code":"insufficient_quota","type":"insufficient_funds"}',
                    status_code=402,
                )
            return _response()

        provider, sdk = self._provider(behaviour)
        result = provider.generate(ProviderRequest(prompt="hello"))

        self.assertEqual(result.output, "Radox reply")
        self.assertEqual(provider.last_key_index, 1)
        self.assertEqual(sdk.keys_used, [self.first, self.second])

        entry = self._ledger()["keys"]["0"]
        self.assertEqual(entry["last_error_code"], 402)
        self.assertIsNotNone(
            entry["disabled_until"],
            "a 402 left disabled_until null, so the dead account stays in rotation",
        )
        remaining = self._remaining_seconds(entry)
        self.assertGreater(remaining, HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS - 60)
        self.assertLessEqual(remaining, HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS)

    def test_02_candidates_skip_a_disabled_position_and_put_it_last(self) -> None:
        ledger = RadoxKeyLedger(state_path=self.state_path)
        ledger.record_failure(0, 402)

        # Two live positions first, the cooling one last: it is a last resort, not a
        # candidate while a healthy account can serve.
        self.assertEqual(ledger.candidates(0, 3), [1, 2, 0])
        self.assertFalse(ledger.every_key_cooling_down(3))

    def test_03_a_later_attempt_selects_the_healthy_key(self) -> None:
        def behaviour(key, **kwargs):
            if key == self.first:
                raise FakeStatusError("insufficient_quota", status_code=402)
            return _response()

        provider, sdk = self._provider(behaviour)
        provider.generate(ProviderRequest(prompt="first"))

        # A completely fresh instance (new process semantics) must not pick the dead
        # account even though the cursor wraps back to it.
        second_provider, second_sdk = self._provider(
            lambda key, **kwargs: _response()
        )
        second_provider.generate(ProviderRequest(prompt="second"))
        self.assertEqual(second_provider.last_key_index, 1)
        self.assertEqual(second_sdk.keys_used, [self.second])

    def test_04_401_and_403_are_hard_failures_too(self) -> None:
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                self.state_path.unlink(missing_ok=True)

                def behaviour(key, _status=status_code, **kwargs):
                    if key == self.first:
                        raise FakeStatusError("rejected", status_code=_status)
                    return _response()

                provider, _ = self._provider(behaviour)
                provider.generate(ProviderRequest(prompt="hello"))
                entry = self._ledger()["keys"]["0"]
                self.assertEqual(entry["last_error_code"], status_code)
                self.assertIsNotNone(entry["disabled_until"])
                self.assertGreater(
                    self._remaining_seconds(entry),
                    HARD_ACCOUNT_FAILURE_BACKOFF_SECONDS - 60,
                )

    def test_05_a_500_still_does_not_cool_down_the_account(self) -> None:
        def behaviour(key, **kwargs):
            raise FakeStatusError("server exploded", status_code=500)

        provider, sdk = self._provider(behaviour)
        with self.assertRaises(FakeStatusError):
            provider.generate(ProviderRequest(prompt="hello"))

        self.assertEqual(sdk.keys_used, [self.first])
        entry = self._ledger()["keys"]["0"]
        self.assertEqual(entry["last_error_code"], 500)
        self.assertIsNone(entry["disabled_until"])
        self.assertFalse(RadoxKeyLedger(state_path=self.state_path).every_key_cooling_down(2))

    def test_06_an_all_cooling_pool_is_still_attempted_and_says_so(self) -> None:
        def failing(key, **kwargs):
            raise FakeStatusError("insufficient_quota", status_code=402)

        provider, sdk = self._provider(failing)
        with self.assertRaises(FakeStatusError) as raised:
            provider.generate(ProviderRequest(prompt="hello"))

        # Both accounts were tried (min(len(keys), 3) is two here) rather than the
        # provider refusing with a "no keys available" error.
        self.assertEqual(sdk.keys_used, [self.first, self.second])
        message = str(raised.exception)
        self.assertIn("HTTP 402", message)
        self.assertIn("insufficient funds or quota", message)
        self.assertIn("cooling down", message)
        # No key material leaked into the message.
        self.assertNotIn(self.first, message)
        self.assertNotIn(self.second, message)

        ledger = RadoxKeyLedger(state_path=self.state_path)
        self.assertTrue(ledger.every_key_cooling_down(2))
        self.assertTrue(
            all(self._remaining_seconds(entry) > 0 for entry in self._ledger()["keys"].values())
        )

        # Last resort: the whole pool cooling does not make the provider unusable. A
        # healthy response from a cooling account is still served.
        recovered, recovered_sdk = self._provider(lambda key, **kwargs: _response())
        recovered.generate(ProviderRequest(prompt="after cooldown"))
        self.assertIn(recovered_sdk.keys_used[0], (self.first, self.second))
        self.assertIsNotNone(recovered.last_key_index)


if __name__ == "__main__":
    unittest.main()
