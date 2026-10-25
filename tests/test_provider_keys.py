"""«Провайдеры и ключи»: a provider added in the interface becomes ready, and no key leaks.

Why this file exists
--------------------
The owner's third complaint: *«Не могу найти, где добавить провайдера с ключами»*.
Providers came from ``.env``, which he is not supposed to edit. The new section stores
the non-secret half of a provider entry in ``.forge/settings.json`` and the key in the
operating system's credential vault.

What is proven here, offline and with an in-memory vault (never the machine's real one):

1. **add → ready.** A provider added through the routes reports ``has_key: true`` and
   ``state: "ready"``, and the *existing* secret path -- ``SecretStore``, which the
   provider layer reads -- resolves the key immediately, with no restart and no second
   registry;
2. **the key is nowhere else.** Not in ``.forge/settings.json``, not in any response
   body, not in a log record;
3. **delete → not ready.** The vault no longer holds it, ``has_key`` is false and the
   state is ``no_key``;
4. **an invalid or empty key is refused** with a plain sentence that does not echo the
   value, and nothing is stored;
5. **the settings file survives both writers**: changing the projects folder does not
   erase the provider entries, and adding a provider does not erase the folder;
6. **loopback only**: a request whose peer is not this machine is refused.

No model call, no network, no real credential.
"""

from __future__ import annotations

import json
import logging
import pathlib
import shutil
import tempfile
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from app.config.app_settings import AppSettingsStore, PROVIDERS_KEY
from app.config.provider_keys import (
    ProviderKeyError,
    ProviderKeyService,
    ProviderKeyStore,
)
from app.config.secrets import SecretStore
from app.config.windows_creds import MemoryCredentialBackend, target_for_secret

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]

#: A key that is obviously fake and long enough to pass the format rules.
FAKE_KEY = "sk-test-0000000000000000"
OTHER_KEY = "sk-other-111111111111111"


@dataclass
class _RegistryProvider:
    """The subset of ``ProviderInfo`` this section reads."""

    provider_id: str
    display_name: str
    default_secret_env: str


REGISTRY = (
    _RegistryProvider("deepseek", "DeepSeek", "DEEPSEEK_API_KEY"),
    _RegistryProvider("groq", "Groq", "GROQ_API_KEY"),
    _RegistryProvider("mock", "Offline", ""),
)


class _Case(unittest.TestCase):
    """One temporary settings file, one in-memory vault, one composed service."""

    def setUp(self) -> None:
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="forge-provider-keys-"))
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.settings_path = self.base / "settings.json"
        self.env_path = self.base / ".env"  # deliberately absent: no real .env is read
        self.settings = AppSettingsStore(path=self.settings_path)
        self.vault = MemoryCredentialBackend()
        self.service = ProviderKeyService(
            store=ProviderKeyStore(self.settings),
            secret_store=SecretStore(
                env_file=self.env_path,
                environ={},
                credential_backend=self.vault,
            ),
            credential_backend=self.vault,
            registry_providers=lambda: REGISTRY,
            enabled_providers=lambda: ("deepseek", "groq", "mock"),
        )

    # ------------------------------------------------------------------ helpers
    def settings_text(self) -> str:
        return self.settings_path.read_text(encoding="utf-8")

    def entry(self, payload: dict, provider_id: str) -> dict:
        for item in payload["providers"]:
            if item["provider_id"] == provider_id:
                return item
        self.fail(f"{provider_id} is not in {payload}")

    def add_deepseek(self, key: str = FAKE_KEY) -> dict:
        return self.service.add(
            {"preset": "deepseek", "base_url": "", "model": "", "api_key": key}
        )


class AddTests(_Case):
    def test_01_an_added_provider_is_ready_and_the_existing_secret_path_sees_the_key(
        self,
    ) -> None:
        # Before: no key anywhere, and the section says so.
        before = self.service.describe()
        self.assertFalse(self.entry(before, "deepseek")["has_key"])
        self.assertEqual(self.entry(before, "deepseek")["state"], "no_key")

        added = self.add_deepseek()
        provider = added["provider"]
        self.assertTrue(added["saved"], added)
        self.assertEqual(provider["provider_id"], "deepseek")
        self.assertTrue(provider["has_key"])
        self.assertEqual(provider["state"], "ready")
        self.assertEqual(provider["key_source"], "vault")
        self.assertEqual(provider["secret_name"], "DEEPSEEK_API_KEY")

        # THE RUNTIME IS THE SAME ONE: the provider layer resolves keys through
        # ``SecretStore``, so the key is visible there on the very next call -- no
        # restart, no second registry, no re-registration.
        runtime_store = SecretStore(
            env_file=self.env_path, environ={}, credential_backend=self.vault
        )
        self.assertEqual(runtime_store.get_secret("DEEPSEEK_API_KEY"), FAKE_KEY)
        self.assertTrue(runtime_store.has_secret("DEEPSEEK_API_KEY"))
        self.assertEqual(runtime_store.secret_source("DEEPSEEK_API_KEY"), "vault")

        # The list shows the same fact, and the vault holds it under the documented target.
        listed = self.service.describe()
        self.assertTrue(self.entry(listed, "deepseek")["has_key"])
        # `with_key` counts providers whose *key* resolves; `ready` also counts the offline
        # provider, which declares no key variable and therefore needs none.
        self.assertEqual(listed["counts"]["with_key"], 1)
        self.assertEqual(listed["counts"]["ready"], 2)
        self.assertIn(target_for_secret("DEEPSEEK_API_KEY"), self.vault._values)

    def test_02_the_key_is_never_written_to_the_settings_file(self) -> None:
        self.add_deepseek()
        text = self.settings_text()
        self.assertIn("deepseek", text)
        self.assertIn(PROVIDERS_KEY, text)
        # The two things a settings file may hold: which provider, and where it points.
        stored = json.loads(text)
        self.assertEqual(stored[PROVIDERS_KEY][0]["provider_id"], "deepseek")
        self.assertEqual(stored[PROVIDERS_KEY][0]["base_url"], "https://api.deepseek.com")
        # The two things it must never hold: the key, in any form.
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn(FAKE_KEY, json.dumps(stored))
        for forbidden in ("api_key", "apikey", "secret_value", "token"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_03_the_key_is_never_in_a_payload_or_a_log_record(self) -> None:
        records: list[str] = []

        class _Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())
                records.append(str(record.args))

        logger = logging.getLogger()
        handler = _Collect()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        added = self.add_deepseek()
        listed = self.service.describe()
        deleted = self.service.delete("deepseek")
        for payload in (added, listed, deleted, {"section": added.get("section")}):
            text = json.dumps(payload, ensure_ascii=False, default=str)
            self.assertNotIn(FAKE_KEY, text)
            # Nor its halves: a prefix is a probe, a length is a probe.
            self.assertNotIn(FAKE_KEY[:8], text)
            self.assertNotIn(str(len(FAKE_KEY)), text)
        self.assertEqual([line for line in records if FAKE_KEY in line], [])
        self.assertEqual([line for line in records if FAKE_KEY[:8] in line], [])
        # The stored entry is the only place a key's *name* appears; its value never does.
        self.assertNotIn(FAKE_KEY, self.settings_text())

    def test_04_adding_twice_updates_instead_of_duplicating(self) -> None:
        self.add_deepseek()
        self.add_deepseek(OTHER_KEY)
        stored = json.loads(self.settings_text())[PROVIDERS_KEY]
        self.assertEqual([item["provider_id"] for item in stored], ["deepseek"])
        self.assertEqual(
            SecretStore(
                env_file=self.env_path, environ={}, credential_backend=self.vault
            ).get_secret("DEEPSEEK_API_KEY"),
            OTHER_KEY,
        )

    def test_05_a_provider_the_build_does_not_know_is_reported_honestly(self) -> None:
        added = self.service.add(
            {
                "preset": "custom",
                "provider_id": "my-gateway",
                "base_url": "https://gateway.example.com/v1",
                "model": "my-model",
                "api_key": FAKE_KEY,
            }
        )
        self.assertTrue(added["provider"]["has_key"])
        self.assertFalse(added["provider"]["registered"])
        self.assertEqual(added["provider"]["state"], "unsupported")
        self.assertEqual(added["provider"]["secret_name"], "MY_GATEWAY_API_KEY")
        self.assertIn("не знает провайдера", added["warning"])

    def test_06_a_provider_outside_the_enabled_set_is_disabled_not_ready(self) -> None:
        service = ProviderKeyService(
            store=ProviderKeyStore(self.settings),
            secret_store=SecretStore(
                env_file=self.env_path, environ={}, credential_backend=self.vault
            ),
            credential_backend=self.vault,
            registry_providers=lambda: REGISTRY,
            enabled_providers=lambda: ("deepseek",),
        )
        service.add({"preset": "groq", "api_key": FAKE_KEY})
        groq = self.entry(service.describe(), "groq")
        self.assertTrue(groq["has_key"])
        self.assertEqual(groq["state"], "disabled")
        self.assertEqual(groq["state_reason"], "provider_not_enabled")

    def test_07_a_provider_that_needs_no_key_is_ready_not_keyless(self) -> None:
        """The offline provider declares no key variable, so it is never «нет ключа».

        Measured: the list said «нет ключа» beside the provider that needs no key at all,
        which reads as a missing setting the owner could never fill in.
        """

        offline = self.entry(self.service.describe(), "mock")
        self.assertFalse(offline["has_key"])
        self.assertEqual(offline["secret_name"], "")
        self.assertEqual(offline["state"], "ready")
        self.assertEqual(offline["state_reason"], "key_present")


class RefusalTests(_Case):
    def _refused(self, body: dict, field: str) -> str:
        with self.assertRaises(ProviderKeyError) as caught:
            self.service.add(body)
        message = str(caught.exception)
        self.assertEqual(caught.exception.field, field, message)
        self.assertTrue(message.strip(), "a refusal must carry a plain sentence")
        return message

    def test_01_an_empty_key_is_refused_and_nothing_is_stored(self) -> None:
        for empty in ("", "   ", "\n\t", None):
            message = self._refused(
                {"preset": "deepseek", "api_key": empty}, "api_key"
            )
            self.assertIn("Введите API-ключ", message)
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(self.vault.list_secret_names(), [])

    def test_02_a_key_that_is_not_a_key_is_refused_without_echoing_it(self) -> None:
        for bad in ("short", "has space inside", "with\x00control", "x" * 600, 7):
            message = self._refused({"preset": "deepseek", "api_key": bad}, "api_key")
            self.assertTrue(message)
            if isinstance(bad, str) and bad.strip():
                self.assertNotIn(bad[:4], message)
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(self.vault.list_secret_names(), [])

    def test_03_a_bad_url_model_or_preset_is_refused(self) -> None:
        message = self._refused(
            {"preset": "deepseek", "base_url": "not-a-url", "api_key": FAKE_KEY},
            "base_url",
        )
        self.assertIn("http", message)
        self._refused(
            {"preset": "deepseek", "base_url": "https://a b c", "api_key": FAKE_KEY},
            "base_url",
        )
        self._refused(
            {"preset": "deepseek", "model": "line\nbreak", "api_key": FAKE_KEY},
            "model",
        )
        # «Свой» without an id cannot name a provider, so it cannot store a key.
        self._refused(
            {"preset": "custom", "api_key": FAKE_KEY}, "provider_id"
        )
        # A traversal-shaped id is refused by the same rule.
        self._refused(
            {"preset": "custom", "provider_id": "../escape", "api_key": FAKE_KEY},
            "provider_id",
        )
        self.assertFalse(self.settings_path.exists())

    def test_04_a_key_named_outside_api_key_is_refused_not_silently_dropped(self) -> None:
        with self.assertRaises(ProviderKeyError) as caught:
            ProviderKeyStore(self.settings).upsert(
                {"provider_id": "deepseek", "key": FAKE_KEY}
            )
        self.assertIn("хранилище учётных данных", str(caught.exception))
        self.assertFalse(self.settings_path.exists())


class DeleteTests(_Case):
    def test_01_delete_removes_the_key_from_the_vault_and_the_entry(self) -> None:
        self.add_deepseek()
        self.assertEqual(self.vault.list_secret_names(), ["DEEPSEEK_API_KEY"])

        deleted = self.service.delete("deepseek")
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["removed_from_vault"])
        self.assertEqual(self.vault.list_secret_names(), [])
        provider = self.entry(deleted["section"], "deepseek")
        self.assertFalse(provider["has_key"])
        self.assertEqual(provider["state"], "no_key")
        # The provider layer agrees: the key no longer resolves.
        self.assertIsNone(
            SecretStore(
                env_file=self.env_path, environ={}, credential_backend=self.vault
            ).get_secret("DEEPSEEK_API_KEY")
        )
        # And the stored record is gone, so nothing claims a key that is not there.
        self.assertNotIn("deepseek", [
            item["provider_id"] for item in json.loads(self.settings_text())[PROVIDERS_KEY]
        ])

    def test_02_deleting_something_that_was_never_added_is_refused(self) -> None:
        with self.assertRaises(ProviderKeyError) as caught:
            self.service.delete("groq")
        self.assertEqual(caught.exception.code, "unknown_provider")
        self.assertIn("не добавлен", str(caught.exception))


class SettingsFileTests(_Case):
    def test_01_the_two_writers_do_not_erase_each_other(self) -> None:
        self.add_deepseek()
        # The owner now changes his projects folder -- the write that owns the same file.
        self.settings.set_project_root(str(self.base / "projects"))
        stored = json.loads(self.settings_text())
        self.assertEqual(stored["project_root"], str(self.base / "projects"))
        self.assertEqual(stored[PROVIDERS_KEY][0]["provider_id"], "deepseek")
        self.assertNotIn(FAKE_KEY, self.settings_text())

        # ... and adding a provider again keeps the folder.
        self.add_deepseek(OTHER_KEY)
        stored = json.loads(self.settings_text())
        self.assertEqual(stored["project_root"], str(self.base / "projects"))
        self.assertEqual(len(stored[PROVIDERS_KEY]), 1)


class RouteTests(_Case):
    """The HTTP surface: three routes, loopback only, no key in any body."""

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from app.api.server import create_api_app
        from app.api.service import ForgeApiService
        from app.projects.service import ProjectService
        from app.projects.store import ProjectStore
        from offline_runtime import offline_runtime

        patcher = patch("app.api.service.create_runtime", offline_runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        service = ForgeApiService()
        projects = ProjectService(ProjectStore(root=self.base / "projects"))
        self.app = create_api_app(
            service,
            project_service=projects,
            settings_store=self.settings,
            provider_key_service=self.service,
        )
        self.client = TestClient(self.app)

    def test_01_add_read_delete_through_the_routes(self) -> None:
        added = self.client.post(
            "/api/settings/providers",
            json={"preset": "deepseek", "api_key": FAKE_KEY},
        )
        self.assertEqual(added.status_code, 200, added.text)
        body = added.json()
        self.assertTrue(body["provider"]["has_key"])
        self.assertEqual(body["provider"]["state"], "ready")
        self.assertNotIn(FAKE_KEY, added.text)

        listed = self.client.get("/api/settings/providers")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn(FAKE_KEY, listed.text)
        self.assertTrue(
            [item for item in listed.json()["providers"] if item["has_key"]]
        )
        # The payload publishes booleans and fixed words only.
        for item in listed.json()["providers"]:
            self.assertIsInstance(item["has_key"], bool)
            self.assertIn(
                item["state"], ("ready", "no_key", "disabled", "unsupported")
            )

        deleted = self.client.delete("/api/settings/providers/deepseek")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(FAKE_KEY, deleted.text)
        after = self.client.get("/api/settings/providers").json()
        self.assertFalse(
            [item for item in after["providers"] if item["provider_id"] == "deepseek"][0][
                "has_key"
            ]
        )

    def test_02_a_refusal_is_plain_and_never_echoes_the_value(self) -> None:
        refused = self.client.post(
            "/api/settings/providers",
            json={"preset": "deepseek", "api_key": "   "},
        )
        self.assertEqual(refused.status_code, 400, refused.text)
        body = refused.json()["detail"]
        self.assertEqual(body["code"], "invalid_provider_entry")
        self.assertIn("Введите API-ключ", body["error"])
        self.assertEqual(body["field"], "api_key")

        unknown = self.client.delete("/api/settings/providers/groq")
        self.assertEqual(unknown.status_code, 400, unknown.text)
        self.assertEqual(unknown.json()["detail"]["code"], "unknown_provider")
        # Nothing was written by either refusal.
        self.assertFalse(self.settings_path.exists())

    def test_03_a_non_loopback_peer_is_refused(self) -> None:
        from fastapi import HTTPException

        from app.api.provider_key_routes import require_loopback

        class _Peer:
            def __init__(self, host: str) -> None:
                self.host = host

        class _Request:
            def __init__(self, host: str) -> None:
                self.client = _Peer(host) if host else None

        for host in ("127.0.0.1", "::1", "localhost", "testclient", ""):
            require_loopback(_Request(host))  # does not raise
        for host in ("10.0.0.7", "192.168.1.10", "0.0.0.0", "8.8.8.8"):
            with self.assertRaises(HTTPException) as caught:
                require_loopback(_Request(host))
            self.assertEqual(caught.exception.status_code, 403)
            self.assertEqual(
                caught.exception.detail["code"], "loopback_required", host
            )

    def test_04_the_route_table_carries_the_three_routes(self) -> None:
        paths = {
            (method, route.path)
            for route in self.app.routes
            if hasattr(route, "path")
            for method in getattr(route, "methods", ())
        }
        for expected in (
            ("GET", "/api/settings/providers"),
            ("POST", "/api/settings/providers"),
        ):
            self.assertIn(expected, paths, expected)
        self.assertIn(
            ("DELETE", "/api/settings/providers/{provider_id}"), paths
        )


if __name__ == "__main__":
    unittest.main()
