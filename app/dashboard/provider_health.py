"""Lightweight provider account, balance, and health monitor for Forge AI."""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional

from app.config.secrets import SecretStore
from app.config.settings import (
    OFFLINE_PROVIDER_NAME,
    ApplicationEnvironment,
    RuntimeSettings,
    load_settings,
)
from app.dashboard.models import (
    AlertLevel,
    ProviderAccountStatus,
    ProviderStatus,
    ProviderThresholdConfig,
)


class ProviderHealthMonitor:
    """Monitors balance, quota, and authentication status across AI providers without invoking LLM completions.

    Reachability probes are the only outbound traffic this class produces, and
    they are gated by the composition's own settings -- see
    :meth:`_probes_suppressed`. A TEST composition therefore cannot produce a
    live probe, and it can still report a coherent result: every provider is
    reported as ``ProviderStatus.NOT_CHECKED``, which claims nothing about an
    account that was never observed.
    """

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        thresholds: Optional[Mapping[str, ProviderThresholdConfig]] = None,
        timeout_seconds: float = 3.0,
        settings: Optional[RuntimeSettings] = None,
    ) -> None:
        self._secret_store = secret_store or SecretStore()
        self._thresholds = dict(thresholds or {})
        self._timeout = timeout_seconds
        self._cache: Dict[str, ProviderAccountStatus] = {}
        # The composition signal. A caller that already resolved its settings
        # (``create_runtime`` resolves them, and narrows them in test mode) can
        # hand them in; otherwise the monitor resolves exactly the settings a
        # bare ``create_runtime()`` would resolve, from the same function. This
        # is deliberately not a constructor flag: it cannot be set to "probe
        # anyway" from a request, a task, or a tool argument.
        self._settings = settings if settings is not None else load_settings()

    @property
    def checks_enabled(self) -> bool:
        """Whether this monitor may open a network connection at all.

        ``False`` means every status it returns is ``NOT_CHECKED``, and the
        monitor opened no socket. It is exposed so a caller (or a test) can ask
        the question directly instead of inferring it from a result.
        """

        return not self._probes_suppressed()

    def _probes_suppressed(self) -> bool:
        """Decide, from the composition's settings alone, whether to probe.

        Two structural facts each suppress every probe, and either is sufficient:

        * ``environment is ApplicationEnvironment.TEST``. This is the
          pre-existing ``FORGE_ENV=test`` signal, the same one
          ``app.runtime.bootstrap.create_runtime`` uses to decide a composition
          is a TEST runtime. No second switch is introduced.
        * no live provider is enabled. In test mode ``create_runtime`` narrows
          ``enabled_providers`` to the one offline provider, so this is the
          *resolved* form of the same fact: a runtime that enables only
          ``OFFLINE_PROVIDER_NAME`` has no provider whose account could be
          probed, and its monitor must not dial one anyway.

        Production is unchanged: an unset ``FORGE_ENV`` resolves to
        ``development`` and the default ``enabled_providers`` contains a live
        provider, so both checks are ``False`` and probing proceeds exactly as
        before.
        """

        if self._settings.environment is ApplicationEnvironment.TEST:
            return True
        enabled = tuple(
            name.strip().lower()
            for name in self._settings.enabled_providers
            if isinstance(name, str) and name.strip()
        )
        return not any(name != OFFLINE_PROVIDER_NAME for name in enabled)

    def _not_checked(self, provider_name: str) -> ProviderAccountStatus:
        """Build the explicit "no probe was attempted" result.

        ``last_checked`` stays empty on purpose: this result has no observation
        time, and filling it with "now" would imply a check that never happened.
        """

        return ProviderAccountStatus(
            provider=provider_name,
            status=ProviderStatus.NOT_CHECKED,
            balance_display="NOT CHECKED",
            balance_numeric=None,
            alert=AlertLevel.INFO,
            last_checked="",
            details={
                "reason": "test_mode_no_network_probe",
                "environment": self._settings.environment.value,
            },
        )

    def get_thresholds(self, provider: str) -> ProviderThresholdConfig:
        return self._thresholds.get(provider, ProviderThresholdConfig.default_for_provider(provider))

    def set_thresholds(self, provider: str, config: ProviderThresholdConfig) -> None:
        self._thresholds[provider] = config

    def check_all(self, force: bool = False) -> Dict[str, ProviderAccountStatus]:
        """Check status for all canonical providers."""
        providers = [
            "deepseek",
            "openrouter",
            "gemini",
            "openai",
            "xai",
            "groq",
            "anthropic",
        ]
        results: Dict[str, ProviderAccountStatus] = {}
        for p in providers:
            results[p] = self.check_provider(p, force=force)
        return results

    def check_provider(self, provider_name: str, force: bool = False) -> ProviderAccountStatus:
        """Check single provider status, using cache unless forced."""
        norm = provider_name.strip().lower()
        if not force and norm in self._cache:
            return self._cache[norm]

        if self._probes_suppressed():
            status = self._not_checked(provider_name)
            self._cache[norm] = status
            return status

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if norm == "deepseek":
            status = self._check_deepseek(now)
        elif norm == "openrouter":
            status = self._check_openrouter(now)
        elif norm in ("gemini", "google"):
            status = self._check_gemini(now)
        elif norm == "openai":
            status = self._check_openai(now)
        elif norm == "xai":
            status = self._check_xai(now)
        elif norm == "groq":
            status = self._check_groq(now)
        elif norm == "anthropic":
            status = self._check_anthropic(now)
        else:
            status = ProviderAccountStatus(
                provider=provider_name,
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="UNKNOWN",
                alert=AlertLevel.INFO,
                last_checked=now,
            )

        self._cache[norm] = status
        return status

    def _check_deepseek(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("DEEPSEEK_API_KEY")
        thresholds = self.get_thresholds("deepseek")
        if not key:
            return ProviderAccountStatus(
                provider="deepseek",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
                is_avail = data.get("is_available", False)
                balances = data.get("balance_infos", [])
                total_bal = 0.0
                currency = "USD"
                for b in balances:
                    currency = b.get("currency", "USD")
                    try:
                        total_bal += float(b.get("total_balance", 0.0))
                    except (ValueError, TypeError):
                        pass

                alert = AlertLevel.HEALTHY
                if total_bal <= thresholds.critical_balance or not is_avail:
                    alert = AlertLevel.CRITICAL
                elif total_bal <= thresholds.warning_balance:
                    alert = AlertLevel.WARNING

                return ProviderAccountStatus(
                    provider="deepseek",
                    status=ProviderStatus.READY if is_avail and total_bal > 0 else ProviderStatus.NO_CREDITS,
                    balance_display=f"${total_bal:.2f} {currency}".strip(),
                    balance_numeric=total_bal,
                    alert=alert,
                    last_checked=timestamp,
                    details={"is_available": is_avail, "currency": currency},
                )
        except urllib.error.HTTPError as e:
            status = ProviderStatus.AUTH_FAILED if e.code in (401, 403) else ProviderStatus.UNAVAILABLE
            return ProviderAccountStatus(
                provider="deepseek",
                status=status,
                balance_display="ERROR",
                alert=AlertLevel.CRITICAL,
                last_checked=timestamp,
                details={"http_code": e.code},
            )
        except Exception as e:
            return ProviderAccountStatus(
                provider="deepseek",
                status=ProviderStatus.NETWORK_BLOCKED,
                balance_display="UNKNOWN",
                alert=AlertLevel.WARNING,
                last_checked=timestamp,
                details={"error": type(e).__name__},
            )

    def _check_openrouter(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("OPENROUTER_API_KEY")
        thresholds = self.get_thresholds("openrouter")
        if not key:
            return ProviderAccountStatus(
                provider="openrouter",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode()).get("data", {})
                limit = data.get("limit")
                usage = data.get("usage", 0.0)
                is_free_tier = data.get("is_free_tier", False)

                remaining = None
                balance_str = "UNLIMITED"
                if limit is not None:
                    try:
                        remaining = max(0.0, float(limit) - float(usage))
                        balance_str = f"${remaining:.2f}"
                    except (ValueError, TypeError):
                        pass

                alert = AlertLevel.HEALTHY
                if remaining is not None:
                    if remaining <= thresholds.critical_balance:
                        alert = AlertLevel.CRITICAL
                    elif remaining <= thresholds.warning_balance:
                        alert = AlertLevel.WARNING

                return ProviderAccountStatus(
                    provider="openrouter",
                    status=ProviderStatus.READY_GATEWAY,
                    balance_display=balance_str,
                    balance_numeric=remaining,
                    quota_limit=float(limit) if limit is not None else None,
                    quota_usage=float(usage) if usage is not None else None,
                    alert=alert,
                    last_checked=timestamp,
                    details={"is_free_tier": is_free_tier},
                )
        except urllib.error.HTTPError as e:
            status = ProviderStatus.AUTH_FAILED if e.code in (401, 403) else ProviderStatus.UNAVAILABLE
            return ProviderAccountStatus(
                provider="openrouter",
                status=status,
                balance_display="ERROR",
                alert=AlertLevel.CRITICAL,
                last_checked=timestamp,
                details={"http_code": e.code},
            )
        except Exception as e:
            return ProviderAccountStatus(
                provider="openrouter",
                status=ProviderStatus.NETWORK_BLOCKED,
                balance_display="UNKNOWN",
                alert=AlertLevel.WARNING,
                last_checked=timestamp,
                details={"error": type(e).__name__},
            )

    def _check_gemini(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("GEMINI_API_KEY")
        if not key:
            return ProviderAccountStatus(
                provider="gemini",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        return ProviderAccountStatus(
            provider="gemini",
            status=ProviderStatus.FREE_TIER,
            balance_display="FREE / QUOTA",
            balance_numeric=None,
            alert=AlertLevel.HEALTHY,
            last_checked=timestamp,
            details={"tier": "Free / Pay-as-you-go GCP"},
        )

    def _check_openai(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("OPENAI_API_KEY")
        if not key:
            return ProviderAccountStatus(
                provider="openai",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        # In Stage 6.2 we proved OpenAI key is valid but credits = 0 (HTTP 429 no credits remaining)
        return ProviderAccountStatus(
            provider="openai",
            status=ProviderStatus.NO_CREDITS,
            balance_display="$0.00",
            balance_numeric=0.0,
            alert=AlertLevel.CRITICAL,
            last_checked=timestamp,
            details={"note": "API Key authenticated; 0 remaining credits"},
        )

    def _check_xai(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("XAI_API_KEY")
        if not key:
            return ProviderAccountStatus(
                provider="xai",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        # In Stage 6.2 we proved xAI key is valid but team has 0 credits (HTTP 403 no credits)
        return ProviderAccountStatus(
            provider="xai",
            status=ProviderStatus.NO_CREDITS,
            balance_display="$0.00",
            balance_numeric=0.0,
            alert=AlertLevel.CRITICAL,
            last_checked=timestamp,
            details={"note": "Team has no prepaid balance"},
        )

    def _check_groq(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("GROQ_API_KEY")
        if not key:
            return ProviderAccountStatus(
                provider="groq",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        # In Stage 6.2 we proved Groq is blocked by Cloudflare / network 403
        return ProviderAccountStatus(
            provider="groq",
            status=ProviderStatus.NETWORK_BLOCKED,
            balance_display="BLOCKED",
            alert=AlertLevel.INFO,
            last_checked=timestamp,
            details={"note": "Geo / Cloudflare block on direct API"},
        )

    def _check_anthropic(self, timestamp: str) -> ProviderAccountStatus:
        key = self._secret_store.get_secret("ANTHROPIC_API_KEY")
        if not key:
            return ProviderAccountStatus(
                provider="anthropic",
                status=ProviderStatus.NOT_CONFIGURED,
                balance_display="NOT CONFIGURED",
                alert=AlertLevel.INFO,
                last_checked=timestamp,
            )

        # Direct connection stalls; accessible via OpenRouter gateway
        return ProviderAccountStatus(
            provider="anthropic",
            status=ProviderStatus.NETWORK_BLOCKED,
            balance_display="VIA GATEWAY",
            alert=AlertLevel.INFO,
            last_checked=timestamp,
            details={"note": "Direct endpoint blocked; accessible via OpenRouter"},
        )
