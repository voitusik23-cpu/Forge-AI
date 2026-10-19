"""Per-provider availability states, derived in exactly one place.

Why a separate vocabulary exists
--------------------------------
``/api/models`` used to report only *registry membership*, and the interface could
easily read "it is listed" as "it is available". Those are different facts:

============  ==========================================================================
State         Meaning, and the evidence it rests on
============  ==========================================================================
REGISTERED    present in the model registry, and nothing further could be established
              because the composition's enabled-provider set is not readable here
DISABLED      the provider is **not** in the composition's ``enabled_providers``
NO_CREDENTIALS  its key environment variable is absent or empty (only presence is read)
READY         enabled, credentials satisfied, and the last probe reported access
NOT_CHECKED   enabled, credentials satisfied, and no probe has been observed
NO_CREDITS    the last probe reported ``insufficient_quota`` / an HTTP 402
NETWORK_BLOCKED  the last probe failed to reach the provider
ERROR         the last probe failed for any other reason
============  ==========================================================================

Precedence, highest first, is the order of the checks in
:func:`derive_provider_state`: a provider that the operator switched off is
reported as ``DISABLED`` even when its key is also missing, because "you turned
this off" is the actionable fact; a provider whose key is missing is
``NO_CREDENTIALS`` even when an old probe result exists, because the key state
can change at any moment and a stale verdict must not outrank it. ``READY`` is
only ever produced from a probe that actually reported access -- nothing is
called ready because it happens to be registered.

A provider that declares **no** key variable (the offline ``mock`` provider) needs
nothing external, so it is reported ``READY`` when enabled; that is the
registry's own declaration, not an assumption made here.

Secrets
-------
This module never receives a key value. It is told whether a credential is
*present* (one boolean), and every reason it emits is a fixed literal. No key
value, length, prefix or hash can appear in its output, and none of the values it
handles are environment values.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional

__all__ = [
    "ProviderState",
    "PROVIDER_STATES",
    "ProviderStateDecision",
    "derive_provider_state",
    "lookup_probe",
    "provider_state_counts",
    "PROVIDER_ID_ALIASES",
]


class ProviderState(str, Enum):
    """What is known about one provider, as one closed vocabulary."""

    REGISTERED = "REGISTERED"
    READY = "READY"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    NO_CREDITS = "NO_CREDITS"
    NOT_CHECKED = "NOT_CHECKED"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


#: Every state, in the order the interface documents them. Exported so a caller
#: (and a test) can assert the vocabulary without copying it.
PROVIDER_STATES = tuple(state.value for state in ProviderState)

#: Reason codes. Fixed literals: the interface translates them, and a code can
#: therefore never carry a provider's answer, a key, or a path.
REASON_ENABLED_SET_UNKNOWN = "enabled_set_unknown"
REASON_DISABLED = "not_in_enabled_providers"
REASON_NO_CREDENTIALS = "credential_env_var_absent"
REASON_NO_CREDENTIAL_REQUIRED = "credential_not_required"
REASON_NOT_PROBED = "not_probed"
REASON_PROBE_READY = "probe_ready"
REASON_PROBE_NO_CREDITS = "probe_insufficient_quota"
REASON_PROBE_NETWORK_BLOCKED = "probe_network_failed"
REASON_PROBE_ERROR = "probe_failed"
REASON_PROBE_NOT_CHECKED = "probe_not_checked"
REASON_PROBE_UNRECOGNISED = "probe_status_unrecognised"

#: Probe vocabularies this module maps from. Two already exist in the codebase:
#: ``app.dashboard.models.ProviderStatus`` (the account health monitor) and the
#: ``connection_status`` values of ``app.dashboard.provider_balance``. Both are
#: read here rather than re-derived, so there is still one probe per provider.
_READY_PROBE_STATUSES = frozenset({"READY", "READY/GATEWAY", "FREE_TIER", "OBSERVED"})
_NO_CREDITS_PROBE_STATUSES = frozenset({"NO_CREDITS"})
_NETWORK_PROBE_STATUSES = frozenset({"NETWORK_BLOCKED", "UNREACHABLE"})
_NO_CREDENTIALS_PROBE_STATUSES = frozenset({"NOT_CONFIGURED"})
_NOT_CHECKED_PROBE_STATUSES = frozenset(
    {"NOT_CHECKED", "NO_PROBE", "NO_BALANCE_ENDPOINT", "UNKNOWN_ENDPOINT"}
)
_ERROR_PROBE_STATUSES = frozenset(
    {"AUTH_FAILED", "UNAUTHORIZED", "UNAVAILABLE", "ERROR", "MALFORMED"}
)

#: An HTTP status equal to 402 means the account has no credit left. It is kept
#: separate from the other 4xx codes on purpose: "no money" and "wrong key" need
#: different answers from the operator.
_HTTP_PAYMENT_REQUIRED = 402

#: Credit-exhaustion reason codes some providers publish instead of a status code.
_NO_CREDITS_ERROR_CODES = frozenset(
    {"insufficient_quota", "insufficient_credits", "billing_hard_limit_reached"}
)

#: Provider ids that name the same account under two spellings. The account health
#: monitor keys Google's account as ``gemini`` while the model registry calls the
#: provider ``google``, so a probe result must be found under either name.
PROVIDER_ID_ALIASES = {
    "google": ("gemini",),
    "gemini": ("google",),
}


class ProviderStateDecision:
    """One provider's derived state, with the reason code it rests on.

    Frozen-by-convention (``__slots__`` and no setters) so a caller cannot rewrite a
    decision after it has been made.
    """

    __slots__ = ("state", "reason", "probed")

    def __init__(self, state: ProviderState, reason: str, *, probed: bool) -> None:
        self.state = state
        self.reason = reason
        self.probed = probed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "probed": bool(self.probed),
        }

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"ProviderStateDecision({self.state.value!r}, {self.reason!r}, probed={self.probed!r})"


def _normalise_provider_names(names: Iterable[str]) -> frozenset:
    return frozenset(
        str(name).strip().lower() for name in names if str(name).strip()
    )


def lookup_probe(
    probes: Optional[Mapping[str, Mapping[str, Any]]], provider_id: str
) -> Optional[Mapping[str, Any]]:
    """The observation recorded for ``provider_id``, under its own name or an alias.

    ``probes`` is keyed by provider name as the probing layer spelled it. Reading it
    through this function is what keeps the ``google``/``gemini`` split from turning
    a real observation into ``NOT_CHECKED``.
    """

    if not probes:
        return None
    clean_id = str(provider_id).strip().lower()
    for name in (clean_id, *PROVIDER_ID_ALIASES.get(clean_id, ())):
        found = probes.get(name)
        if isinstance(found, Mapping):
            return found
    return None


def _probe_status(probe: Mapping[str, Any]) -> str:
    for key in ("status", "connection_status", "account_status"):
        value = probe.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def _probe_http_status(probe: Mapping[str, Any]) -> Optional[int]:
    for key in ("http_status", "http_code", "status_code"):
        value = probe.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _probe_error_code(probe: Mapping[str, Any]) -> str:
    for key in ("error_code", "reason_code", "quota_status"):
        value = probe.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _is_no_credits(probe: Mapping[str, Any]) -> bool:
    if _probe_http_status(probe) == _HTTP_PAYMENT_REQUIRED:
        return True
    if _probe_error_code(probe) in _NO_CREDITS_ERROR_CODES:
        return True
    return _probe_status(probe) in _NO_CREDITS_PROBE_STATUSES


def derive_provider_state(
    *,
    enabled_providers: Optional[Iterable[str]],
    provider_id: str,
    credential_env_var: str = "",
    credential_present: bool = False,
    probe: Optional[Mapping[str, Any]] = None,
) -> ProviderStateDecision:
    """Derive one provider's state. This is the only place the vocabulary is decided.

    ``enabled_providers=None`` means the composition's enabled set could not be
    read; the decision then stays at ``REGISTERED`` (membership only) instead of
    guessing that the provider is enabled or disabled.

    ``probe`` is an already-observed probe record (the account health monitor's
    status, or a balance report). ``None`` means nothing has been probed. This
    function performs no I/O, opens no socket, and reads no secret value.
    """

    if enabled_providers is None:
        return ProviderStateDecision(
            ProviderState.REGISTERED, REASON_ENABLED_SET_UNKNOWN, probed=False
        )

    enabled = _normalise_provider_names(enabled_providers)
    clean_id = str(provider_id).strip().lower()
    if clean_id not in enabled:
        return ProviderStateDecision(ProviderState.DISABLED, REASON_DISABLED, probed=False)

    credential_required = bool(str(credential_env_var).strip())
    if credential_required and not credential_present:
        return ProviderStateDecision(
            ProviderState.NO_CREDENTIALS, REASON_NO_CREDENTIALS, probed=False
        )
    if not credential_required:
        return ProviderStateDecision(
            ProviderState.READY, REASON_NO_CREDENTIAL_REQUIRED, probed=False
        )

    if probe is None:
        return ProviderStateDecision(ProviderState.NOT_CHECKED, REASON_NOT_PROBED, probed=False)

    status = _probe_status(probe)
    probed = True
    if _is_no_credits(probe):
        return ProviderStateDecision(ProviderState.NO_CREDITS, REASON_PROBE_NO_CREDITS, probed=probed)
    if status in _NETWORK_PROBE_STATUSES:
        return ProviderStateDecision(
            ProviderState.NETWORK_BLOCKED, REASON_PROBE_NETWORK_BLOCKED, probed=probed
        )
    if status in _ERROR_PROBE_STATUSES:
        return ProviderStateDecision(ProviderState.ERROR, REASON_PROBE_ERROR, probed=probed)
    if status in _NO_CREDENTIALS_PROBE_STATUSES:
        return ProviderStateDecision(
            ProviderState.NO_CREDENTIALS, REASON_NO_CREDENTIALS, probed=probed
        )
    if status in _NOT_CHECKED_PROBE_STATUSES:
        return ProviderStateDecision(
            ProviderState.NOT_CHECKED, REASON_PROBE_NOT_CHECKED, probed=False
        )
    if status in _READY_PROBE_STATUSES:
        return ProviderStateDecision(ProviderState.READY, REASON_PROBE_READY, probed=probed)
    # An answer this vocabulary cannot classify establishes nothing. It is not
    # reported as READY and not reported as an error either, because no failure was
    # observed -- only an unreadable observation.
    return ProviderStateDecision(
        ProviderState.NOT_CHECKED, REASON_PROBE_UNRECOGNISED, probed=False
    )


def provider_state_counts(decisions: Iterable[ProviderStateDecision]) -> Dict[str, int]:
    """``{"registered_total": N, "ready_total": M, "READY": ..., ...}``.

    ``registered_total`` is how many providers are in the payload at all (membership),
    and every state also appears under its own name with the number of providers
    reported in it, so a caller never has to guess what "counts" means.

    The two aggregates are named ``*_total`` on purpose: the per-state keys are upper
    case, and a JSON object carrying both ``REGISTERED`` and ``registered`` cannot be
    parsed by a case-insensitive reader (Windows PowerShell, for instance, rejects the
    duplicate). The payload therefore never contains two keys that differ only by case.
    """

    counts = {state.value: 0 for state in ProviderState}
    total = 0
    for decision in decisions:
        total += 1
        counts[decision.state.value] = counts.get(decision.state.value, 0) + 1
    counts["registered_total"] = total
    counts["ready_total"] = counts.get(ProviderState.READY.value, 0)
    return counts
