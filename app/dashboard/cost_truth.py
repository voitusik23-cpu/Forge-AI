"""Six distinguishable states for a provider's reported cost, without touching the read model.

The problem, measured: ``ProviderUsageSummary.provider_reported_cost`` is a plain number. A zero
there can mean "the provider told us it was zero", "the provider told us nothing", "we never ran
anything" or "the request failed" -- four different facts behind one value. The Costs screen had to
guess, and the earlier guess ("a zero with runs is silence") was honest but improvable.

The protected read-model contract is **not** changed: the six states are derived on demand from the
existing summaries by this module, and published by a separate read-only endpoint. Any consumer that
only knows the numeric field keeps working exactly as before.

``ProviderUsageSummary.provider_reported_cost`` remains the provider's number; this module never
turns an unknown into zero, never treats an error as zero, and never presents Forge's own estimate
as a provider-reported fact.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

#: The six states the mandate asks to distinguish.
KNOWN_ZERO = "KNOWN_ZERO"
KNOWN_NONZERO = "KNOWN_NONZERO"
UNKNOWN = "UNKNOWN"
ERROR = "ERROR"
MALFORMED = "MALFORMED"
UNSUPPORTED = "UNSUPPORTED"

ALL_STATES = (KNOWN_ZERO, KNOWN_NONZERO, UNKNOWN, ERROR, MALFORMED, UNSUPPORTED)

#: Providers whose API publishes no usage or cost figure at all, so a per-provider cost can never be
#: a provider-reported fact. The list is measured, not assumed: OpenAI, Anthropic, Gemini, xAI and
#: Groq publish no balance or usage endpoint, and the three newest entries were probed the same way
#: (NVIDIA and DashScope answer 404 on every balance/usage path, and the router has no known host).
NO_USAGE_API = frozenset(
    {"openai", "anthropic", "gemini", "xai", "groq", "nvidia", "dashscope", "nararouter"}
)

REASONS = {
    KNOWN_ZERO: "no run was recorded for this provider, so nothing was spent",
    KNOWN_NONZERO: "the provider reported a cost greater than zero",
    UNKNOWN: (
        "runs exist and the recorded provider cost is zero, but the numeric field cannot tell a "
        "reported zero from silence, and the run records carry no per-run cost basis"
    ),
    ERROR: "the run accounting for this provider could not be read",
    MALFORMED: "a run record for this provider is present but its accounting is not usable",
    UNSUPPORTED: "this provider publishes no usage or cost figure through its API",
}


def derive_cost_state(
    *,
    provider: str,
    total_runs: Optional[int],
    provider_reported_cost: Optional[float],
    estimated_cost: Optional[float] = None,
    runs_unreadable: bool = False,
    malformed_record: bool = False,
) -> str:
    """Return one of the six states for a provider's reported cost.

    Order matters: a failure to read the data is reported as a failure rather than being folded into
    "unknown", because the two call for different actions from the owner.
    """

    if malformed_record:
        return MALFORMED
    if runs_unreadable:
        return ERROR

    norm = (provider or "").strip().lower()

    if isinstance(total_runs, int) and total_runs == 0:
        # Nothing ran: a confirmed zero, which is a fact and not an absence. This holds regardless
        # of whether the provider publishes usage at all, because the absence of a run is observed
        # here rather than reported by the provider.
        return KNOWN_ZERO

    if norm in NO_USAGE_API:
        return UNSUPPORTED

    if isinstance(provider_reported_cost, (int, float)) and provider_reported_cost > 0:
        return KNOWN_NONZERO

    # A zero with runs present, and an estimate is not a provider fact, so the state stays unknown
    # rather than borrowing the estimate's authority.
    del estimated_cost
    return UNKNOWN


def cost_state_report(
    providers: Mapping[str, Mapping[str, Any]],
    *,
    runs_unreadable: bool = False,
    malformed_records: Optional[Mapping[str, bool]] = None,
) -> dict:
    """Build the read-only payload published by ``GET /api/costs/states``."""

    malformed = dict(malformed_records or {})
    entries: dict[str, dict] = {}
    for name, summary in providers.items():
        state = derive_cost_state(
            provider=name,
            total_runs=summary.get("total_runs"),
            provider_reported_cost=summary.get("provider_reported_cost"),
            estimated_cost=summary.get("estimated_cost"),
            runs_unreadable=runs_unreadable,
            malformed_record=bool(malformed.get(name, False)),
        )
        entries[name] = {
            "provider": name,
            "reported_cost_state": state,
            "reason": REASONS[state],
            "provider_reported_cost": summary.get("provider_reported_cost"),
            "estimated_cost": summary.get("estimated_cost"),
            "effective_cost": summary.get("effective_cost"),
            "total_runs": summary.get("total_runs"),
            "total_tokens": summary.get("total_tokens"),
        }
    return entries


def state_counts(entries: Mapping[str, Mapping[str, Any]]) -> dict:
    """How many providers are in each state, for a summary line in the interface."""

    counts = {state: 0 for state in ALL_STATES}
    for entry in entries.values():
        state = str(entry.get("reported_cost_state", UNKNOWN))
        counts[state] = counts.get(state, 0) + 1
    return counts
