"""Provider-level regression for "a fail-closed reply is not a model answer".

WHY THIS FILE EXISTS
====================

During the Mission 3 audit, ``AIDecisionProvider.decide`` was observed publishing
the provider identity **before** it judged the response. With an empty model reply
the provider's own observability fields still named a producer::

    provider.calls     = 1
    decision.reason_code = "empty_model_output"
    last_provider_name = "fake-gen"     <-- a fail-closed reply naming a producer
    last_model_name    = "fake-m"
    last_usage         = Usage(input_tokens=1, output_tokens=2, ...)

The workflow's evidence layer is supposed to report a producer only when a response
actually produced a project, and ``tests/test_project_factory_end_to_end.py`` pins
that separately. This file pins the **provider-level** fields, because a reader of
``AIDecisionProvider.last_provider_name`` must not be told that a model answered
when the reply it just rejected was empty.

A successful reply *does* publish the identity, and that positive control is
asserted here too: without it, the negative assertions would also pass if the
fields were simply never populated at all.

No real provider is called. ``_CountingTransport`` is an in-process fake.
"""

from __future__ import annotations

import json
import unittest

from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import DecisionRequest
from app.usage import Usage

PRODUCER_NAME = "audit-injected-producer"
PRODUCER_MODEL = "audit-injected-model"
PRODUCER_USAGE = Usage(input_tokens=31, output_tokens=37, estimated_cost=0.75)

_VALID_REPLY = json.dumps(
    {
        "decision_type": "CONTINUE",
        "action": "EXECUTE",
        "reason_code": "execution_required",
        "rationale": "proceed",
        "confidence": 0.9,
    }
)


class _CountingTransport(Provider):
    """A fake transport that returns a fixed string and counts its calls."""

    def __init__(self, output: str) -> None:
        super().__init__(
            ProviderConfig(
                provider_name=PRODUCER_NAME,
                model_name=PRODUCER_MODEL,
                enabled=True,
            )
        )
        self._output = output
        self.calls: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=self.model_name,
            output=self._output,
            usage=PRODUCER_USAGE,
        )


class FailClosedRepliesNameNoProducerTests(unittest.TestCase):
    """One ``decide`` call is one physical call, and a rejection names nobody."""

    def _decide(self, output: str):
        transport = _CountingTransport(output)
        provider = AIDecisionProvider(transport, mock_transport=True)
        decision = provider.decide(DecisionRequest(run_id="audit-run-1"))
        return transport, provider, decision

    def test_the_transport_really_was_called_exactly_once(self) -> None:
        transport, _provider, _decision = self._decide(_VALID_REPLY)
        # The premise of every assertion in this file: one decide() is one call.
        # Two calls here would mean a generation attempt spends two provider calls
        # where one is described; that is a separate, reported defect in the
        # verification dispatch, not in this method.
        self.assertEqual(len(transport.calls), 1)

    def test_an_empty_reply_is_fail_closed_with_its_own_reason(self) -> None:
        _transport, _provider, decision = self._decide("")
        self.assertEqual(decision.reason_code, "empty_model_output")
        self.assertEqual(decision.confidence, 0.0)

    def test_an_empty_reply_names_no_producer_and_no_measurement(self) -> None:
        _transport, provider, _decision = self._decide("")
        # The producer view withholds everything, because nothing produced a
        # decision. This is the question a report must ask before attributing a
        # result to a model.
        self.assertEqual(
            provider.producer_provider_name,
            "",
            "an empty model reply must not name a provider that answered",
        )
        self.assertEqual(provider.producer_model_name, "")
        self.assertIsNone(provider.producer_usage)

    def test_a_malformed_reply_names_no_producer_either(self) -> None:
        _transport, provider, decision = self._decide("{ not json")
        self.assertEqual(decision.reason_code, "malformed_json")
        self.assertEqual(provider.producer_provider_name, "")
        self.assertEqual(provider.producer_model_name, "")
        self.assertIsNone(provider.producer_usage)

    def test_a_whitespace_only_reply_is_treated_as_empty(self) -> None:
        _transport, provider, decision = self._decide("   \n\t  ")
        self.assertEqual(decision.reason_code, "empty_model_output")
        self.assertEqual(provider.producer_provider_name, "")
        self.assertIsNone(provider.producer_usage)

    def test_a_successful_reply_does_name_the_producer(self) -> None:
        """The positive control: the producer view really does report a producer
        when a usable reply arrived. Without this, the tests above would also pass
        if the fields were never set at all."""

        _transport, provider, decision = self._decide(_VALID_REPLY)
        self.assertNotEqual(decision.reason_code, "empty_model_output")
        self.assertEqual(provider.producer_provider_name, PRODUCER_NAME)
        self.assertEqual(provider.producer_model_name, PRODUCER_MODEL)
        self.assertIs(provider.producer_usage, PRODUCER_USAGE)
        # The attempt-level fields agree with it on this path.
        self.assertEqual(provider.last_provider_name, PRODUCER_NAME)
        self.assertIs(provider.last_usage, PRODUCER_USAGE)

    def test_the_attempt_fields_are_labelled_apart_from_the_producer_view(self) -> None:
        """The separation itself is asserted, so the two questions cannot be
        conflated again.

        ``last_provider_name`` deliberately names the *attempt* (so a caller can
        measure a call that produced nothing). ``producer_provider_name`` answers
        whether a model produced the decision. Here they must disagree.
        """

        _transport, provider, _decision = self._decide("")
        self.assertEqual(provider.last_provider_name, PRODUCER_NAME)
        self.assertIsNotNone(provider.last_usage)
        self.assertEqual(provider.producer_provider_name, "")
        self.assertIsNone(provider.producer_usage)
        self.assertFalse(provider.last_response_produced_a_decision)

    def test_decide_from_output_of_empty_text_clears_the_producer_view(self) -> None:
        """A caller validating empty text that *it* obtained must not read a stale
        producer identity left by an earlier call on the same provider object."""

        transport = _CountingTransport(_VALID_REPLY)
        provider = AIDecisionProvider(transport, mock_transport=True)
        provider.decide(DecisionRequest(run_id="audit-run-1"))
        self.assertEqual(provider.producer_provider_name, PRODUCER_NAME)

        decision = provider.decide_from_output(DecisionRequest(run_id="audit-run-1"), "")
        self.assertEqual(decision.reason_code, "empty_model_output")
        self.assertEqual(provider.producer_provider_name, "")
        self.assertEqual(provider.producer_model_name, "")
        self.assertIsNone(provider.producer_usage)


if __name__ == "__main__":
    unittest.main()
