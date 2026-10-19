"""Tests for the provider-state vocabulary (Part 2 of the operator brief).

The vocabulary exists so the interface can never label a provider "AVAILABLE" merely
because it is registered. Every state is derived in one function,
``app.agents.providers.provider_state.derive_provider_state``, and these tests cover each
transition offline: no network call is made, no probe is started, and no key value is
read -- the derivation is told only whether a credential is *present*.

States covered: REGISTERED, READY, NO_CREDENTIALS, NETWORK_BLOCKED, NO_CREDITS,
NOT_CHECKED, DISABLED, ERROR.
"""

from __future__ import annotations

import json
import os
import unittest

from app.agents.providers.provider_state import (
    PROVIDER_ID_ALIASES,
    PROVIDER_STATES,
    REASON_DISABLED,
    REASON_ENABLED_SET_UNKNOWN,
    REASON_NO_CREDENTIALS,
    REASON_NO_CREDENTIAL_REQUIRED,
    REASON_NOT_PROBED,
    REASON_PROBE_ERROR,
    REASON_PROBE_NETWORK_BLOCKED,
    REASON_PROBE_NO_CREDITS,
    REASON_PROBE_NOT_CHECKED,
    REASON_PROBE_READY,
    REASON_PROBE_UNRECOGNISED,
    ProviderState,
    derive_provider_state,
    lookup_probe,
    provider_state_counts,
)

ENABLED_WITH_GOOGLE = ("mock", "openrouter", "google")


def derive(**overrides):
    """Derive a state with the common Google arguments, overridable per test."""

    arguments = dict(
        enabled_providers=ENABLED_WITH_GOOGLE,
        provider_id="google",
        credential_env_var="GEMINI_API_KEY",
        credential_present=True,
        probe=None,
    )
    arguments.update(overrides)
    return derive_provider_state(**arguments)


class VocabularyTests(unittest.TestCase):
    def test_01_the_vocabulary_is_exactly_the_eight_states_in_order(self) -> None:
        self.assertEqual(
            PROVIDER_STATES,
            (
                "REGISTERED",
                "READY",
                "NO_CREDENTIALS",
                "NETWORK_BLOCKED",
                "NO_CREDITS",
                "NOT_CHECKED",
                "DISABLED",
                "ERROR",
            ),
        )
        self.assertEqual(
            {state.value for state in ProviderState}, set(PROVIDER_STATES)
        )

    def test_02_available_is_not_a_member_of_the_vocabulary(self) -> None:
        # "AVAILABLE" was the word that made a registry entry look reachable.
        self.assertNotIn("AVAILABLE", PROVIDER_STATES)

    def test_03_a_decision_is_a_state_and_a_fixed_reason_code(self) -> None:
        decision = derive()
        self.assertEqual(set(decision.to_dict()), {"state", "reason", "probed"})
        self.assertIsInstance(decision.to_dict()["probed"], bool)

    def test_04_no_reason_code_can_carry_a_key(self) -> None:
        reasons = {
            derive(probe=probe).reason
            for probe in (
                None,
                {"status": "READY"},
                {"status": "NO_CREDITS"},
                {"status": "NETWORK_BLOCKED"},
                {"status": "AUTH_FAILED"},
                {"status": "NOT_CHECKED"},
                {"status": "WHAT_IS_THIS"},
            )
        }
        for reason in reasons:
            self.assertRegex(reason, r"[a-z_]+\Z")
            self.assertNotIn("sk-", reason)
            self.assertNotIn("AIza", reason)


class RegisteredWithoutCredentialsTests(unittest.TestCase):
    def test_01_an_enabled_provider_without_its_key_is_no_credentials(self) -> None:
        decision = derive(credential_present=False)
        self.assertEqual(decision.state, ProviderState.NO_CREDENTIALS)
        self.assertEqual(decision.reason, REASON_NO_CREDENTIALS)
        self.assertFalse(decision.probed)

    def test_02_the_credential_fact_outranks_a_stale_ready_probe(self) -> None:
        decision = derive(credential_present=False, probe={"status": "READY"})
        self.assertEqual(decision.state, ProviderState.NO_CREDENTIALS)

    def test_03_a_probe_that_reports_not_configured_is_no_credentials_too(self) -> None:
        decision = derive(probe={"status": "NOT_CONFIGURED"})
        self.assertEqual(decision.state, ProviderState.NO_CREDENTIALS)


class DisabledTests(unittest.TestCase):
    def test_01_a_provider_outside_enabled_providers_is_disabled(self) -> None:
        decision = derive(enabled_providers=("mock", "openrouter"))
        self.assertEqual(decision.state, ProviderState.DISABLED)
        self.assertEqual(decision.reason, REASON_DISABLED)

    def test_02_disabled_outranks_a_missing_credential(self) -> None:
        decision = derive(enabled_providers=("mock",), credential_present=False)
        self.assertEqual(decision.state, ProviderState.DISABLED)

    def test_03_disabled_outranks_any_probe_result(self) -> None:
        decision = derive(enabled_providers=("mock",), probe={"status": "READY"})
        self.assertEqual(decision.state, ProviderState.DISABLED)

    def test_04_the_enabled_check_ignores_case_and_padding(self) -> None:
        decision = derive(enabled_providers=(" Mock ", " GOOGLE "))
        self.assertNotEqual(decision.state, ProviderState.DISABLED)


class ReadyTests(unittest.TestCase):
    def test_01_enabled_and_keyed_and_probed_ready_is_ready(self) -> None:
        decision = derive(probe={"status": "READY"})
        self.assertEqual(decision.state, ProviderState.READY)
        self.assertEqual(decision.reason, REASON_PROBE_READY)
        self.assertTrue(decision.probed)

    def test_02_the_existing_ready_vocabularies_all_map_to_ready(self) -> None:
        for status_value in ("READY", "READY/GATEWAY", "FREE_TIER", "OBSERVED"):
            decision = derive(probe={"status": status_value})
            self.assertEqual(decision.state, ProviderState.READY, status_value)

    def test_03_a_provider_that_needs_no_key_is_ready_when_enabled(self) -> None:
        # The offline mock provider declares no key variable: nothing external gates it.
        decision = derive(
            enabled_providers=("mock",),
            provider_id="mock",
            credential_env_var="",
            credential_present=False,
        )
        self.assertEqual(decision.state, ProviderState.READY)
        self.assertEqual(decision.reason, REASON_NO_CREDENTIAL_REQUIRED)

    def test_04_registration_alone_is_never_ready(self) -> None:
        # The defect this vocabulary exists to make impossible: a provider that is
        # merely listed must not read as ready.
        decision = derive(probe=None)
        self.assertNotEqual(decision.state, ProviderState.READY)


class NoCreditsTests(unittest.TestCase):
    def test_01_a_402_report_is_no_credits(self) -> None:
        decision = derive(probe={"status": "ERROR", "http_status": 402})
        self.assertEqual(decision.state, ProviderState.NO_CREDITS)
        self.assertEqual(decision.reason, REASON_PROBE_NO_CREDITS)

    def test_02_insufficient_quota_is_no_credits(self) -> None:
        decision = derive(probe={"status": "ERROR", "error_code": "insufficient_quota"})
        self.assertEqual(decision.state, ProviderState.NO_CREDITS)

    def test_03_a_no_credits_status_is_no_credits(self) -> None:
        decision = derive(probe={"status": "NO_CREDITS"})
        self.assertEqual(decision.state, ProviderState.NO_CREDITS)

    def test_04_no_credits_outranks_the_generic_error_status(self) -> None:
        decision = derive(probe={"status": "AUTH_FAILED", "http_status": "402"})
        self.assertEqual(decision.state, ProviderState.NO_CREDITS)

    def test_05_a_different_http_error_is_not_no_credits(self) -> None:
        decision = derive(probe={"status": "ERROR", "http_status": 500})
        self.assertEqual(decision.state, ProviderState.ERROR)


class ErrorAndNetworkTests(unittest.TestCase):
    def test_01_a_failed_probe_is_error(self) -> None:
        for status_value in ("AUTH_FAILED", "UNAUTHORIZED", "UNAVAILABLE", "ERROR", "MALFORMED"):
            decision = derive(probe={"status": status_value})
            self.assertEqual(decision.state, ProviderState.ERROR, status_value)
            self.assertEqual(decision.reason, REASON_PROBE_ERROR)

    def test_02_an_unreachable_probe_is_network_blocked(self) -> None:
        for status_value in ("NETWORK_BLOCKED", "UNREACHABLE"):
            decision = derive(probe={"status": status_value})
            self.assertEqual(decision.state, ProviderState.NETWORK_BLOCKED, status_value)
            self.assertEqual(decision.reason, REASON_PROBE_NETWORK_BLOCKED)

    def test_03_an_unrecognised_answer_claims_nothing(self) -> None:
        decision = derive(probe={"status": "SOMETHING_NEW"})
        self.assertEqual(decision.state, ProviderState.NOT_CHECKED)
        self.assertEqual(decision.reason, REASON_PROBE_UNRECOGNISED)
        self.assertFalse(decision.probed)


class NotCheckedTests(unittest.TestCase):
    def test_01_enabled_and_keyed_but_never_probed_is_not_checked(self) -> None:
        decision = derive(probe=None)
        self.assertEqual(decision.state, ProviderState.NOT_CHECKED)
        self.assertEqual(decision.reason, REASON_NOT_PROBED)

    def test_02_a_probe_that_itself_did_not_check_is_not_checked(self) -> None:
        decision = derive(probe={"status": "NOT_CHECKED"})
        self.assertEqual(decision.state, ProviderState.NOT_CHECKED)
        self.assertEqual(decision.reason, REASON_PROBE_NOT_CHECKED)


class RegisteredTests(unittest.TestCase):
    def test_01_an_unreadable_enabled_set_reports_membership_only(self) -> None:
        decision = derive(enabled_providers=None)
        self.assertEqual(decision.state, ProviderState.REGISTERED)
        self.assertEqual(decision.reason, REASON_ENABLED_SET_UNKNOWN)

    def test_02_registered_outranks_every_other_condition(self) -> None:
        decision = derive(
            enabled_providers=None, credential_present=False, probe={"status": "READY"}
        )
        self.assertEqual(decision.state, ProviderState.REGISTERED)


class ProbeLookupTests(unittest.TestCase):
    def test_01_a_probe_recorded_under_the_alias_spelling_is_found(self) -> None:
        self.assertEqual(lookup_probe({"gemini": {"status": "READY"}}, "google"), {"status": "READY"})
        self.assertEqual(lookup_probe({"google": {"status": "READY"}}, "gemini"), {"status": "READY"})

    def test_02_a_missing_or_malformed_record_is_absent(self) -> None:
        self.assertIsNone(lookup_probe(None, "google"))
        self.assertIsNone(lookup_probe({}, "google"))
        self.assertIsNone(lookup_probe({"google": "not a mapping"}, "google"))

    def test_03_the_alias_table_is_symmetric(self) -> None:
        for name, aliases in PROVIDER_ID_ALIASES.items():
            for alias in aliases:
                self.assertIn(name, PROVIDER_ID_ALIASES[alias])


class CountsTests(unittest.TestCase):
    def test_01_counts_separate_registered_from_ready(self) -> None:
        decisions = [
            derive(probe={"status": "READY"}),
            derive(credential_present=False),
            derive(enabled_providers=("mock",)),
        ]
        counts = provider_state_counts(decisions)
        self.assertEqual(counts["registered_total"], 3)
        self.assertEqual(counts["ready_total"], 1)
        self.assertEqual(counts["NO_CREDENTIALS"], 1)
        self.assertEqual(counts["DISABLED"], 1)


class CataloguePayloadTests(unittest.TestCase):
    """The derived state is what ``GET /api/models`` reports; no key material travels."""

    def setUp(self) -> None:
        from app.api.service import ForgeApiService

        self.service = ForgeApiService()
        self.catalogue = self.service.list_models_catalogue()

    def test_01_every_provider_reports_a_state_from_the_vocabulary(self) -> None:
        self.assertGreaterEqual(len(self.catalogue["providers"]), 1)
        for provider in self.catalogue["providers"]:
            self.assertIn(provider["state"], PROVIDER_STATES, provider["provider_id"])
            self.assertIsInstance(provider["reason"], str)
            self.assertIsInstance(provider["probed"], bool)

    def test_02_the_two_counters_are_reported_separately(self) -> None:
        counts = self.catalogue["counts"]
        self.assertEqual(counts["providers"], len(self.catalogue["providers"]))
        self.assertIn("providers_ready", counts)
        self.assertEqual(
            counts["providers_ready"], self.catalogue["provider_states"]["READY"]
        )
        self.assertEqual(self.catalogue["provider_states"]["registered_total"], counts["providers"])

    def test_03_the_vocabulary_and_its_meaning_travel_with_the_payload(self) -> None:
        note = self.catalogue["provider_state_note"]
        for state in PROVIDER_STATES:
            self.assertIn(state, note)

    def test_04_no_key_value_length_or_prefix_appears_in_the_payload(self) -> None:
        payload = json.dumps(self.catalogue, ensure_ascii=False)
        leaked = []
        for name, value in os.environ.items():
            if not isinstance(value, str) or len(value) < 8:
                continue
            if not any(
                token in name.upper()
                for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            ):
                continue
            if value in payload:
                leaked.append(name)
        self.assertEqual(leaked, [], f"secret values leaked into the catalogue: {leaked}")

    def test_05_no_provider_is_reported_available(self) -> None:
        for provider in self.catalogue["providers"]:
            self.assertNotIn("available", json.dumps(provider).lower())

    def test_06_no_two_payload_keys_differ_only_by_case(self) -> None:
        # A case-insensitive JSON reader refuses an object that carries both "REGISTERED"
        # and "registered". The two aggregates are therefore *_total, and this test is the
        # regression guard for that collision (it was measured against this payload).
        def walk(value, path: str) -> None:
            if isinstance(value, dict):
                seen = {}
                for key in value:
                    lowered = str(key).lower()
                    self.assertNotIn(
                        lowered,
                        seen,
                        f"{path} carries {seen.get(lowered)!r} and {key!r}",
                    )
                    seen[lowered] = key
                    walk(value[key], f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        walk(self.catalogue, "catalogue")


if __name__ == "__main__":
    unittest.main()
