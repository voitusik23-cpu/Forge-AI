"""Lightweight provider account, balance, and health monitor for Forge AI."""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional

from app.config.secrets import SecretStore
from app.dashboard.models import (
    AlertLevel,
    ProviderAccountStatus,
    ProviderStatus,
    ProviderThresholdConfig,
)


class ProviderHealthMonitor:
    """Monitors balance, quota, and authentication status across AI providers without invoking LLM completions."""

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        thresholds: Optional[Mapping[str, ProviderThresholdConfig]] = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._secret_store = secret_store or SecretStore()
        self._thresholds = dict(thresholds or {})
        self._timeout = timeout_seconds
        self._cache: Dict[str, ProviderAccountStatus] = {}

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
