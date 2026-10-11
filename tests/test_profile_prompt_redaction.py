"""Regression tests: a profile's environment values must not reach a model prompt.

The profile is an authority artifact. ``environment_variables`` is where a deployment
declares the values a *declared command* may receive, and a declaration is admitted only
when it matches those values exactly. A profile that authorizes a deploy token therefore
holds a real credential in that mapping.

``ProjectExecutionProfile.to_dict()`` carries those values, and three call sites placed it
into a task context -- the engineering run and the task-specification path -- which every
provider appends to its prompt as JSON. A probe against a fake SDK client confirmed the
value appearing verbatim in the outbound request.

The fix routes prompt-bound serialization through ``to_context_dict()``, which keeps the
variable *names* and replaces the values, and marks the field ``repr=False`` so a repr or
traceback cannot print it either.

Everything here uses placeholder values. No real .env is read and no network call is made.
"""

import json
import unittest
from dataclasses import replace

from app.agents.providers.base import ProviderRequest
from app.agents.providers.config import ProviderConfig
from app.agents.providers.openai import OpenAIProvider
from app.config.secrets import SecretStore
from app.execution.profile import ProjectExecutionProfile
from app.tasks.specification import TaskSpecification

#: A distinguishable marker. It is not a credential and matches no credential pattern;
#: the test proves the *value* is withheld, not that a redactor recognized its shape.
SECRET_MARKER = "PLACEHOLDER-deploy-token-0123456789"


def _specification() -> TaskSpecification:
    """A minimal valid specification carrying the probe profile."""

    from app.tasks.specification import AcceptanceCriterion, Requirement

    return TaskSpecification(
        task_id="spec-probe",
        title="probe",
        description="probe the prompt path",
        requirements=(Requirement("r-1", "the probe runs"),),
        acceptance_criteria=(AcceptanceCriterion("c-1", "the probe is observable"),),
        execution_profile=_profile(),
    )


def _profile() -> ProjectExecutionProfile:
    return ProjectExecutionProfile(
        profile_id="prompt-leak-probe",
        allowed_commands=("python",),
        environment_variables={
            "DEPLOY_TOKEN": SECRET_MARKER,
            "PUBLIC_SETTING": "not-a-secret",
        },
    )


class _FakeResponses:
    """Captures the outbound payload so the test can inspect what would be sent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Usage:
            input_tokens = 1
            output_tokens = 1

        class _Response:
            output_text = "ok"
            model = "placeholder-model"
            usage = _Usage()

        return _Response()


class _FakeClient:
    """The shape `OpenAIProvider.generate` drives: `client.responses.create(...)`."""

    def __init__(self) -> None:
        self.responses = _FakeResponses()


class _FakeSdk:
    """Minimal shape the adapter's error translation understands."""

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        pass


class _Store(SecretStore):
    def get_secret(self, name: str):
        return "placeholder-api-key"


def _provider() -> tuple[OpenAIProvider, _FakeClient]:
    client = _FakeClient()
    provider = OpenAIProvider(
        ProviderConfig(provider_name="openai", model_name="placeholder-model"),
        secret_store=_Store(),
        client=client,
        sdk_module=_FakeSdk,
    )
    return provider, client


class ProfileSerializationTests(unittest.TestCase):
    def test_01_authority_view_still_carries_values(self):
        """to_dict() is the authority view and must keep working unchanged."""

        data = _profile().to_dict()
        self.assertEqual(data["environment_variables"]["DEPLOY_TOKEN"], SECRET_MARKER)

    def test_02_context_view_redacts_values_and_keeps_names(self):
        data = _profile().to_context_dict()
        self.assertEqual(
            set(data["environment_variables"]), {"DEPLOY_TOKEN", "PUBLIC_SETTING"}
        )
        self.assertNotIn(SECRET_MARKER, json.dumps(data))
        self.assertIn("<redacted>", json.dumps(data))

    def test_03_context_view_keeps_every_other_field(self):
        authority = _profile().to_dict()
        context = _profile().to_context_dict()
        self.assertEqual(
            sorted(set(context)), sorted(set(authority))
        )
        for key in set(authority) - {"environment_variables"}:
            self.assertEqual(context[key], authority[key], key)

    def test_04_repr_does_not_print_environment_values(self):
        self.assertNotIn(SECRET_MARKER, repr(_profile()))

    def test_05_a_redacted_profile_is_still_a_valid_profile(self):
        """Redaction is a serialization concern; it must not mutate the artifact."""

        profile = _profile()
        profile.to_context_dict()
        self.assertEqual(
            profile.environment_variables["DEPLOY_TOKEN"], SECRET_MARKER
        )
        self.assertTrue(profile.validate().errors == ())


class PromptPathTests(unittest.TestCase):
    """The path that actually reaches a provider."""

    def test_20_task_specification_context_does_not_carry_profile_values(self):
        specification = _specification()
        serialized = json.dumps(specification.to_context_data())
        self.assertNotIn(SECRET_MARKER, serialized)
        self.assertIn("DEPLOY_TOKEN", serialized)

    def test_21_provider_prompt_does_not_carry_profile_values(self):
        """End to end: build the context the way the engineering path does, then send it.

        This is the assertion that fails against the pre-fix implementation, because the
        prompt is assembled by the adapter and the marker would appear in
        ``client.responses.calls[0]["messages"][0]["content"]``.
        """

        specification = _specification()
        context = dict(specification.to_context_data())

        provider, client = _provider()
        provider.generate(ProviderRequest(prompt="do the thing", context=context))

        self.assertEqual(len(client.responses.calls), 1)
        outbound = client.responses.calls[0]["input"]
        self.assertNotIn(SECRET_MARKER, outbound)
        self.assertIn("DEPLOY_TOKEN", outbound)

    def test_22_the_authority_view_would_have_leaked(self):
        """Proves the test above is meaningful: the old path is demonstrably unsafe.

        If this assertion ever stops holding, the redaction test above has become
        vacuous and should be re-derived rather than trusted.
        """

        specification = _specification()
        unsafe_context = dict(specification.to_context_data())
        unsafe_context["execution_profile"] = _profile().to_dict()

        provider, client = _provider()
        provider.generate(ProviderRequest(prompt="do the thing", context=unsafe_context))
        outbound = client.responses.calls[0]["input"]
        self.assertIn(SECRET_MARKER, outbound)


class RunScopeAuthorityPayloadTests(unittest.TestCase):
    def test_30_the_scope_authority_payload_is_not_a_prompt_document(self):
        """The scope payload is hashed for authority, not sent to a model.

        It contains the authority view deliberately: the fingerprint must change when a
        declared value changes. This test documents that boundary rather than asserting
        redaction, so nobody "fixes" the fingerprint by redacting it.
        """

        import inspect

        from app.runtime.run_scope import RunScope

        source = inspect.getsource(RunScope.authority_payload)
        self.assertIn("to_dict", source)
        self.assertNotIn("to_context_dict", source)


if __name__ == "__main__":
    unittest.main()
