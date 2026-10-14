"""Tests for the read-only model catalogue contract.

The contract exists so the interface can show real registry data without ever seeing a secret
and without mistaking a registry entry for a working model. These tests assert exactly that,
plus the shape a UI depends on.
"""

from __future__ import annotations

import json
import os
import unittest

from app.api.service import ForgeApiService


class ModelCatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ForgeApiService()
        self.catalogue = self.service.list_models_catalogue()

    def test_01_the_catalogue_comes_from_the_real_registry(self) -> None:
        self.assertEqual(self.catalogue["source"], "ModelRegistry")
        self.assertGreaterEqual(self.catalogue["counts"]["providers"], 1)
        self.assertGreaterEqual(self.catalogue["counts"]["models"], 1)

    def test_02_every_model_carries_the_fields_a_ui_needs(self) -> None:
        for model in self.catalogue["models"]:
            for field in (
                "provider_id",
                "model_id",
                "display_name",
                "context_window",
                "max_output_tokens",
                "capabilities",
                "cost_tier",
                "supports_system_prompt",
            ):
                self.assertIn(field, model, f"{model.get('model_id')} lacks {field}")
            self.assertGreater(int(model["context_window"]), 0)
            self.assertIsInstance(model["capabilities"], list)

    def test_03_a_provider_payload_carries_exactly_the_allowed_keys(self) -> None:
        # The variable *name* is not a secret and belongs here; the *value* is not. Asserting the
        # exact key set catches a payload that grows a field able to carry one.
        allowed = {
            "provider_id",
            "display_name",
            "protocol",
            "is_gateway",
            "model_count",
            "secret_env_name",
            "has_base_url",
        }
        for provider in self.catalogue["providers"]:
            self.assertEqual(set(provider), allowed, provider.get("provider_id"))

    def test_04_no_environment_secret_value_appears_anywhere_in_the_payload(self) -> None:
        payload = json.dumps(self.catalogue, ensure_ascii=False)
        leaked = []
        for name, value in os.environ.items():
            if not value or len(value) < 8:
                continue
            if not any(token in name.upper() for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                continue
            if value in payload:
                leaked.append(name)
        self.assertEqual(leaked, [], f"secret values leaked into the catalogue: {leaked}")

    def test_05_an_account_reports_configuration_but_not_its_reference(self) -> None:
        for account in self.catalogue["accounts"]:
            self.assertIn("secret_configured", account)
            self.assertIsInstance(account["secret_configured"], bool)
            self.assertNotIn("secret_ref", account)
            # Nothing that could be a reference or a value may travel with the account.
            self.assertNotIn("sk-", json.dumps(account))

    def test_06_registry_membership_is_not_reported_as_availability(self) -> None:
        note = self.catalogue["availability_note"].lower()
        self.assertIn("not availability", note)
        for model in self.catalogue["models"]:
            self.assertNotIn("available", {key.lower() for key in model})
            self.assertNotIn("ready", {key.lower() for key in model})

    def test_07_the_route_is_declared_in_the_api_source(self) -> None:
        """The route is asserted on the source, because the application object is created
        inside a closure and cannot be introspected from outside. Behaviour is covered by the
        other tests in this file; this one guards against the endpoint being dropped."""

        import inspect

        from app.api import server

        source = inspect.getsource(server)
        self.assertIn('@app.get("/api/models")', source)
        self.assertIn("list_models_catalogue", source)


if __name__ == "__main__":
    unittest.main()
