"""A TEST runtime whose only enabled provider is the offline mock.

Tests that build `ForgeApiService()` with no arguments get a runtime from
`create_runtime()`, which reads the real `.env`. That file is gitignored, so it is absent
in a clean checkout and present on a developer machine -- and where it is present it
enables live providers, so a test that dispatches a task can reach the network and spend
real credits. That is not hypothetical: `tests/test_desktop.py::test_04` was measured
making a real call (`Provider: openrouter (cohere/north-mini-code:free)`, 586 tokens).

This module is the one place that removes the possibility. `offline_settings()` returns
settings with `enabled_providers=("mock",)`, and `offline_runtime()` passes
`test_mode=True` so the runtime's own TEST-runtime invariant enforces the same narrowing
even if a caller hands in different settings. Every live provider is therefore disabled
by configuration, so none can be selected by automatic routing, by a fallback chain, or
by an explicit `provider_name` request.

The invariant itself lives in `app/runtime/bootstrap.py` and is tested in
`tests/test_offline_runtime_guarantee.py`; this module is the fixture that opts into it.

Nothing here reads or prints a secret value.
"""

from __future__ import annotations

from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings

#: The single provider these tests may use. It is offline by construction: its
#: `generate` returns a deterministic string and opens no socket.
OFFLINE_PROVIDER = OFFLINE_PROVIDER_NAME


def offline_settings() -> RuntimeSettings:
    """Settings in which the offline provider is the only enabled one.

    `default_provider` is the offline provider as well, so the configured default that
    automatic routing appends is itself offline.
    """

    return RuntimeSettings(
        default_provider=OFFLINE_PROVIDER,
        enabled_providers=(OFFLINE_PROVIDER,),
        provider_fallback_chain=(),
    )


def offline_runtime(*, tool_registry=None, fabric=None):
    """A runtime built with :func:`offline_settings`, as a TEST runtime.

    The keyword signature mirrors `app.runtime.bootstrap.create_runtime` so this can be
    substituted for it directly, including the `fabric` argument a service passes.
    `test_mode=True` is what makes the guarantee structural rather than a property of the
    settings this helper happens to pass.
    """

    from app.runtime.bootstrap import create_runtime

    return create_runtime(
        settings=offline_settings(),
        fabric=fabric,
        tool_registry=tool_registry,
        test_mode=True,
    )
