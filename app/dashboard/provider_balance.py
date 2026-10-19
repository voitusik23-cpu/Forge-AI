"""Honest provider balance reporting: official endpoints only, no invented money.

Why a second, additive layer instead of extending ``ProviderAccountStatus``:

* ``tests/test_dashboard_offline_health.py`` pins the **exact** key set of
  ``ProviderAccountStatus.to_dict()`` and the historical verdicts that the non-probing providers
  report. Those are deliberate contracts and they are preserved untouched.
* the existing record carries one formatted ``balance_display`` string, which cannot express what
  the owner needs to see separately: an available balance, granted credits, topped-up credit,
  confirmed usage, a period, a currency, an observation time, a source and an age.

This module therefore adds a record of its own. Every financial value in it is either

* taken from an official, free provider endpoint and stamped with the moment it was obtained, or
* reported as **unavailable** (``None`` plus a reason), never as a zero and never as a guess.

Money is carried as **integer minor units** plus its currency and the exponent used, so no
monetary value is ever a binary float. ``1.29`` is ``129`` minor units and ``"1.29"`` display
text; nothing here recomputes or rounds a provider's figure beyond that representation.

Sources, verified against the providers' own documentation:

* DeepSeek — ``GET https://api.deepseek.com/user/balance`` (documented "Query Balance" endpoint;
  free, no completion). It returns ``is_available`` and ``balance_infos[]``, each with
  ``currency``, ``total_balance``, ``granted_balance`` and ``topped_up_balance``. The two last
  fields are distinct entities and are kept distinct here: granted credit is promotional, topped-up
  credit is money the owner paid in.
* OpenRouter — ``GET https://openrouter.ai/api/v1/auth/key`` (free key-inspection endpoint). It
  reports ``limit``, ``limit_remaining``, ``usage`` and ``is_free_tier``. It does not report
  granted credit, so that field stays ``None`` rather than being filled with the limit.
* OpenAI, Anthropic, Google Gemini, xAI and Groq — no official endpoint publishes an account
  balance. They are reported as ``unsupported`` with the reason, because a balance visible only in
  the provider's own web console cannot be read from here without harvesting a session, which is
  explicitly out of bounds.
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

from app.config.secrets import SecretStore

#: Keys of :meth:`ProviderBalanceReport.to_dict`, declared once so a test can pin them.
BALANCE_PAYLOAD_KEYS = frozenset(
    {
        "provider",
        "supported",
        "connection_status",
        "currency",
        "minor_unit_exponent",
        "available_minor",
        "available_display",
        "granted_minor",
        "granted_display",
        "topped_up_minor",
        "topped_up_display",
        "usage_minor",
        "usage_display",
        "billing_period",
        "last_updated",
        "stale",
        "age_seconds",
        "source",
        "error",
    }
)

#: Providers whose account balance is published by an official endpoint, with that endpoint.
SUPPORTED_SOURCES: Mapping[str, str] = {
    "deepseek": "GET https://api.deepseek.com/user/balance",
    "openrouter": "GET https://openrouter.ai/api/v1/auth/key",
}

#: Providers with no official balance endpoint, and the precise reason. These are reported as
#: ``supported=False`` with no monetary value at all.
UNSUPPORTED_REASONS: Mapping[str, str] = {
    "openai": (
        "OpenAI publishes no account balance through the API; the figure exists only in the "
        "provider's own billing console"
    ),
    "anthropic": (
        "Anthropic publishes no account balance through the API; credit is visible only in the "
        "provider's console"
    ),
    "gemini": (
        "Google Gemini is quota-based rather than balance-based; the free tier has no monetary "
        "balance and paid usage is billed through Google Cloud"
    ),
    "xai": (
        "xAI publishes no account balance through the API; prepaid credit is visible only in the "
        "provider's console"
    ),
    "groq": (
        "Groq publishes no account balance through the API; usage is visible only in the "
        "provider's console"
    ),
    "nvidia": (
        "NVIDIA publishes no balance or credit endpoint: the key authenticates and lists "
        "models, and credits are visible only in the NVIDIA console"
    ),
    "dashscope": (
        "Alibaba DashScope publishes no balance, usage or billing endpoint through its API. "
        "The official route to the account balance is Alibaba Cloud BssOpenApi "
        "QueryAccountBalance (business.aliyuncs.com), which requires a signed request with "
        "an Alibaba Cloud AccessKey ID and secret -- not the DashScope API key the models "
        "are called with"
    ),
    "nararouter": (
        "no endpoint is known for this provider: the configured name does not resolve, so a "
        "base URL is required before anything can be requested"
    ),
    "radox": (
        "this project establishes no account-balance endpoint for Radox AI; the provider is "
        "OpenAI-compatible for chat completions only, and a balance visible solely in its own "
        "console cannot be read from here without harvesting a session"
    ),
}

#: Free, official endpoints used only to answer "does this key work?". None of them returns a
#: balance; a 200 here means the key is valid, and nothing more is claimed.
REACHABILITY_ENDPOINTS: Mapping[str, str] = {
    "nvidia": "GET https://integrate.api.nvidia.com/v1/models",
    "dashscope": "GET https://dashscope.aliyuncs.com/api/v1/models",
}

#: Secret names for the same providers, so the check above can authenticate.
REACHABILITY_KEYS: Mapping[str, str] = {
    "nvidia": "NVIDIA_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "nararouter": "NARAROUTER_API_KEY",
}

#: Providers that publish no balance and are therefore answered without any money field.
NO_BALANCE_PROVIDERS: tuple = ("nvidia", "dashscope", "nararouter")

#: ``status`` values used by this layer. Deliberately not the historical verdict vocabulary,
#: because this record only ever describes an observation or the absence of one.
STATUS_OBSERVED = "OBSERVED"
STATUS_NOT_CHECKED = "NOT_CHECKED"
STATUS_UNSUPPORTED = "UNSUPPORTED_BY_PROVIDER"
STATUS_UNAUTHORIZED = "UNAUTHORIZED"
STATUS_UNREACHABLE = "UNREACHABLE"
STATUS_ERROR = "ERROR"
STATUS_MALFORMED = "MALFORMED"
#: The key works and the provider simply publishes no balance through its API.
STATUS_NO_BALANCE_ENDPOINT = "NO_BALANCE_ENDPOINT"
#: No endpoint is known for this provider, so nothing was attempted and nothing is claimed.
STATUS_UNKNOWN_ENDPOINT = "UNKNOWN_ENDPOINT"


def _minor(value: Any) -> Optional[int]:
    """Convert a provider's decimal string into integer minor units.

    ``None`` and unparseable input return ``None``: an absent figure is never turned into zero.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return int((parsed * 100).to_integral_value(rounding="ROUND_HALF_UP"))


def _display(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return f"{parsed.quantize(Decimal('0.01'))}"


@dataclass(frozen=True)
class ProviderBalanceReport:
    """One provider's balance observation, or the explicit absence of one."""

    provider: str
    supported: bool
    connection_status: str
    currency: str = ""
    minor_unit_exponent: int = 2
    available_minor: Optional[int] = None
    available_display: Optional[str] = None
    granted_minor: Optional[int] = None
    granted_display: Optional[str] = None
    topped_up_minor: Optional[int] = None
    topped_up_display: Optional[str] = None
    usage_minor: Optional[int] = None
    usage_display: Optional[str] = None
    billing_period: str = ""
    last_updated: str = ""
    stale: bool = False
    age_seconds: Optional[int] = None
    source: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "supported": self.supported,
            "connection_status": self.connection_status,
            "currency": self.currency,
            "minor_unit_exponent": self.minor_unit_exponent,
            "available_minor": self.available_minor,
            "available_display": self.available_display,
            "granted_minor": self.granted_minor,
            "granted_display": self.granted_display,
            "topped_up_minor": self.topped_up_minor,
            "topped_up_display": self.topped_up_display,
            "usage_minor": self.usage_minor,
            "usage_display": self.usage_display,
            "billing_period": self.billing_period,
            "last_updated": self.last_updated,
            "stale": self.stale,
            "age_seconds": self.age_seconds,
            "source": self.source,
            "error": self.error,
        }


def unsupported_report(provider: str, *, reason: Optional[str] = None) -> ProviderBalanceReport:
    """The explicit "the provider does not publish this" record."""

    norm = provider.strip().lower()
    return ProviderBalanceReport(
        provider=norm,
        supported=False,
        connection_status=STATUS_UNSUPPORTED,
        source="",
        error=reason or UNSUPPORTED_REASONS.get(
            norm, "this provider publishes no account balance through its API"
        ),
    )


def parse_deepseek_balance(payload: Mapping[str, Any], *, observed_at: str) -> ProviderBalanceReport:
    """Parse the documented DeepSeek balance response.

    ``total_balance``, ``granted_balance`` and ``topped_up_balance`` are three different things and
    are kept as three different fields. When several currencies are reported, the first entry is
    used and its currency is named, so a figure can never be read without knowing its currency.
    """

    infos = payload.get("balance_infos") or []
    entry: Mapping[str, Any] = infos[0] if infos and isinstance(infos[0], Mapping) else {}
    currency = str(entry.get("currency") or "")
    return ProviderBalanceReport(
        provider="deepseek",
        supported=True,
        connection_status=STATUS_OBSERVED,
        currency=currency,
        available_minor=_minor(entry.get("total_balance")),
        available_display=_display(entry.get("total_balance")),
        granted_minor=_minor(entry.get("granted_balance")),
        granted_display=_display(entry.get("granted_balance")),
        topped_up_minor=_minor(entry.get("topped_up_balance")),
        topped_up_display=_display(entry.get("topped_up_balance")),
        last_updated=observed_at,
        source=SUPPORTED_SOURCES["deepseek"],
        error="" if payload.get("is_available", False) else "the provider reports the account as unavailable",
    )


def parse_openrouter_key(payload: Mapping[str, Any], *, observed_at: str) -> ProviderBalanceReport:
    """Parse the documented OpenRouter key-inspection response.

    ``limit_remaining`` is the available amount; ``usage`` is what was spent against the key's
    limit. OpenRouter publishes no granted-credit entity through this endpoint, so
    ``granted_minor`` stays ``None`` -- the limit is not promoted into it.
    """

    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    limit_remaining = data.get("limit_remaining")
    usage = data.get("usage")
    limit = data.get("limit")
    if limit_remaining is None and limit is not None and usage is not None:
        remaining = _minor(limit)
        spent = _minor(usage)
        limit_remaining = None if remaining is None or spent is None else (remaining - spent) / 100
    return ProviderBalanceReport(
        provider="openrouter",
        supported=True,
        connection_status=STATUS_OBSERVED,
        currency="USD",
        available_minor=_minor(limit_remaining),
        available_display=_display(limit_remaining),
        usage_minor=_minor(usage),
        usage_display=_display(usage),
        last_updated=observed_at,
        source=SUPPORTED_SOURCES["openrouter"],
        error="" if not data.get("disabled", False) else "the provider reports this key as disabled",
    )


class ProviderBalanceProbe:
    """Fetch balance reports from official endpoints, remembering the last confirmed value.

    * it reuses :class:`ProviderHealthMonitor`'s suppression rule, so a TEST composition opens no
      socket here either (the same test-mode signal, not a second switch);
    * a failed refresh never erases what was last confirmed: the previous value is returned with
      ``stale=True`` and its age in seconds, so an old figure is never presented as current;
    * the API key is passed to the request header only. It is never stored in a report, logged, or
      included in an error message.
    """

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        *,
        timeout_seconds: float = 5.0,
        settings: Optional[Any] = None,
    ) -> None:
        self._secret_store = secret_store or SecretStore()
        self._timeout = timeout_seconds
        self._last_confirmed: Dict[str, ProviderBalanceReport] = {}
        #: This session's observations per provider, newest last. Bounded, and labelled
        #: as a session history wherever it is served: it is not a billing archive.
        self._history: Dict[str, list] = {}
        self._history_limit = 20
        from app.dashboard.provider_health import ProviderHealthMonitor

        self._monitor = ProviderHealthMonitor(
            secret_store=self._secret_store, settings=settings
        )

    @property
    def checks_enabled(self) -> bool:
        """Whether this probe may open a socket at all (same signal as the health monitor)."""

        return self._monitor.checks_enabled

    # -- internals ---------------------------------------------------------

    def _get_json(self, url: str, key: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _no_balance_report(self, provider: str) -> ProviderBalanceReport:
        """A provider that publishes no balance: verify the key, claim no money."""

        reason = UNSUPPORTED_REASONS.get(provider, "this provider publishes no account balance")
        endpoint = REACHABILITY_ENDPOINTS.get(provider, "")
        key_name = REACHABILITY_KEYS.get(provider, "")
        if endpoint == "" or key_name == "":
            # Nothing is known to ask, so nothing is asked and nothing is claimed. A provider
            # that is documented as publishing no balance keeps its established status; one
            # whose host is simply unknown is reported as an unknown endpoint.
            status = (
                STATUS_UNKNOWN_ENDPOINT
                if provider in NO_BALANCE_PROVIDERS
                else STATUS_UNSUPPORTED
            )
            return ProviderBalanceReport(
                provider=provider,
                supported=False,
                connection_status=status,
                error=reason,
            )
        key = self._secret_store.get_secret(key_name)
        if not key:
            return ProviderBalanceReport(
                provider=provider,
                supported=False,
                connection_status=STATUS_UNAUTHORIZED,
                source=endpoint,
                error=f"no API key is configured ({key_name}); {reason}",
            )
        if not self.checks_enabled:
            return ProviderBalanceReport(
                provider=provider,
                supported=False,
                connection_status=STATUS_NOT_CHECKED,
                source=endpoint,
                error=f"probes are disabled in this composition; {reason}",
            )
        try:
            self._get_json(endpoint.replace("GET ", ""), key)
        except urllib.error.HTTPError as error:
            status = STATUS_UNAUTHORIZED if error.code in (401, 403) else STATUS_ERROR
            return ProviderBalanceReport(
                provider=provider,
                supported=False,
                connection_status=status,
                source=endpoint,
                error=f"the provider answered HTTP {error.code}; {reason}",
            )
        except Exception as error:  # noqa: BLE001 - expected and reported
            return ProviderBalanceReport(
                provider=provider,
                supported=False,
                connection_status=STATUS_UNREACHABLE,
                source=endpoint,
                error=f"request failed: {type(error).__name__}; {reason}",
            )
        return ProviderBalanceReport(
            provider=provider,
            supported=False,
            connection_status=STATUS_NO_BALANCE_ENDPOINT,
            source=endpoint,
            error=reason,
        )

    def _failure(
        self, provider: str, status: str, error: str, *, now: str
    ) -> ProviderBalanceReport:
        """A failed refresh: keep the last confirmed value, marked stale and aged."""

        previous = self._last_confirmed.get(provider)
        if previous is None:
            return ProviderBalanceReport(
                provider=provider,
                supported=True,
                connection_status=status,
                source=SUPPORTED_SOURCES.get(provider, ""),
                error=error,
            )
        age: Optional[int] = None
        if previous.last_updated:
            try:
                observed = datetime.datetime.fromisoformat(previous.last_updated)
                age = max(0, int((datetime.datetime.now(datetime.timezone.utc) - observed).total_seconds()))
            except ValueError:
                age = None
        del now
        return ProviderBalanceReport(
            provider=previous.provider,
            supported=True,
            connection_status=status,
            currency=previous.currency,
            minor_unit_exponent=previous.minor_unit_exponent,
            available_minor=previous.available_minor,
            available_display=previous.available_display,
            granted_minor=previous.granted_minor,
            granted_display=previous.granted_display,
            topped_up_minor=previous.topped_up_minor,
            topped_up_display=previous.topped_up_display,
            usage_minor=previous.usage_minor,
            usage_display=previous.usage_display,
            billing_period=previous.billing_period,
            last_updated=previous.last_updated,
            stale=True,
            age_seconds=age,
            source=previous.source,
            error=error,
        )

    # -- public surface ----------------------------------------------------

    def report(self, provider: str, *, force: bool = True) -> ProviderBalanceReport:
        """One provider's balance report. Never raises for a provider-side problem."""

        norm = provider.strip().lower()
        if norm not in SUPPORTED_SOURCES:
            report = self._no_balance_report(norm)
            self._remember(report)
            return report

        key = self._secret_store.get_secret(
            {
                "deepseek": "DEEPSEEK_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
            }[norm]
        )
        if not key:
            report = ProviderBalanceReport(
                provider=norm,
                supported=True,
                connection_status=STATUS_UNAUTHORIZED,
                source=SUPPORTED_SOURCES[norm],
                error="no API key is configured for this provider, so no balance was requested",
            )
            self._remember(report)
            return report

        if not self.checks_enabled:
            # The composition forbids outbound probes (a TEST runtime). Say so; do not dial.
            previous = self._last_confirmed.get(norm)
            if previous is not None:
                report = self._failure(
                    norm,
                    STATUS_NOT_CHECKED,
                    "probes are disabled in this composition; showing the last confirmed value",
                    now="",
                )
                self._remember(report)
                return report
            report = ProviderBalanceReport(
                provider=norm,
                supported=True,
                connection_status=STATUS_NOT_CHECKED,
                source=SUPPORTED_SOURCES[norm],
                error="probes are disabled in this composition, so no balance was requested",
            )
            self._remember(report)
            return report

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            payload = self._get_json(SUPPORTED_SOURCES[norm].replace("GET ", ""), key)
        except json.JSONDecodeError:
            # The provider answered, but not with JSON. That is not a network failure, and the
            # body itself is never quoted: it could contain anything.
            report = self._failure(
                norm, STATUS_MALFORMED, "the provider's answer was not valid JSON", now=now
            )
            self._remember(report)
            return report
        except urllib.error.HTTPError as error:
            status = (
                STATUS_UNAUTHORIZED
                if error.code in (401, 403)
                else STATUS_ERROR
            )
            # Never include the response body or the key: only the status code.
            report = self._failure(
                norm, status, f"the provider answered HTTP {error.code}", now=now
            )
            self._remember(report)
            return report
        except Exception as error:  # noqa: BLE001 - network failures are expected and reported
            report = self._failure(
                norm, STATUS_UNREACHABLE, f"request failed: {type(error).__name__}", now=now
            )
            self._remember(report)
            return report

        # A JSON body of the wrong shape is a damaged answer, not an empty account: report the
        # state explicitly instead of returning a record whose amounts are all absent.
        if not isinstance(payload, Mapping):
            report = self._failure(
                norm,
                STATUS_MALFORMED,
                "the provider's answer was not a JSON object",
                now=now,
            )
            self._remember(report)
            return report
        expected = (
            ("is_available", "balance_infos")
            if norm == "deepseek"
            else ("data", "limit", "limit_remaining", "usage")
        )
        if not any(field in payload for field in expected):
            report = self._failure(
                norm,
                STATUS_MALFORMED,
                "the provider's answer carried none of the fields this endpoint documents",
                now=now,
            )
            self._remember(report)
            return report
        report = (
            parse_deepseek_balance(payload, observed_at=now)
            if norm == "deepseek"
            else parse_openrouter_key(payload, observed_at=now)
        )
        if report.error == "" and report.available_minor is None and report.usage_minor is None:
            # A well-formed answer that still carries no amount is incomplete, and saying so is
            # more useful than presenting an account whose balance is merely absent.
            report = self._failure(
                norm,
                STATUS_MALFORMED,
                "the provider's answer contained no amount",
                now=now,
            )
            self._remember(report)
            return report
        if report.error:
            # The call succeeded but the provider reports the account as unavailable/disabled.
            # Keep the figures it did return, and mark the condition explicitly.
            return report
        self._last_confirmed[norm] = report
        self._remember(report)
        return report

    def _remember(self, report: ProviderBalanceReport) -> None:
        """Record one observation attempt in the session history."""

        entries = self._history.setdefault(report.provider, [])
        entries.append(
            {
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": report.connection_status,
                "source": report.source,
                "currency": report.currency,
                "available_minor": report.available_minor,
                "granted_minor": report.granted_minor,
                "topped_up_minor": report.topped_up_minor,
                "usage_minor": report.usage_minor,
                "stale": report.stale,
                "error": report.error,
            }
        )
        del entries[: max(0, len(entries) - self._history_limit)]

    def history(self, provider: str) -> list:
        """This session's observations for one provider, newest last."""

        return list(self._history.get(provider.strip().lower(), ()))

    def reports(self, *, force: bool = True) -> Dict[str, ProviderBalanceReport]:
        """Reports for every canonical provider, supported or not."""

        order = (
            "deepseek",
            "openrouter",
            "gemini",
            "openai",
            "xai",
            "groq",
            "anthropic",
            "nvidia",
            "dashscope",
            "nararouter",
            "radox",
        )
        return {name: self.report(name, force=force) for name in order}
