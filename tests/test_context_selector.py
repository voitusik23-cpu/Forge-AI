"""Unit tests for ContextSelector, ContextBudgetPolicy, and SelectionReport (Stage 18 / Block 2)."""

from __future__ import annotations

import unittest

from app.agents.providers.models import CostTier, ModelCapability, ProviderModelInfo
from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSensitivity,
    ContextSourceType,
    ContextTrustLevel,
)
from app.context.selector import (
    ContextBudgetPolicy,
    ContextSelector,
    CriticalContextUnfitError,
    InsufficientContextBudgetError,
    SelectionReport,
    estimate_tokens,
)


class TestContextSelector(unittest.TestCase):
    def test_token_estimation_heuristic(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abc"), 1)
        self.assertEqual(estimate_tokens("a" * 30), 10)
        self.assertEqual(estimate_tokens("a" * 300), 100)

    def test_context_budget_policy_calculation(self) -> None:
        policy = ContextBudgetPolicy(
            total_context_window=32_768,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
        )
        # 32768 - 4096 - 2048 = 26624
        self.assertEqual(policy.input_budget_tokens, 26_624)

    def test_context_budget_policy_insufficient_physical_window_raises(self) -> None:
        # Physical window (4096) is smaller than output (4096) + headroom (2048) -> raw_budget = -2048
        policy = ContextBudgetPolicy(
            total_context_window=4_096,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
        )
        with self.assertRaises(InsufficientContextBudgetError):
            _ = policy.input_budget_tokens

    def test_context_budget_policy_below_min_input_budget_raises(self) -> None:
        # Window 8192 - output 4096 - headroom 2048 = raw 2048. Required minimum = 4096.
        policy = ContextBudgetPolicy(
            total_context_window=8_192,
            max_output_tokens=4_096,
            safety_headroom_tokens=2_048,
            min_input_budget_tokens=4_096,
        )
        with self.assertRaises(InsufficientContextBudgetError):
            _ = policy.input_budget_tokens

    def test_context_budget_policy_never_exceeds_physical_capacity(self) -> None:
        policy = ContextBudgetPolicy(
            total_context_window=8_192,
            max_output_tokens=2_048,
            safety_headroom_tokens=1_024,
            min_input_budget_tokens=1_024,
        )
        # raw_budget = 8192 - 2048 - 1024 = 5120
        self.assertEqual(policy.input_budget_tokens, 5_120)
        self.assertLessEqual(
            policy.input_budget_tokens,
            policy.total_context_window - policy.max_output_tokens - policy.safety_headroom_tokens,
        )

    def test_context_budget_policy_from_model_info(self) -> None:
        model_info = ProviderModelInfo(
            provider_id="openai",
            model_id="gpt-4o",
            display_name="GPT-4o",
            context_window=131_072,
            capabilities=frozenset({ModelCapability.CODE}),
            max_output_tokens=4_096,
            cost_tier=CostTier.PAID,
        )
        policy = ContextBudgetPolicy.from_model_info(model_info, safety_headroom_tokens=2_048)
        self.assertEqual(policy.total_context_window, 131_072)
        self.assertEqual(policy.max_output_tokens, 4_096)
        self.assertEqual(policy.safety_headroom_tokens, 2_048)
        # 131072 - 4096 - 2048 = 124928
        self.assertEqual(policy.input_budget_tokens, 124_928)
        self.assertEqual(policy.model_name, "openai:gpt-4o")

    def test_unknown_model_receives_safe_bounded_budget(self) -> None:
        policy = ContextBudgetPolicy.from_model_info(None)
        self.assertEqual(policy.total_context_window, 32_768)
        self.assertEqual(policy.max_output_tokens, 4_096)
        self.assertLess(policy.input_budget_tokens, 32_768)
        self.assertEqual(policy.input_budget_tokens, 26_624)

    def test_tier1_not_evicted_by_low_priority_history(self) -> None:
        # Create a very tight budget of 200 tokens (~600 chars)
        policy = ContextBudgetPolicy(
            total_context_window=1_000,
            max_output_tokens=500,
            safety_headroom_tokens=300,
            allow_truncation=False,
        )
        selector = ContextSelector(policy=policy)

        # Candidates: 1 requirement (Tier 1), 1 acceptance (Tier 1), 3 traces (Tier 6)
        req_item = ContextItem(
            item_id="req-1",
            item_type="requirement",
            source_type=ContextSourceType.REQUIREMENT,
            value="Must support secure transactions with high reliability.",
            trust_level=ContextTrustLevel.VERIFIED,
        )
        acc_item = ContextItem(
            item_id="acc-1",
            item_type="acceptance",
            source_type=ContextSourceType.ACCEPTANCE,
            value="Acceptance criterion: 100% tests pass under load.",
            trust_level=ContextTrustLevel.VERIFIED,
        )
        trace_item1 = ContextItem(
            item_id="trace-1",
            item_type="run_trace",
            source_type=ContextSourceType.RUN_TRACE,
            value="Verbose historical event trace log payload " * 10,
            trust_level=ContextTrustLevel.CONFIRMED,
        )
        trace_item2 = ContextItem(
            item_id="trace-2",
            item_type="run_trace",
            source_type=ContextSourceType.RUN_TRACE,
            value="Another historical trace event payload " * 10,
            trust_level=ContextTrustLevel.CONFIRMED,
        )

        candidates = [trace_item1, trace_item2, req_item, acc_item]
        selected, report = selector.select(candidates, budget_policy=policy)

        selected_ids = [it.item_id for it in selected]
        self.assertIn("req-1", selected_ids)
        self.assertIn("acc-1", selected_ids)
        # Tier 1 items must appear before any trace
        self.assertEqual(selected_ids[0], "req-1")
        self.assertEqual(selected_ids[1], "acc-1")
        self.assertTrue(report.dropped_count >= 1)
        self.assertIn("trace-2", report.dropped_item_ids)

    def test_tier1_critical_unfit_raises_fail_closed(self) -> None:
        # Budget of only 10 tokens (~30 chars)
        policy = ContextBudgetPolicy(
            total_context_window=600,
            max_output_tokens=300,
            safety_headroom_tokens=290,
        )
        selector = ContextSelector(policy=policy)

        # Large requirement that exceeds 10 tokens
        large_req = ContextItem(
            item_id="req-huge",
            item_type="requirement",
            source_type=ContextSourceType.REQUIREMENT,
            value="Critical requirement that cannot fit in a 10 token budget under any circumstances.",
        )

        with self.assertRaises(CriticalContextUnfitError):
            selector.select([large_req], budget_policy=policy)

    def test_tier1_critical_unfit_flag_when_raise_disabled(self) -> None:
        policy = ContextBudgetPolicy(
            total_context_window=600,
            max_output_tokens=300,
            safety_headroom_tokens=290,
        )
        selector = ContextSelector(policy=policy)

        large_req = ContextItem(
            item_id="req-huge",
            item_type="requirement",
            source_type=ContextSourceType.REQUIREMENT,
            value="Critical requirement that cannot fit in a 10 token budget under any circumstances.",
        )

        selected, report = selector.select([large_req], budget_policy=policy, raise_on_critical_unfit=False)
        self.assertEqual(len(selected), 0)
        self.assertTrue(report.critical_context_unfit)
        self.assertIn("req-huge", report.dropped_item_ids)

    def test_sensitivity_and_forbidden_substring_filtering(self) -> None:
        policy = ContextBudgetPolicy(total_context_window=32_768)
        selector = ContextSelector(policy=policy)

        confidential_item = ContextItem(
            item_id="item-secret",
            item_type="config",
            source_type=ContextSourceType.PROJECT_MEMORY,
            value="Internal confidential database configuration",
            sensitivity=ContextSensitivity.CONFIDENTIAL,
        )
        restricted_item = ContextItem(
            item_id="item-restricted",
            item_type="config",
            source_type=ContextSourceType.PROJECT_MEMORY,
            value="Restricted server address details",
            sensitivity=ContextSensitivity.RESTRICTED,
        )
        forbidden_val_item = ContextItem(
            item_id="item-token-leak",
            item_type="note",
            source_type=ContextSourceType.PROJECT_MEMORY,
            value="Here is the api_key for the remote system",
            sensitivity=ContextSensitivity.INTERNAL,
        )
        valid_item = ContextItem(
            item_id="item-valid",
            item_type="note",
            source_type=ContextSourceType.PROJECT_MEMORY,
            value="Architecture decision on caching layer",
            sensitivity=ContextSensitivity.INTERNAL,
        )

        selected, report = selector.select(
            [confidential_item, restricted_item, forbidden_val_item, valid_item]
        )

        selected_ids = [it.item_id for it in selected]
        self.assertEqual(selected_ids, ["item-valid"])
        self.assertEqual(report.dropped_count, 3)
        self.assertIn("security_sensitivity_exclusion:CONFIDENTIAL", report.dropped_reasons["item-secret"])
        self.assertIn("security_sensitivity_exclusion:RESTRICTED", report.dropped_reasons["item-restricted"])
        self.assertIn("security_forbidden_content:api_key", report.dropped_reasons["item-token-leak"])

    def test_truncation_of_oversized_low_priority_item(self) -> None:
        # Budget of 100 tokens (~300 chars)
        policy = ContextBudgetPolicy(
            total_context_window=600,
            max_output_tokens=300,
            safety_headroom_tokens=200,
            min_input_budget_tokens=0,
            allow_truncation=True,
            min_retained_characters=50,
        )
        selector = ContextSelector(policy=policy)

        req_item = ContextItem(
            item_id="req-1",
            item_type="requirement",
            source_type=ContextSourceType.REQUIREMENT,
            value="Deploy FastAPI backend cleanly.",
        )
        large_skill = ContextItem(
            item_id="skill-1",
            item_type="skill",
            source_type=ContextSourceType.SKILL,
            value="Comprehensive skill instruction guidelines on deployment procedures " * 15,
        )

        selected, report = selector.select([req_item, large_skill], budget_policy=policy)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].item_id, "req-1")
        self.assertEqual(selected[1].item_id, "skill-1")
        self.assertIn("[TRUNCATED DUE TO CONTEXT BUDGET]", selected[1].value)
        self.assertEqual(report.truncated_count, 1)
        self.assertIn("skill-1", report.truncated_item_ids)

    def test_deterministic_scoring_and_ordering(self) -> None:
        policy = ContextBudgetPolicy()
        selector = ContextSelector(policy=policy)

        item_a = ContextItem(
            item_id="b-task",
            item_type="task",
            source_type=ContextSourceType.TASK_SPECIFICATION,
            value="Build microservice",
            trust_level=ContextTrustLevel.VERIFIED,
        )
        item_b = ContextItem(
            item_id="a-task",
            item_type="task",
            source_type=ContextSourceType.TASK_SPECIFICATION,
            value="Build microservice",
            trust_level=ContextTrustLevel.VERIFIED,
        )

        selected1, _ = selector.select([item_a, item_b])
        selected2, _ = selector.select([item_b, item_a])

        # Ordering must be deterministic regardless of input permutation
        self.assertEqual(
            [it.item_id for it in selected1],
            [it.item_id for it in selected2],
        )
        self.assertEqual([it.item_id for it in selected1], ["a-task", "b-task"])


if __name__ == "__main__":
    unittest.main()
