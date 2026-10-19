"""Radox AI provider using its OpenAI-compatible Chat Completions API.

The owner runs the same service through several accounts, each with its own monthly
allowance, so this adapter rotates across a **key pool** instead of one credential:

* the pool is ``RADOX_API_KEY`` followed by ``RADOX_API_KEY_1`` .. ``RADOX_API_KEY_N``
  in ascending order, de-duplicated, re-read on every request;
* the rotation cursor lives in ``.forge/radox-keys.json``, so successive requests and
  successive runs advance instead of always spending the first account;
* a 429 (rate limit), 402 (insufficient balance), 401/403 (bad key) or a transport
  failure retries the *same* request on the next account, up to ``min(len(keys), 3)``
  attempts in total, and after that raises the last error verbatim with the key
  value redacted;
* per-position counters (requests, tokens, last error, cooldown) are written to the
  same ledger, which never contains a key, a hash of one, a prefix or a length.

With exactly one key the path is the same single call the other compatible adapters
make: one SDK call, the same model/usage handling, and the same error messages.
"""

from typing import Any, Optional

from app.agents.providers.base import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.agents.providers.openai_compatible import OpenAICompatibleProvider
from app.agents.providers.radox_catalogue import MODEL_IDS as RADOX_MODEL_IDS
from app.agents.providers.radox_keys import (
    MAX_FAILOVER_ATTEMPTS,
    RADOX_KEY_ENV_VAR,
    RadoxKeyLedger,
    collect_keys,
    retry_after_seconds,
)
from app.config.secrets import SecretStore

#: Status codes that mean "this account cannot serve the request, another one may".
FAILOVER_STATUS_CODES = frozenset({401, 402, 403, 429})

#: SDK error classes that describe the transport, not the account's quota. They are
#: matched by name because the adapter receives the SDK module, not a fixed import.
TRANSPORT_ERROR_CLASS_NAMES = ("APIConnectionError", "APITimeoutError")

#: What a redacted key value is replaced with in an error message.
REDACTED_PLACEHOLDER = "[redacted]"

#: What a failed account's HTTP status means, in the words the operator needs to act.
#: The status is stated explicitly because the translated SDK message ("Radox API request
#: failed") does not say *why*, and a spent balance is an owner action, not a retry.
ACCOUNT_STATUS_NOTES = {
    401: "the account credential was rejected (HTTP 401)",
    402: "the account has insufficient funds or quota (HTTP 402)",
    403: "the account credential was rejected (HTTP 403)",
    429: "the account is rate limited (HTTP 429)",
}


def _status_code_of(exc: BaseException) -> Optional[int]:
    """The HTTP status an SDK error carries, when it carries one."""

    value = getattr(exc, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    return None


def _is_failover_error(
    exc: BaseException, sdk: Any, status_code: Optional[int]
) -> bool:
    """Whether another account may legitimately serve the same request."""

    if status_code is not None and status_code in FAILOVER_STATUS_CODES:
        return True
    for class_name in TRANSPORT_ERROR_CLASS_NAMES:
        error_type = getattr(sdk, class_name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return True
    return False


def _retry_after_of(exc: BaseException) -> Optional[float]:
    """The ``Retry-After`` the provider sent with the failure, as seconds."""

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for name in ("retry-after", "Retry-After"):
        try:
            value = headers.get(name)
        except Exception:  # noqa: BLE001 - an unreadable header is simply absent
            return None
        if value is not None:
            return retry_after_seconds(value)
    return None


def _redacted(error: BaseException, secret: str) -> BaseException:
    """The same error with the key value replaced, never the key itself.

    A message that does not contain the key is returned untouched, which is what
    "raise the last error verbatim" means for every error that did not echo it.
    """

    if not isinstance(secret, str) or not secret:
        return error
    text = str(error)
    if secret not in text:
        return error
    redacted_text = text.replace(secret, REDACTED_PLACEHOLDER)
    try:
        return type(error)(redacted_text)
    except Exception:  # noqa: BLE001 - an SDK error type that refuses a plain message
        return ProviderError(redacted_text)


def _account_failure_note(
    status_code: Optional[int], every_key_cooling_down: bool, key_count: int
) -> str:
    """The operator-facing explanation appended to an exhausted-pool failure."""

    notes = []
    status_note = ACCOUNT_STATUS_NOTES.get(status_code) if status_code is not None else None
    if status_note:
        notes.append(status_note)
    if every_key_cooling_down:
        notes.append(
            f"all {key_count} Radox API key account(s) are cooling down after repeated failures"
        )
    return "; ".join(notes)


class RadoxProvider(OpenAICompatibleProvider):
    """Call the Radox AI models across a rotating pool of owner accounts.

    The owner keeps the credentials outside this repository's code: keys are resolved
    at call time from ``RADOX_API_KEY`` and its numbered siblings and are never stored,
    logged or reproduced in an error. A pool with no key therefore fails with the same
    honest message shape the other OpenAI-compatible adapters use, naming only the
    environment variable.
    """

    PROVIDER_NAME = "radox"
    API_KEY_ENV_VAR = RADOX_KEY_ENV_VAR
    BASE_URL = "https://radoxai.com/v1"

    #: The owner's curated active Radox AI set, in his order. The list is not
    #: written out here: it is derived from ``radox_catalogue.CURATED_MODEL_IDS``,
    #: the same tuple the model registry builds its rows from, so the provider
    #: and the registry cannot drift apart. The rest of the 33-model catalogue
    #: stays available as ``radox_catalogue.CATALOGUE`` (the archive) but is not
    #: advertised. ``tests/test_radox_provider.py`` still asserts the two against
    #: the catalogue independently, because a shared source that is itself wrong
    #: would otherwise agree with itself.
    MODEL_IDS = RADOX_MODEL_IDS

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        client: Any = None,
        sdk_module: Any = None,
        secret_store: Optional[SecretStore] = None,
        timeout: Optional[float] = None,
        keys_state_path: Optional[Any] = None,
    ) -> None:
        super().__init__(
            config,
            client=client,
            sdk_module=sdk_module,
            secret_store=secret_store,
            timeout=timeout,
        )
        self._keys_state_path = keys_state_path
        #: Which pool position served the last request, and the run that request
        #: belonged to. The run's durable record reads this pair so a served index
        #: cannot be attributed across runs.
        self.last_key_index: Optional[int] = None
        self.last_key_run_id = ""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Serve one request, failing over across the key pool on account errors."""

        api_key_env_var = self.config.api_key_env_var or self.API_KEY_ENV_VAR
        keys = collect_keys(self.secret_store, api_key_env_var)
        if not keys:
            raise ProviderNotConfiguredError(
                f"Set {api_key_env_var} in the local environment to use {self.PROVIDER_NAME}"
            )
        model_name = self._resolve_model_name(request)
        sdk = self._load_sdk()

        # Reset before any attempt so a failed run can never inherit the position a
        # previous run was served by.
        self.last_key_index = None
        self.last_key_run_id = ""

        ledger = RadoxKeyLedger(state_path=self._keys_state_path)
        start = ledger.reserve(len(keys))
        attempts = min(len(keys), MAX_FAILOVER_ATTEMPTS)
        candidates = ledger.candidates(start, len(keys))[:attempts]

        last_error: Optional[BaseException] = None
        last_status_code: Optional[int] = None
        last_key = keys[0]
        for index in candidates:
            api_key = keys[index]
            last_key = api_key
            try:
                response = self._perform_request(request, api_key, model_name, sdk)
            except Exception as exc:  # noqa: BLE001 - classified below, then re-raised
                status_code = _status_code_of(exc)
                last_status_code = status_code
                ledger.record_failure(index, status_code, _retry_after_of(exc))
                last_error = self._translated_error(exc, sdk)
                if not _is_failover_error(exc, sdk, status_code):
                    # Not an account problem: another account cannot serve this request.
                    raise _redacted(last_error, api_key) from None
                continue

            self.last_key_index = index
            self.last_key_run_id = self._run_id_of(request)
            usage = response.usage
            ledger.record_success(
                index,
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
            )
            return response

        # The pool could not serve the request. The error says which HTTP status failed
        # and, when the whole pool is in cooldown, that it is -- so a spent balance is
        # reported honestly instead of looking like a transient blip. A single-account
        # pool has no rotation to explain, so its message stays exactly as it was.
        if last_error is not None:
            note = (
                _account_failure_note(
                    last_status_code,
                    ledger.every_key_cooling_down(len(keys)),
                    len(keys),
                )
                if len(keys) > 1
                else ""
            )
            redacted = _redacted(last_error, last_key)
            if not note:
                raise redacted from None
            message = f"{redacted} ({note})"
            try:
                noted: BaseException = type(redacted)(message)
            except TypeError:  # an SDK error type that refuses the longer message
                noted = ProviderError(message)
            raise noted from None
        raise ProviderNotConfiguredError(
            f"Set {api_key_env_var} in the local environment to use {self.PROVIDER_NAME}"
        )

    def _translated_error(self, exc: Exception, sdk: Any) -> BaseException:
        """The safe error this failure is reported as, without raising it yet."""

        try:
            self._translate_sdk_error(exc, sdk)
        except ProviderError as translated:
            return translated
        return exc

    @staticmethod
    def _run_id_of(request: ProviderRequest) -> str:
        context = request.context if isinstance(request.context, dict) else {}
        run_id = context.get("run_id")
        return run_id if isinstance(run_id, str) else ""
